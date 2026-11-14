use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Layout {
    pub root: PathBuf,
    pub upstream: PathBuf,
    pub python: PathBuf,
    pub bridge: PathBuf,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("oas-layout-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)));
            std::fs::create_dir_all(&root).unwrap();
            Self(root)
        }
        fn file(&self, path: &str) {
            let file = self.0.join(path);
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            std::fs::write(file, "fixture").unwrap();
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }

    #[test]
    fn source_fork_uses_root_python_runtime() {
        let f = Fixture::new();
        for path in ["script.py", "toolkit/python.exe", "bridge/worker.py"] { f.file(path); }
        let layout = Layout::at(&f.0).unwrap();
        assert_eq!(layout.upstream, f.0.canonicalize().unwrap());
        assert_eq!(layout.python, layout.root.join("toolkit/python.exe"));
    }

    #[test]
    fn installed_runtime_takes_precedence_over_source_tree() {
        let f = Fixture::new();
        for path in ["script.py", "toolkit/python.exe", "bridge/worker.py",
            "runtime/upstream/script.py", "runtime/upstream/toolkit/python.exe"] { f.file(path); }
        let layout = Layout::at(&f.0).unwrap();
        assert_eq!(layout.upstream, layout.root.join("runtime/upstream"));
    }
}

impl Layout {
    pub fn at(root: &Path) -> Result<Self, String> {
        let root = root.canonicalize().map_err(|e| e.to_string())?;
        let upstream = if root.join("runtime/upstream/script.py").is_file() {
            root.join("runtime/upstream")
        } else if root.join("script.py").is_file() {
            root.clone()
        } else {
            root.join("OAS")
        };
        let layout = Self {
            python: upstream.join("toolkit/python.exe"),
            bridge: root.join("bridge/worker.py"),
            upstream,
            root,
        };
        for file in [&layout.python, &layout.bridge, &layout.upstream.join("script.py")] {
            if !file.is_file() {
                return Err(format!("缺少运行文件：{}", file.display()));
            }
        }
        Ok(layout)
    }

    pub fn discover() -> Result<Self, String> {
        if let Some(root) = std::env::var_os("OAS_DESKTOP_ROOT") {
            return Self::at(Path::new(&root));
        }
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        for root in exe.parent().into_iter().flat_map(Path::ancestors) {
            if let Ok(layout) = Self::at(root) {
                return Ok(layout);
            }
        }
        Self::at(&std::env::current_dir().map_err(|e| e.to_string())?)
    }
}
