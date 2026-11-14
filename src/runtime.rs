use crate::layout::Layout;
use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver, Sender},
    thread,
    time::{Duration, Instant},
};

pub fn hide(command: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    command
}

// The OS owns cleanup even if the GUI crashes. Never kill unrelated Python processes.
#[cfg(windows)]
struct Job(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl Job {
    fn attach(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() { return Err(std::io::Error::last_os_error().to_string()); }
            let job = Self(handle);
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
                &info as *const _ as _, std::mem::size_of_val(&info) as u32) == 0
                || AssignProcessToJobObject(handle, child.as_raw_handle() as _) == 0 {
                return Err(std::io::Error::last_os_error().to_string());
            }
            Ok(job)
        }
    }
}
#[cfg(windows)]
impl Drop for Job {
    fn drop(&mut self) { unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0); } }
}

struct Bridge {
    child: Child,
    input: Option<ChildStdin>,
    #[cfg(windows)]
    _job: Job,
}
impl Bridge {
    fn spawn(layout: &Layout, tx: Sender<Value>) -> Result<Self, String> {
        let mut command = Command::new(&layout.python);
        hide(&mut command).arg("-u").arg(&layout.bridge).arg("--root").arg(&layout.upstream)
            .current_dir(&layout.upstream).env("PYTHONIOENCODING", "utf-8").env("PYTHONUTF8", "1")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|e| e.to_string())?;
        #[cfg(windows)]
        let job = match Job::attach(&child) {
            Ok(job) => job,
            Err(e) => { let _ = child.kill(); let _ = child.wait(); return Err(e); }
        };
        let input = child.stdin.take();
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let out = tx.clone();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(line) => {
                        let value = serde_json::from_str(&line).unwrap_or_else(|_| json!({"event":"log","text":line}));
                        if out.send(value).is_err() { break; }
                    }
                    Err(_) => break,
                }
            }
        });
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if tx.send(json!({"event":"log","text":line})).is_err() { break; }
            }
        });
        Ok(Self { child, input, #[cfg(windows)] _job: job })
    }
    fn send(&mut self, value: &Value) -> Result<(), String> {
        let input = self.input.as_mut().ok_or("运行环境已关闭")?;
        writeln!(input, "{value}").and_then(|_| input.flush()).map_err(|e| e.to_string())
    }
}
impl Drop for Bridge {
    fn drop(&mut self) {
        let _ = self.send(&json!({"command":"shutdown"}));
        self.input.take();
        let end = Instant::now() + Duration::from_secs(5);
        while Instant::now() < end {
            if matches!(self.child.try_wait(), Ok(Some(_))) { return; }
            thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub enum Work { Request(Value), Sync, Restart, Quit }
pub struct Worker {
    pub tx: Sender<Work>,
    pub events: Receiver<Value>,
    thread: Option<thread::JoinHandle<()>>,
}
impl Worker {
    pub fn new(layout: Layout) -> Self {
        let (tx, rx) = mpsc::channel();
        let (events_tx, events) = mpsc::channel();
        let thread = thread::spawn(move || {
            let start = |tx: &Sender<Value>| match Bridge::spawn(&layout, tx.clone()) {
                Ok(bridge) => Some(bridge),
                Err(error) => { let _ = tx.send(json!({"event":"fatal","error":error})); None }
            };
            let mut bridge = start(&events_tx);
            loop {
                let work = match rx.recv_timeout(Duration::from_millis(200)) {
                    Ok(work) => work,
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        if let Some(b) = bridge.as_mut() {
                            if let Ok(Some(code)) = b.child.try_wait() {
                                let _ = events_tx.send(json!({"event":"fatal","error":format!("运行环境退出：{code}")}));
                                bridge.take();
                            }
                        }
                        continue;
                    }
                    Err(_) => break,
                };
                match work {
                    Work::Quit => break,
                    Work::Restart => { bridge.take(); bridge = start(&events_tx); }
                    Work::Request(value) => {
                        let result = bridge.as_mut().ok_or("运行环境未连接".to_string()).and_then(|b| b.send(&value));
                        if let Err(error) = result { let _ = events_tx.send(json!({"event":"error","error":error})); }
                    }
                    Work::Sync => {
                        bridge.take();
                        let result = crate::upstream::sync(&layout);
                        let _ = events_tx.send(match result {
                            Ok(data) => json!({"event":"updated","data":data}),
                            Err(error) => json!({"event":"error","error":error}),
                        });
                        bridge = start(&events_tx);
                    }
                }
            }
        });
        Self { tx, events, thread: Some(thread) }
    }
    pub fn request(&self, value: Value) { let _ = self.tx.send(Work::Request(value)); }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.tx.send(Work::Quit);
        if let Some(thread) = self.thread.take() { let _ = thread.join(); }
    }
}
