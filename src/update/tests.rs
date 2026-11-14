use super::*;
use std::{cell::Cell, path::PathBuf, sync::atomic::{AtomicU64, Ordering}};

static NEXT: AtomicU64 = AtomicU64::new(0);
struct Fixture { directory: PathBuf, seed: PathBuf, layout: Layout, source: String }

impl Fixture {
    fn run(dir: &Path, args: &[&str]) -> String {
        let output = hide(&mut Command::new("git")).args(args).current_dir(dir).output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        String::from_utf8_lossy(&output.stdout).trim().into()
    }
    fn commit(dir: &Path) {
        Self::run(dir, &["add", "."]);
        Self::run(dir, &["-c", "user.name=Gangan test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"]);
    }
    fn new() -> Self {
        let directory = std::env::temp_dir().join(format!("gangan-update-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)));
        let seed = directory.join("seed");
        fs::create_dir_all(&seed).unwrap();
        Self::run(&seed, &["init", "-b", "main"]);
        for (name, content) in [("requirements.txt", "base\n"), ("script.py", "original\n"),
            ("tasks/existing.py", "original task\n"), ("bridge/worker.py", "original bridge\n"),
            ("src/main.rs", "fn main() {}\n"), (".gitignore", "runtime/\nconfig/\n.build-tools/\n*.exe\n")] {
            let path = seed.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, content).unwrap();
        }
        Self::commit(&seed);
        Self::run(&directory, &["clone", seed.to_str().unwrap(), "app"]);
        let root = directory.join("app");
        let upstream = root.join("runtime/upstream");
        for name in ["script.py", "tasks/existing.py"] {
            let target = upstream.join(name);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::copy(seed.join(name), target).unwrap();
        }
        for (name, content) in [("config/account.json", "private config"), ("log/old.txt", "private log"),
            ("toolkit/python.exe", "python fixture"), (".git/config", "old OAS origin fixture")] {
            let target = upstream.join(name);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(target, content).unwrap();
        }
        let source = Self::run(&root, &["remote", "get-url", "origin"]);
        let layout = Layout { root, upstream, python: PathBuf::new(), bridge: PathBuf::new() };
        Self { directory, seed, layout, source }
    }
    fn advance(&self, files: &[(&str, &[u8])]) {
        for (name, data) in files {
            let path = self.seed.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, data).unwrap();
        }
        Self::commit(&self.seed);
    }
    fn update(&self) -> Result<Value, String> { sync_with(&self.layout, &self.source, |_| Ok(())) }
}

impl Drop for Fixture {
    fn drop(&mut self) { let _ = fs::remove_dir_all(&self.directory); }
}

#[test]
fn updates_app_bridge_and_runtime_core_preserving_user_data_and_old_git() {
    let f = Fixture::new();
    f.advance(&[("script.py", b"updated script\n"), ("src/main.rs", b"fn main() { /* update */ }\n"),
        ("bridge/worker.py", b"updated bridge\n"), ("assets/new.png", &[0, 255, 13, 10, 42])]);
    let built = Cell::new(false);
    let result = sync_with(&f.layout, &f.source, |layout| {
        assert_eq!(fs::read_to_string(layout.root.join("src/main.rs")).unwrap().trim(), "fn main() { /* update */ }");
        assert_eq!(fs::read(layout.upstream.join("script.py")).unwrap(), b"original\n");
        built.set(true);
        Ok(())
    }).unwrap();
    assert!(built.get());
    assert_eq!(result["source"], f.source);
    assert_eq!(result["branch"], "main");
    assert_eq!(result["restart_required"], true);
    assert_ne!(result["before"], result["after"]);
    assert_eq!(fs::read_to_string(f.layout.root.join("bridge/worker.py")).unwrap().trim(), "updated bridge");
    assert_eq!(fs::read(f.layout.upstream.join("script.py")).unwrap(), b"updated script\n");
    assert_eq!(fs::read(f.layout.upstream.join("assets/new.png")).unwrap(), [0, 255, 13, 10, 42]);
    for (name, data) in [("config/account.json", "private config"), ("log/old.txt", "private log"),
        ("toolkit/python.exe", "python fixture"), (".git/config", "old OAS origin fixture")] {
        assert_eq!(fs::read_to_string(f.layout.upstream.join(name)).unwrap(), data);
    }
    let record: Value = serde_json::from_str(&fs::read_to_string(f.layout.root.join("runtime/update-history.jsonl")).unwrap()).unwrap();
    assert_eq!(record, result);
    assert!(git(&f.layout, &["status", "--porcelain"]).unwrap().is_empty());
}

#[test]
fn source_edits_are_never_overwritten() {
    let f = Fixture::new();
    fs::write(f.layout.root.join("script.py"), "local edit").unwrap();
    f.advance(&[("script.py", b"remote change\n")]);
    assert!(f.update().unwrap_err().contains("本地修改"));
    assert_eq!(fs::read_to_string(f.layout.root.join("script.py")).unwrap(), "local edit");
}

#[test]
fn runtime_edits_cancel_update_before_changing_source() {
    let f = Fixture::new();
    let before = revision(&f.layout);
    fs::write(f.layout.upstream.join("script.py"), "runtime local edit").unwrap();
    f.advance(&[("script.py", b"remote change\n")]);
    assert!(f.update().unwrap_err().contains("任务文件存在本地修改"));
    assert_eq!(revision(&f.layout), before);
    assert_eq!(fs::read_to_string(f.layout.upstream.join("script.py")).unwrap(), "runtime local edit");
}

#[test]
fn python_dependency_changes_require_adaptation() {
    let f = Fixture::new();
    let before = revision(&f.layout);
    f.advance(&[("requirements.txt", b"new dependency\n")]);
    assert!(f.update().unwrap_err().contains("依赖"));
    assert_eq!(revision(&f.layout), before);
}

#[test]
fn local_commits_are_never_rebased_or_reset() {
    let f = Fixture::new();
    fs::write(f.layout.root.join("local.txt"), "local commit").unwrap();
    Fixture::commit(&f.layout.root);
    let before = revision(&f.layout);
    f.advance(&[("script.py", b"remote change\n")]);
    assert!(f.update().unwrap_err().contains("本地独有"));
    assert_eq!(revision(&f.layout), before);
}

#[test]
fn wrong_remote_is_rejected_before_fetch() {
    let f = Fixture::new();
    Fixture::run(&f.layout.root, &["remote", "set-url", "origin", "https://gitcode.com/OnmyojiAutoScript/OnmyojiAutoScript"]);
    assert!(f.update().unwrap_err().contains("更新来源不匹配"));
}

#[test]
fn non_main_branch_is_not_updated() {
    let f = Fixture::new();
    Fixture::run(&f.layout.root, &["checkout", "-b", "work"]);
    assert!(f.update().unwrap_err().contains("不是 main"));
}

#[test]
fn failed_build_keeps_runtime_and_retry_finishes_pending_task_update() {
    let f = Fixture::new();
    let before = revision(&f.layout);
    f.advance(&[("script.py", b"updated script\n")]);
    let failure = sync_with(&f.layout, &f.source, |_| Err("build fixture failed".into()));
    assert!(failure.unwrap_err().contains("build fixture failed"));
    assert_ne!(revision(&f.layout), before);
    assert_eq!(fs::read(f.layout.upstream.join("script.py")).unwrap(), b"original\n");
    let result = f.update().unwrap();
    assert_eq!(result["before"], result["after"]);
    assert_eq!(fs::read(f.layout.upstream.join("script.py")).unwrap(), b"updated script\n");
}

#[test]
fn deletes_managed_task_files_without_deleting_user_files() {
    let f = Fixture::new();
    fs::remove_file(f.seed.join("tasks/existing.py")).unwrap();
    Fixture::commit(&f.seed);
    f.update().unwrap();
    assert!(!f.layout.upstream.join("tasks/existing.py").exists());
    assert!(f.layout.upstream.join("config/account.json").is_file());
}

#[test]
fn source_layout_updates_without_a_second_runtime_checkout() {
    let mut f = Fixture::new();
    f.layout.upstream = f.layout.root.clone();
    f.advance(&[("script.py", b"updated source layout\n")]);
    f.update().unwrap();
    assert_eq!(fs::read_to_string(f.layout.root.join("script.py")).unwrap().trim(), "updated source layout");
}

#[test]
fn latest_version_does_not_rebuild_or_request_restart() {
    let f = Fixture::new();
    fs::write(f.layout.root.join("GanganMouse.exe"), "installed fixture").unwrap();
    let result = sync_with(&f.layout, &f.source, |_| panic!("Latest version must not rebuild")).unwrap();
    assert_eq!(result["before"], result["after"]);
    assert_eq!(result["restart_required"], false);
    assert_eq!(fs::read_to_string(f.layout.root.join("GanganMouse.exe")).unwrap(), "installed fixture");
}

#[cfg(windows)]
#[test]
fn failed_native_build_keeps_executable_and_records_full_diagnostics() {
    let f = Fixture::new();
    fs::create_dir_all(f.layout.root.join("scripts")).unwrap();
    fs::write(f.layout.root.join("scripts/build.ps1"),
        "Write-Output 'compiler stdout detail'\n[Console]::Error.WriteLine('compiler stderr detail')\nexit 17\n").unwrap();
    fs::write(f.layout.root.join("GanganMouse.exe"), b"installed executable").unwrap();
    let error = build(&f.layout).unwrap_err();
    assert!(error.contains("update-build.log"));
    let log = fs::read_to_string(f.layout.root.join("runtime/update-build.log")).unwrap();
    assert!(log.contains("compiler stdout detail"));
    assert!(log.contains("compiler stderr detail"));
    assert!(log.contains("17"));
    assert_eq!(fs::read(f.layout.root.join("GanganMouse.exe")).unwrap(), b"installed executable");
}

#[cfg(windows)]
#[test]
fn windows_powershell_build_bootstrap_accepts_canonical_verbatim_paths() {
    let mut f = Fixture::new();
    fs::create_dir_all(f.layout.root.join("scripts")).unwrap();
    let bootstrap = include_str!("../../scripts/build.ps1").split("Push-Location $projectRoot").next().unwrap();
    fs::write(f.layout.root.join("scripts/build.ps1"), format!(
        "{bootstrap}\nWrite-Output (Join-Path $projectRoot 'runtime')\n")).unwrap();
    f.layout.root = f.layout.root.canonicalize().unwrap();
    assert!(f.layout.root.to_string_lossy().starts_with(r"\\?\"));
    build(&f.layout).unwrap();
    let log = fs::read_to_string(f.layout.root.join("runtime/update-build.log")).unwrap();
    assert!(log.contains("runtime"));
    assert!(!log.contains("Join-Path :"));
}
