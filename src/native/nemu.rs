//! MuMu SDK capture, preserving the Python implementation's RGBA row order.
use std::{collections::HashMap, path::Path};
type Connect=unsafe extern "C" fn(*const u16,i32)->i32;
type Disconnect=unsafe extern "C" fn(i32)->i32;
type Capture=unsafe extern "C" fn(i32,i32,i32,*mut i32,*mut i32,*mut u8)->i32;

struct Connection {
    _library:libloading::Library,
    disconnect:Disconnect,
    capture:Capture,
    id:i32,
}
impl Drop for Connection {
    fn drop(&mut self) {unsafe { (self.disconnect)(self.id); }}
}

#[derive(Default)]
pub struct Nemu {
    connections:HashMap<(String,i32),Connection>,
}
impl Nemu {
    pub fn capture(&mut self,folder:&str,instance:i32,display:i32)->Result<(usize,usize,Vec<u8>),String> {
        let key=(folder.to_owned(),instance);
        if !self.connections.contains_key(&key) {
            let paths=["shell/sdk/external_renderer_ipc.dll","nx_device/12.0/shell/sdk/external_renderer_ipc.dll",
                "nx_device/15.0/shell/sdk/external_renderer_ipc.dll"].into_iter()
                .map(|part|Path::new(folder).join(part));
            unsafe {
                let mut loaded=None;
                let mut errors=Vec::new();
                for path in paths.filter(|p|p.is_file()) {
                    match libloading::Library::new(&path) {
                        Ok(library)=>{loaded=Some(library);break;}
                        Err(error)=>errors.push(format!("{}: {error}",path.display())),
                    }
                }
                let library=loaded.ok_or_else(||format!("MuMu SDK unavailable: {}",errors.join("; ")))?;
                let connect:Connect=*library.get(b"nemu_connect\0").map_err(|e|e.to_string())?;
                let disconnect:Disconnect=*library.get(b"nemu_disconnect\0").map_err(|e|e.to_string())?;
                let capture:Capture=*library.get(b"nemu_capture_display\0").map_err(|e|e.to_string())?;
                let folder:Vec<u16>=folder.encode_utf16().chain(Some(0)).collect();
                let id=connect(folder.as_ptr(),instance);
                if id==0 {return Err("MuMu SDK connection failed".into());}
                self.connections.insert(key.clone(),Connection {_library:library,disconnect,capture,id});
            }
        }
        let connection=&self.connections[&key];
        let (mut w,mut h)=(0,0);
        let status=unsafe {(connection.capture)(connection.id,display,0,&mut w,&mut h,std::ptr::null_mut())};
        let len=(w as usize).checked_mul(h as usize).and_then(|n|n.checked_mul(4))
            .filter(|n|*n>0 && *n<=64*1024*1024);
        if status>0 || w<=0 || h<=0 || len.is_none() {
            self.connections.remove(&key);
            return Err(format!("MuMu SDK resolution failed: {status}"));
        }
        let mut bytes=vec![0;len.unwrap()];
        let (width,height)=(w,h);
        let status=unsafe {(connection.capture)(connection.id,display,bytes.len() as i32,&mut w,&mut h,bytes.as_mut_ptr())};
        if status>0 || (w,h)!=(width,height) {
            self.connections.remove(&key);
            return Err(format!("MuMu SDK screenshot failed: {status}"));
        }
        Ok((w as usize,h as usize,bytes))
    }
}
