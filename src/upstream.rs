use crate::{layout::Layout, runtime::hide};
use serde_json::{json, Value};
use std::{fs::{self, OpenOptions}, io::Write, process::Command};

fn git(layout: &Layout, args: &[&str]) -> Result<String, String> {
    let bundled = layout.upstream.join("toolkit/Git/mingw64/bin/git.exe");
    let mut command = Command::new(if bundled.is_file() { bundled } else { "git".into() });
    let output = hide(&mut command).args(args).current_dir(&layout.upstream)
        .env("GIT_TERMINAL_PROMPT", "0").output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(format!("git {}：{}", args.join(" "), String::from_utf8_lossy(&output.stderr)));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

pub fn sync(layout: &Layout) -> Result<Value, String> {
    if !git(layout, &["status", "--porcelain"])?.is_empty() {
        return Err("上游目录存在本地修改，已保留修改并取消更新。".into());
    }
    let branch = git(layout, &["symbolic-ref", "--short", "HEAD"])?;
    if branch != "master" { return Err(format!("当前上游分支为 {branch}，仅自动更新 master。")); }
    let before = git(layout, &["rev-parse", "HEAD"])?;
    git(layout, &["fetch", "origin", "--prune"])?;
    let after = git(layout, &["rev-parse", "origin/master"])?;
    if !git(layout, &["rev-list", "origin/master..HEAD"])?.is_empty() {
        return Err("存在本地独有提交，已取消自动合并。".into());
    }
    for path in ["requirements.txt", "requirements-in.txt"] {
        if !git(layout, &["diff", &before, &after, "--", path])?.is_empty() {
            return Err("上游依赖清单发生变化，需要先适配运行环境；本次未修改当前版本。".into());
        }
    }
    let changed = git(layout, &["diff", "--name-only", &before, &after])?;
    let record = json!({"before":before,"after":after,"files":changed.lines().collect::<Vec<_>>()});
    let state = layout.root.join("runtime");
    fs::create_dir_all(&state).map_err(|e| e.to_string())?;
    // Ensure the audit destination is writable before touching the checkout.
    let mut history = OpenOptions::new().create(true).append(true).open(state.join("sync-history.jsonl")).map_err(|e| e.to_string())?;
    git(layout, &["merge", "--ff-only", "origin/master"])?;
    writeln!(history, "{record}").map_err(|e| format!("更新已完成，但记录失败：{e}"))?;
    Ok(record)
}

pub fn revision(layout: &Layout) -> String {
    git(layout, &["rev-parse", "--short", "HEAD"]).unwrap_or_else(|_| "未知".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{path::PathBuf, sync::atomic::{AtomicU64, Ordering}};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture { root: PathBuf, seed: PathBuf, layout: Layout }
    impl Fixture {
        fn run(dir: &std::path::Path, args: &[&str]) {
            let output = hide(&mut Command::new("git")).args(args).current_dir(dir).output().unwrap();
            assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        }
        fn commit(dir: &std::path::Path) {
            Self::run(dir, &["add", "."]);
            Self::run(dir, &["-c", "user.name=OAS test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"]);
        }
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("oas-sync-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)));
            let seed = root.join("seed");
            fs::create_dir_all(&seed).unwrap();
            Self::run(&seed, &["init", "-b", "master"]);
            fs::write(seed.join("requirements.txt"), "base\n").unwrap();
            fs::write(seed.join("script.py"), "original\n").unwrap();
            fs::write(seed.join(".gitignore"), "config/\n").unwrap();
            Self::commit(&seed);
            Self::run(&root, &["clone", seed.to_str().unwrap(), "upstream"]);
            let layout = Layout { root: root.clone(), upstream: root.join("upstream"), python: PathBuf::new(), bridge: PathBuf::new() };
            Self { root, seed, layout }
        }
        fn advance(&self, file: &str) {
            fs::write(self.seed.join(file), "upstream change\n").unwrap();
            Self::commit(&self.seed);
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.root); }
    }
    #[test]
    fn fast_forward_preserves_user_config_and_records_changes() {
        let f = Fixture::new();
        fs::create_dir(f.layout.upstream.join("config")).unwrap();
        let config = f.layout.upstream.join("config/account.json");
        fs::write(&config, "private fixture").unwrap();
        f.advance("script.py");
        let result = sync(&f.layout).unwrap();
        assert_ne!(result["before"], result["after"]);
        assert_eq!(fs::read_to_string(config).unwrap(), "private fixture");
        assert!(f.root.join("runtime/sync-history.jsonl").is_file());
    }
    #[test]
    fn local_edits_are_never_overwritten() {
        let f = Fixture::new();
        fs::write(f.layout.upstream.join("script.py"), "local edit").unwrap();
        f.advance("script.py");
        assert!(sync(&f.layout).is_err());
        assert_eq!(fs::read_to_string(f.layout.upstream.join("script.py")).unwrap(), "local edit");
    }
    #[test]
    fn dependency_changes_require_adaptation() {
        let f = Fixture::new();
        let before = revision(&f.layout);
        f.advance("requirements.txt");
        assert!(sync(&f.layout).unwrap_err().contains("依赖"));
        assert_eq!(revision(&f.layout), before);
    }
    #[test]
    fn local_commits_are_never_rebased_or_reset() {
        let f = Fixture::new();
        fs::write(f.layout.upstream.join("local.txt"), "local commit").unwrap();
        Fixture::commit(&f.layout.upstream);
        let before = revision(&f.layout);
        f.advance("script.py");
        assert!(sync(&f.layout).unwrap_err().contains("本地独有"));
        assert_eq!(revision(&f.layout), before);
    }
}
