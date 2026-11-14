#[cfg(windows)]
pub struct Instance(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl Instance {
    pub fn acquire(root: &std::path::Path) -> Result<Self, String> {
        use std::hash::{Hash, Hasher};
        use windows_sys::Win32::{Foundation::*, System::Threading::CreateMutexW};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        root.to_string_lossy().to_lowercase().hash(&mut hasher);
        let name: Vec<u16> = format!("Local\\OASDesktop-{:x}\0", hasher.finish()).encode_utf16().collect();
        unsafe {
            let handle = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
            if handle.is_null() { return Err(std::io::Error::last_os_error().to_string()); }
            if GetLastError() == ERROR_ALREADY_EXISTS {
                CloseHandle(handle);
                return Err("此目录的 OAS 已经运行，请使用现有窗口。".into());
            }
            Ok(Self(handle))
        }
    }
}
#[cfg(windows)]
impl Drop for Instance {
    fn drop(&mut self) { unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0); } }
}
