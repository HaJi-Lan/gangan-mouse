use crate::{layout::Layout, runtime::hide};
use serde_json::{json, Value};
use std::{fs::{self, OpenOptions}, io::Write, path::Path, process::Command};

pub const SOURCE: &str = "https://github.com/HaJi-Lan/gangan-mouse.git";
const BRANCH: &str = "main";

fn git_bytes(layout: &Layout, args: &[&str]) -> Result<Vec<u8>, String> {
    let bundled = layout.python.parent().unwrap_or(&layout.upstream).join("Git/mingw64/bin/git.exe");
    let mut command = Command::new(if bundled.is_file() { bundled } else { "git".into() });
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

fn build(layout: &Layout) -> Result<(), String> {
    let output = hide(&mut Command::new("powershell.exe"))
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(layout.root.join("scripts/build.ps1")).arg("-Release")
        .current_dir(&layout.root).output().map_err(|e| format!("无法启动构建：{e}"))?;
    if !output.status.success() {
        return Err(format!("源码已更新，但程序构建失败；修复后可再次点击更新。\n{}\n{}",
            String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr)));
    }
    Ok(())
}

pub fn sync(layout: &Layout) -> Result<Value, String> {
    sync_with(layout, SOURCE, build)
}

fn sync_with(layout: &Layout, source: &str, build: impl FnOnce(&Layout) -> Result<(), String>) -> Result<Value, String> {
    let top = git(layout, &["rev-parse", "--show-toplevel"])?;
    if Path::new(&top).canonicalize().map_err(|e| e.to_string())? != layout.root.canonicalize().map_err(|e| e.to_string())? {
        return Err("安装目录不是肝肝鼠源码仓库，无法自动更新。".into());
    }
    if !same_source(&git(layout, &["remote", "get-url", "origin"])?, source) {
        return Err(format!("更新来源不匹配，需要肝肝鼠仓库：{source}"));
    }
    if !git(layout, &["status", "--porcelain"])?.is_empty() {
        return Err("肝肝鼠目录存在本地修改，已保留修改并取消更新。".into());
    }
    if git(layout, &["symbolic-ref", "--short", "HEAD"])? != BRANCH {
        return Err("当前不是 main 分支，已取消自动更新。".into());
    }
    let before = git(layout, &["rev-parse", "HEAD"])?;
    git(layout, &["fetch", "origin", BRANCH])?;
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
    let files = git_bytes(layout, &["diff", "--name-only", "-z", &before, &after])?;
    let files: Vec<_> = files.split(|b| *b == 0).filter(|name| !name.is_empty()).map(|name| String::from_utf8_lossy(name).to_string()).collect();
    fs::create_dir_all(&state).map_err(|e| e.to_string())?;
    let mut history = OpenOptions::new().create(true).append(true).open(state.join("update-history.jsonl")).map_err(|e| e.to_string())?;
    // Retain the installed task version even if the following build fails, so retry can finish it.
    fs::write(&marker, &baseline).map_err(|e| e.to_string())?;
    git(layout, &["merge", "--ff-only", &after])?;
    if rebuild { build(layout)?; }
    install_core(layout, &changes)?;
    fs::write(&marker, &after).map_err(|e| format!("更新已完成，但任务版本记录失败：{e}"))?;
    let record = json!({"source":source,"branch":BRANCH,"before":before,"after":after,
        "files":files,"restart_required":rebuild});
    writeln!(history, "{record}").map_err(|e| format!("更新已完成，但记录失败：{e}"))?;
    Ok(record)
}

pub fn revision(layout: &Layout) -> String {
    git(layout, &["rev-parse", "--short", "HEAD"]).unwrap_or_else(|_| "未知".into())
}

#[cfg(test)]
mod tests;
