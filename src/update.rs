use crate::{layout::Layout, runtime::hide};
use serde_json::{json, Value};
use std::{fs::{self, OpenOptions}, io::Write, path::Path, process::Command};

pub const SOURCE: &str = "https://github.com/HaJi-Lan/gangan-mouse.git";
const BRANCH: &str = "main";
mod source;
pub use source::Source;

fn git_bytes(layout: &Layout, args: &[&str]) -> Result<Vec<u8>, String> {
    let git_root = layout.python.parent().unwrap_or(&layout.upstream).join("Git");
    let bundled = [git_root.join("cmd/git.exe"), git_root.join("mingw64/bin/git.exe")]
        .into_iter().find(|path| path.is_file()).unwrap_or_else(|| "git".into());
    let mut command = Command::new(bundled);
    let output = hide(&mut command).args(args).current_dir(&layout.root)
        .env("GIT_TERMINAL_PROMPT", "0").env("GCM_INTERACTIVE", "never")
        .output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(format!("git {}：{}", args.join(" "), String::from_utf8_lossy(&output.stderr)));
    }
    Ok(output.stdout)
}

fn git(layout: &Layout, args: &[&str]) -> Result<String, String> {
    String::from_utf8(git_bytes(layout, args)?).map(|s| s.trim().to_string()).map_err(|e| e.to_string())
}

fn same_source(left: &str, right: &str) -> bool {
    left.trim_end_matches('/').trim_end_matches(".git") == right.trim_end_matches('/').trim_end_matches(".git")
}

fn core_file(name: &str) -> bool {
    let safe = Path::new(name).components().all(|part| matches!(part, std::path::Component::Normal(_)));
    safe && (name == "script.py" || ["module/", "tasks/", "assets/", "bin/", "deploy/"].iter().any(|p| name.starts_with(p)))
}

fn blob(layout: &Layout, revision: &str, name: &str) -> Result<Option<Vec<u8>>, String> {
    let entry = git(layout, &["ls-tree", revision, "--", name])?;
    if entry.is_empty() { return Ok(None); }
    if !entry.starts_with("100644 ") && !entry.starts_with("100755 ") {
        return Err(format!("任务文件类型不受支持：{name}"));
    }
    git_bytes(layout, &["show", &format!("{revision}:{name}")]).map(Some)
}

fn same_content(left: &[u8], right: &[u8]) -> bool {
    if left == right { return true; }
    match (std::str::from_utf8(left), std::str::from_utf8(right)) {
        (Ok(a), Ok(b)) if !a.contains('\0') && !b.contains('\0') => a.replace("\r\n", "\n") == b.replace("\r\n", "\n"),
        _ => false,
    }
}

struct CoreChange {
    name: String,
    previous: Option<Vec<u8>>,
    next: Option<Vec<u8>>,
}

fn runtime_changes(layout: &Layout, baseline: &str, after: &str) -> Result<Vec<CoreChange>, String> {
    if layout.root == layout.upstream { return Ok(vec![]); }
    let names = git_bytes(layout, &["diff", "--no-renames", "--name-only", "-z", baseline, after])?;
    let mut changes = vec![];
    for name in names.split(|b| *b == 0).filter(|name| !name.is_empty()) {
        let name = std::str::from_utf8(name).map_err(|e| e.to_string())?;
        if !core_file(name) { continue; }
        let before = blob(layout, baseline, name)?;
        let next = blob(layout, after, name)?;
        let target = layout.upstream.join(name);
        let previous = match fs::read(&target) {
            Ok(data) => Some(data),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(format!("无法读取任务文件 {name}：{e}")),
        };
        let matches = |expected: &Option<Vec<u8>>| match (&previous, expected) {
            (Some(a), Some(b)) => same_content(a, b),
            (None, None) => true,
            _ => false,
        };
        if !matches(&before) && !matches(&next) {
            return Err(format!("任务文件存在本地修改，已取消更新：{name}"));
        }
        changes.push(CoreChange { name: name.into(), previous, next });
    }
    Ok(changes)
}

fn write_core(root: &Path, name: &str, data: &Option<Vec<u8>>) -> Result<(), String> {
    let target = root.join(name);
    match data {
        Some(bytes) => {
            fs::create_dir_all(target.parent().unwrap()).map_err(|e| e.to_string())?;
            fs::write(&target, bytes).map_err(|e| format!("无法写入任务文件 {name}：{e}"))
        }
        None if target.exists() => fs::remove_file(&target).map_err(|e| format!("无法移除任务文件 {name}：{e}")),
        None => Ok(()),
    }
}

fn install_core(layout: &Layout, changes: &[CoreChange]) -> Result<(), String> {
    for (index, change) in changes.iter().enumerate() {
        if let Err(error) = write_core(&layout.upstream, &change.name, &change.next) {
            let mut failures = vec![];
            for applied in changes[..=index].iter().rev() {
                if let Err(e) = write_core(&layout.upstream, &applied.name, &applied.previous) { failures.push(e); }
            }
            return Err(if failures.is_empty() { error } else { format!("{error}；恢复任务文件失败：{}", failures.join("；")) });
        }
    }
    Ok(())
}

fn prepare_binary(layout: &Layout, commit: &str, downloads: &str) -> Result<Option<std::path::PathBuf>, String> {
    // Keep the downloader paired with the running EXE, including installations
    // whose old source checkout does not yet contain the new update script.
    let installer = layout.root.join("runtime/update-installer.py");
    fs::create_dir_all(installer.parent().unwrap()).map_err(|e| e.to_string())?;
    fs::write(&installer, include_str!("../scripts/install_prebuilt.py")).map_err(|e| e.to_string())?;
    let output = hide(&mut Command::new(&layout.python))
        .arg(installer)
        .arg("--root").arg(&layout.root).args(["--commit", commit, "--downloads", downloads, "--stage-only"])
        .env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8")
        .current_dir(&layout.root).output().map_err(|e| format!("无法启动程序下载：{e}"))?;
    let log = layout.root.join("runtime/update-download.log");
    let details = format!("exit: {}\n{}\n{}", output.status,
        String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    fs::create_dir_all(log.parent().unwrap()).map_err(|e| e.to_string())?;
    fs::write(&log, &details).map_err(|e| format!("无法保存更新日志：{e}"))?;
    if !output.status.success() {
        return Err(format!("预编译程序下载失败，当前版本已保留。\n日志：{}\n{}", log.display(), String::from_utf8_lossy(&output.stderr)));
    }
    let data: Value = serde_json::from_slice(&output.stdout).map_err(|e| format!("程序下载结果无效：{e}"))?;
    let path = layout.root.join("runtime/prebuilt").join(format!("GanganMouse-{commit}-Windows-x64.exe"));
    if data["commit"] != commit || !path.is_file() { return Err("下载程序的版本记录不匹配".into()); }
    Ok(Some(path))
}

fn install_binary(layout: &Layout, staged: &Path) -> Result<(), String> {
    let directory = layout.root.join("runtime/previous-executables");
    fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
    let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let pending = directory.join(format!("{}-{unique}.pending.exe", std::process::id()));
    let previous = directory.join(format!("{}-{unique}.exe", std::process::id()));
    fs::copy(staged, &pending).map_err(|e| format!("无法准备新程序：{e}"))?;
    let target = layout.root.join("GanganMouse.exe");
    let existed = target.exists();
    let result = (|| {
        if existed { fs::rename(&target, &previous).map_err(|e| format!("无法保留当前程序：{e}"))?; }
        if let Err(error) = fs::rename(&pending, &target) {
            if existed { fs::rename(&previous, &target).map_err(|e| format!("无法恢复当前程序：{e}"))?; }
            return Err(format!("无法替换程序，当前版本已恢复：{error}"));
        }
        Ok(())
    })();
    if pending.exists() { let _ = fs::remove_file(pending); }
    if result.is_ok() {
        let mut old: Vec<_> = fs::read_dir(&directory).into_iter().flatten().filter_map(Result::ok)
            .map(|entry| entry.path()).filter(|path| path.is_file() && path.extension().is_some_and(|e| e == "exe") && *path != previous).collect();
        old.sort_by_key(|path| std::cmp::Reverse(fs::metadata(path).and_then(|m| m.modified()).ok()));
        for path in old.into_iter().skip(if existed { 1 } else { 2 }) { let _ = fs::remove_file(path); }
    }
    result
}

pub fn sync(layout: &Layout) -> Result<Value, String> {
    let settings = Source::load(&layout.root)?;
    sync_with(layout, SOURCE, &settings, prepare_binary)
}

fn sync_with(layout: &Layout, identity: &str, source: &Source,
    prepare: impl FnOnce(&Layout, &str, &str) -> Result<Option<std::path::PathBuf>, String>) -> Result<Value, String> {
    let top = git(layout, &["rev-parse", "--show-toplevel"])?;
    if Path::new(&top).canonicalize().map_err(|e| e.to_string())? != layout.root.canonicalize().map_err(|e| e.to_string())? {
        return Err("安装目录不是肝肝鼠源码仓库，无法自动更新。".into());
    }
    let origin = git(layout, &["remote", "get-url", "origin"])?;
    if !same_source(&origin, identity) && !same_source(&origin, &source.repository) {
        return Err("更新来源不匹配，需要本项目仓库或已配置的源码更新源".into());
    }
    if !git(layout, &["status", "--porcelain"])?.is_empty() {
        return Err("肝肝鼠目录存在本地修改，已保留修改并取消更新。".into());
    }
    let branch = git(layout, &["symbolic-ref", "--short", "HEAD"])?;
    if branch != BRANCH && branch != source.branch {
        return Err("当前不是 main 或已配置更新分支，已取消自动更新。".into());
    }
    let before = git(layout, &["rev-parse", "HEAD"])?;
    git(layout, &["fetch", "--", &source.repository, &source.branch])?;
    let after = git(layout, &["rev-parse", "FETCH_HEAD"])?;
    if !git(layout, &["rev-list", &format!("{after}..HEAD")])?.is_empty() {
        return Err("存在本地独有提交，已取消自动合并。".into());
    }
    let state = layout.root.join("runtime");
    let marker = state.join("gangan-core-revision");
    let baseline = match fs::read_to_string(&marker) {
        Ok(value) => value.trim().to_string(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => before.clone(),
        Err(e) => return Err(e.to_string()),
    };
    if ![40, 64].contains(&baseline.len()) || !baseline.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("任务版本记录无效，已取消更新。".into());
    }
    for base in [&before, &baseline] {
        for path in ["requirements.txt", "requirements-in.txt"] {
            if !git(layout, &["diff", base, &after, "--", path])?.is_empty() {
                return Err("Python 依赖清单发生变化，需要先适配运行环境；本次未修改当前版本。".into());
            }
        }
    }
    let changes = runtime_changes(layout, &baseline, &after)?;
    let rebuild = before != after || baseline != after || !layout.root.join("GanganMouse.exe").is_file();
    // Download and verify the exact source commit before advancing the working tree.
    let binary = if rebuild { prepare(layout, &after, &source.downloads)? } else { None };
    let files = git_bytes(layout, &["diff", "--name-only", "-z", &before, &after])?;
    let files: Vec<_> = files.split(|b| *b == 0).filter(|name| !name.is_empty()).map(|name| String::from_utf8_lossy(name).to_string()).collect();
    fs::create_dir_all(&state).map_err(|e| e.to_string())?;
    let mut history = OpenOptions::new().create(true).append(true).open(state.join("update-history.jsonl")).map_err(|e| e.to_string())?;
    // Keep the installed version marker until both runtime and binary are installed.
    fs::write(&marker, &baseline).map_err(|e| e.to_string())?;
    git(layout, &["merge", "--ff-only", &after])?;
    install_core(layout, &changes)?;
    if let Some(binary) = binary {
        if let Err(error) = install_binary(layout, &binary) {
            for change in changes.iter().rev() { write_core(&layout.upstream, &change.name, &change.previous)?; }
            return Err(error);
        }
    }
    fs::write(&marker, &after).map_err(|e| format!("更新已完成，但任务版本记录失败：{e}"))?;
    let record = json!({"source":source.repository,"branch":source.branch,"downloads":source.downloads,"before":before,"after":after,
        "files":files,"restart_required":rebuild});
    writeln!(history, "{record}").map_err(|e| format!("更新已完成，但记录失败：{e}"))?;
    Ok(record)
}

pub fn revision(layout: &Layout) -> String {
    git(layout, &["rev-parse", "--short", "HEAD"]).unwrap_or_else(|_| "未知".into())
}

#[cfg(test)]
mod tests;
