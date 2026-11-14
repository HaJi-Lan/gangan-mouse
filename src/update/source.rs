use super::*;

pub const DOWNLOADS: &str = "https://github.com/HaJi-Lan/gangan-mouse/releases/download/desktop-builds/";

#[derive(Clone)]
pub struct Source {
    pub repository: String,
    pub branch: String,
    pub downloads: String,
}

impl Default for Source {
    fn default() -> Self {
        Self { repository: SOURCE.into(), branch: "main".into(), downloads: DOWNLOADS.into() }
    }
}

fn http_url(value: &str) -> bool {
    let tail = value.strip_prefix("https://").or_else(|| value.strip_prefix("http://"));
    tail.is_some_and(|tail| !tail.split('/').next().unwrap_or("").is_empty()
        && !tail.contains('@') && !value.chars().any(char::is_whitespace)
        && !value.contains(['\\', '\0', '?', '#']))
}

impl Source {
    pub fn validate(&self) -> Result<(), String> {
        if !http_url(&self.repository) { return Err("源码更新源需要填写完整的 HTTP 或 HTTPS 仓库地址".into()); }
        if !http_url(&self.downloads) { return Err("程序下载源需要填写完整的 HTTP 或 HTTPS 地址前缀".into()); }
        if self.branch.is_empty() || self.branch.starts_with('-') || self.branch.contains("..")
            || self.branch.contains("@{") || self.branch.chars().any(|c| c.is_whitespace() || c.is_control() || "~^:?*[\\".contains(c)) {
            return Err("更新分支名称无效".into());
        }
        Ok(())
    }
    pub fn load(root: &Path) -> Result<Self, String> {
        let data = match fs::read(root.join("runtime/update-source.json")) {
            Ok(data) => data,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Self::default()),
            Err(e) => return Err(format!("无法读取更新源配置：{e}")),
        };
        let value: Value = serde_json::from_slice(&data).map_err(|e| format!("更新源配置无效：{e}"))?;
        let field = |name| value[name].as_str().map(str::to_string).ok_or_else(|| format!("更新源缺少字段：{name}"));
        let source = Self { repository: field("repository")?, branch: field("branch")?, downloads: field("downloads")? };
        source.validate()?;
        Ok(source)
    }
    pub fn save(&mut self, root: &Path) -> Result<(), String> {
        self.repository = self.repository.trim().into();
        self.branch = self.branch.trim().into();
        self.downloads = format!("{}/", self.downloads.trim().trim_end_matches('/'));
        self.validate()?;
        let directory = root.join("runtime");
        fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
        let pending = directory.join("update-source.pending.json");
        fs::write(&pending, serde_json::to_vec_pretty(&json!({"repository":self.repository,"branch":self.branch,"downloads":self.downloads})).unwrap()).map_err(|e| e.to_string())?;
        fs::rename(pending, directory.join("update-source.json")).map_err(|e| e.to_string())
    }
}
