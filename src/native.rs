//! Device/vision worker. JSON headers followed by exact-length image/tensor buffers.
use serde_json::{json, Value};
use std::io::{BufRead, Read, Write};
mod ocr;
mod nemu;
mod ctc;
mod vision;
mod preprocess;

#[cfg(windows)]
fn protocol_output() -> Result<Box<dyn Write>,String> {
    use std::os::windows::io::FromRawHandle;
    use windows_sys::Win32::{Foundation::*,System::{Threading::GetCurrentProcess,Console::*}};
    unsafe {
        let mut handle=std::ptr::null_mut();
        if DuplicateHandle(GetCurrentProcess(),GetStdHandle(STD_OUTPUT_HANDLE),GetCurrentProcess(),
            &mut handle,0,0,DUPLICATE_SAME_ACCESS)==0 {return Err(std::io::Error::last_os_error().to_string());}
        // SDKs print diagnostics from C. Keep those off the binary protocol pipe.
        SetStdHandle(STD_OUTPUT_HANDLE,GetStdHandle(STD_ERROR_HANDLE));
        for crt in ["msvcrt.dll","ucrtbase.dll"] {
            if let Ok(lib)=libloading::Library::new(crt) {
                if let Ok(dup)=lib.get::<unsafe extern "C" fn(i32,i32)->i32>(b"_dup2\0") {dup(2,1);}
            }
        }
        Ok(Box::new(std::fs::File::from_raw_handle(handle)))
    }
}
#[cfg(not(windows))]
fn protocol_output()->Result<Box<dyn Write>,String> {Ok(Box::new(std::io::stdout()))}

fn number(v: &Value, key: &str) -> Result<usize, String> {
    v[key].as_u64().and_then(|n| usize::try_from(n).ok()).ok_or_else(|| format!("invalid {key}"))
}
fn image_len(w: usize, h: usize) -> Result<usize, String> {
    w.checked_mul(h).and_then(|n| n.checked_mul(3)).filter(|n| *n > 0 && *n <= 64*1024*1024)
        .ok_or_else(|| "invalid image dimensions".into())
}

pub fn serve() -> Result<(), String> {
    let mut input = std::io::stdin().lock();
    let mut output = protocol_output()?;
    let mut line = String::new();
    let mut ocr = ocr::OcrEngine::default();
    let mut nemu = nemu::Nemu::default();
    let mut matcher = vision::Matcher::default();
    loop {
        line.clear();
        if input.read_line(&mut line).map_err(|e| e.to_string())? == 0 { return Ok(()); }
        let request: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
        let len = number(&request, "bytes")?;
        if len > 64*1024*1024 { return Err("request too large".into()); }
        let mut data = vec![0; len];
        input.read_exact(&mut data).map_err(|e| e.to_string())?;
        let (mut reply, bytes) = match execute(&request, &data, &mut ocr, &mut nemu, &mut matcher) {
            Ok((reply, bytes)) => (reply, bytes),
            Err(error) => (json!({"ok":false,"error":error}), Vec::new()),
        };
        reply["bytes"] = json!(bytes.len());
        writeln!(output, "{reply}").and_then(|_| output.write_all(&bytes))
            .and_then(|_| output.flush()).map_err(|e| e.to_string())?;
    }
}

fn execute(r: &Value, data: &[u8], ocr: &mut ocr::OcrEngine, nemu: &mut nemu::Nemu, matcher: &mut vision::Matcher) -> Result<(Value, Vec<u8>), String> {
    match r["command"].as_str().unwrap_or("") {
        "ping" => Ok((json!({"ok":true,"protocol":1}), vec![])),
        "ctc_decode" => Ok((json!({"ok":true,"results":ctc::decode(r,data)?}),vec![])),
        "ocr_filter_boxes" => {
            let boxes=preprocess::filter_boxes(data,number(r,"width")?,number(r,"height")?)?;
            Ok((json!({"ok":true,"count":boxes.len()/8}),boxes.iter().flat_map(|v|v.to_le_bytes()).collect()))
        }
        "ocr_normalize" => {
            let (w,h,pw)=(number(r,"width")?,number(r,"height")?,number(r,"padded_width")?);
            let frame=vision::Frame::new(data,w,h,3)?;
            let values=preprocess::normalize(frame,pw,r["kind"].as_str().unwrap_or(""))?;
            Ok((json!({"ok":true,"shape":[3,h,pw]}),values.iter().flat_map(|v|v.to_le_bytes()).collect()))
        }
        "ocr_image_infer" => {
            let model=r["model"].as_str().ok_or("missing OCR model path")?;
            let kind=r["kind"].as_str().ok_or("missing OCR normalization kind")?;
            let widths=r["widths"].as_array().filter(|v|!v.is_empty()).ok_or("missing OCR image widths")?;
            let (h,pw)=(number(r,"height")?,number(r,"padded_width")?);
            let count=widths.len().checked_mul(3).and_then(|n|n.checked_mul(h)).and_then(|n|n.checked_mul(pw))
                .filter(|n|*n>0 && *n<=16_777_216).ok_or("invalid OCR image batch size")?;
            let mut tensor=Vec::with_capacity(count);
            let mut offset=0_usize;
            for width in widths {
                let w=width.as_u64().and_then(|v|usize::try_from(v).ok()).ok_or("invalid OCR image width")?;
                let end=image_len(w,h)?.checked_add(offset).filter(|end|*end<=data.len()).ok_or("OCR image batch payload mismatch")?;
                tensor.extend(preprocess::normalize(vision::Frame::new(&data[offset..end],w,h,3)?,pw,kind)?);
                offset=end;
            }
            if offset!=data.len() {return Err("OCR image batch payload mismatch".into());}
            let (shape,bytes)=ocr.infer_values(model,vec![widths.len(),3,h,pw],&tensor)?;
            Ok((json!({"ok":true,"shape":shape}),bytes))
        }
        "match_map" => {
            let (w,h,tw,th,c)=(number(r,"width")?,number(r,"height")?,number(r,"template_width")?,number(r,"template_height")?,number(r,"channels")?);
            let n=w.checked_mul(h).and_then(|n|n.checked_mul(c)).ok_or("invalid source dimensions")?;
            let tn=tw.checked_mul(th).and_then(|n|n.checked_mul(c)).ok_or("invalid template dimensions")?;
            let masked=r["masked"].as_bool().unwrap_or(false);
            let mask_len=if masked {tw.checked_mul(th).ok_or("invalid mask dimensions")?} else {0};
            if n.checked_add(tn).and_then(|v|v.checked_add(mask_len))!=Some(data.len()) {return Err("match map payload size mismatch".into());}
            let source=vision::Frame::new(&data[..n],w,h,c)?;
            let template=vision::Frame::new(&data[n..n+tn],tw,th,c)?;
            let scores=matcher.map(source,template,if masked {Some(&data[n+tn..])} else {None})?;
            Ok((json!({"ok":true,"width":w-tw+1,"height":h-th+1}),scores.iter().flat_map(|s|s.to_le_bytes()).collect()))
        }
        "nemu_capture" => {
            let folder=r["folder"].as_str().ok_or("missing MuMu folder")?;
            let instance=i32::try_from(number(r,"instance")?).map_err(|_|"invalid MuMu instance")?;
            let display=i32::try_from(number(r,"display")?).map_err(|_|"invalid MuMu display")?;
            let (w,h,bytes)=nemu.capture(folder,instance,display)?;
            Ok((json!({"ok":true,"width":w,"height":h}),bytes))
        }
        "desktop_press" => {
            desktop_press(number(r,"hwnd")?,number(r,"x")?,number(r,"y")?,r["seconds"].as_f64().ok_or("missing press duration")?)?;
            Ok((json!({"ok":true}),vec![]))
        }
        "ocr_infer" => {
            let model = r["model"].as_str().ok_or("missing OCR model path")?;
            let shape = r["shape"].as_array().ok_or("missing OCR tensor shape")?.iter()
                .map(|v|v.as_u64().and_then(|v|usize::try_from(v).ok()).ok_or("invalid OCR tensor dimension"))
                .collect::<Result<Vec<_>,_>>()?;
            let (shape,bytes)=ocr.infer(model,shape,data)?;
            Ok((json!({"ok":true,"shape":shape}),bytes))
        }
        "match" => {
            let (w,h,tw,th) = (number(r,"width")?,number(r,"height")?,number(r,"template_width")?,number(r,"template_height")?);
            let n = image_len(w,h)?;
            if data.len() != n + image_len(tw,th)? { return Err("image payload size mismatch".into()); }
            let (score,x,y) = match_rgb(&data[..n],w,h,&data[n..],tw,th)?;
            Ok((json!({"ok":true,"score":score,"x":x,"y":y}), vec![]))
        }
        "capture" => {
            let (w,h) = (number(r,"width")?,number(r,"height")?);
            image_len(w,h)?;
            let bytes = capture(number(r,"hwnd")?,w,h,r["client"].as_bool().unwrap_or(false))?;
            Ok((json!({"ok":true,"width":w,"height":h}), bytes))
        }
        "adb" => {
            let executable = r["executable"].as_str().ok_or("missing adb executable")?;
            let serial = r["serial"].as_str().filter(|s| !s.is_empty()).ok_or("missing adb serial")?;
            let mut command = std::process::Command::new(executable);
            crate::runtime::hide(&mut command).arg("-s").arg(serial);
            match r["action"].as_str().unwrap_or("") {
                "capture" => { command.args(["exec-out","screencap","-p"]); }
                "tap" => { command.args(["shell","input","tap"]).arg(number(r,"x")?.to_string()).arg(number(r,"y")?.to_string()); }
                "swipe" => { command.args(["shell","input","swipe"]); for key in ["x","y","end_x","end_y","duration_ms"] { command.arg(number(r,key)?.to_string()); } }
                _ => return Err("unsupported adb action".into()),
            }
            let result = adb_output(&mut command)?;
            if !result.status.success() { return Err(format!("ADB exited with {}: {}",result.status,String::from_utf8_lossy(&result.stderr))); }
            Ok((json!({"ok":true}), result.stdout))
        }
        _ => Err("unsupported native command".into()),
    }
}

#[cfg(windows)]
fn desktop_press(hwnd:usize,x:usize,y:usize,seconds:f64)->Result<(),String> {
    use windows_sys::Win32::UI::WindowsAndMessaging::*;
    if !seconds.is_finite() || !(0.0..=60.0).contains(&seconds) || x>65535 || y>65535 {
        return Err("invalid desktop press".into());
    }
    unsafe {
        let hwnd=hwnd as _;
        if IsWindow(hwnd)==0 {return Err("invalid window handle".into());}
        let point=((y<<16)|x) as isize;
        if PostMessageW(hwnd,WM_LBUTTONDOWN,1,point)==0 {return Err(std::io::Error::last_os_error().to_string());}
        std::thread::sleep(std::time::Duration::from_secs_f64(seconds));
        if PostMessageW(hwnd,WM_LBUTTONUP,0,point)==0 {return Err(std::io::Error::last_os_error().to_string());}
        SendMessageW(hwnd,WM_CAPTURECHANGED,0,0);
        PostMessageW(hwnd,WM_MOUSEMOVE,0,point);
        Ok(())
    }
}
#[cfg(not(windows))]
fn desktop_press(_:usize,_:usize,_:usize,_:f64)->Result<(),String> {Err("Windows control unavailable".into())}

fn adb_output(command: &mut std::process::Command) -> Result<std::process::Output,String> {
    use std::{process::Stdio,thread,time::{Duration,Instant}};
    let mut child=command.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|e|e.to_string())?;
    let mut stdout=child.stdout.take().unwrap(); let mut stderr=child.stderr.take().unwrap();
    let out=thread::spawn(move || {let mut data=Vec::new(); stdout.read_to_end(&mut data).map(|_|data)});
    let err=thread::spawn(move || {let mut data=Vec::new(); stderr.read_to_end(&mut data).map(|_|data)});
    let deadline=Instant::now()+Duration::from_secs(20);
    let status=loop {
        match child.try_wait() {
            Ok(Some(status))=>break Ok(status),
            Ok(None) if Instant::now()<deadline=>thread::sleep(Duration::from_millis(10)),
            result=>{let _=child.kill(); let _=child.wait(); break Err(match result {Err(e)=>e.to_string(),_=>"ADB timed out after 20s".into()});}
        }
    };
    let stdout=out.join().map_err(|_|"ADB output reader failed")?.map_err(|e|e.to_string())?;
    let stderr=err.join().map_err(|_|"ADB error reader failed")?.map_err(|e|e.to_string())?;
    Ok(std::process::Output {status:status?,stdout,stderr})
}

/// OpenCV TM_CCOEFF_NORMED: subtract each channel's mean independently.
pub fn match_rgb(src: &[u8], w: usize, h: usize, tpl: &[u8], tw: usize, th: usize) -> Result<(f64,usize,usize),String> {
    if src.len()!=image_len(w,h)? || tpl.len()!=image_len(tw,th)? || tw>w || th>h { return Err("invalid match dimensions".into()); }
    let pixels = (tw*th) as f64;
    let mut means = [0.0;3];
    for p in tpl.chunks_exact(3) { for c in 0..3 { means[c]+=p[c] as f64/pixels; } }
    let centered: Vec<f64> = tpl.iter().enumerate().map(|(i,v)| *v as f64-means[i%3]).collect();
    let variance: f64 = centered.iter().map(|v| v*v).sum();
    if variance <= f64::EPSILON { return Err("flat template".into()); }
    let mut best = (-1.0,0,0);
    for y in 0..=h-th { for x in 0..=w-tw {
        let (mut sum,mut square,mut dot) = ([0.0;3],0.0,0.0);
        for ty in 0..th {
            let row = &src[((y+ty)*w+x)*3..((y+ty)*w+x+tw)*3];
            for (i,v) in row.iter().enumerate() {
                let v = *v as f64;
                sum[i%3]+=v; square+=v*v; dot+=v*centered[ty*tw*3+i];
            }
        }
        let sv = (square-sum.iter().map(|s| s*s/pixels).sum::<f64>()).max(0.0);
        let score = if sv <= f64::EPSILON { 0.0 } else { (dot/(variance*sv).sqrt()).clamp(-1.0,1.0) };
        if score > best.0 { best=(score,x,y); }
    } }
    Ok(best)
}

#[cfg(windows)]
fn capture(hwnd: usize, w: usize, h: usize, client: bool) -> Result<Vec<u8>,String> {
    use windows_sys::Win32::{Graphics::Gdi::*, UI::{WindowsAndMessaging::*,HiDpi::*}};
    unsafe {
        let hwnd = hwnd as _;
        if IsWindow(hwnd)==0 { return Err("invalid window handle".into()); }
        // The worker needs its own DPI context; the Python caller's is thread local.
        let dpi = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let source = if client { GetDC(hwnd) } else { GetWindowDC(hwnd) };
        if source.is_null() { if !dpi.is_null() { SetThreadDpiAwarenessContext(dpi); } return Err("GetDC failed".into()); }
        let dc = CreateCompatibleDC(source);
        let bitmap = CreateCompatibleBitmap(source,w as i32,h as i32);
        let old = SelectObject(dc,bitmap as _);
        let mut bgra = vec![0u8;w*h*4];
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = w as i32; info.bmiHeader.biHeight = -(h as i32);
        info.bmiHeader.biPlanes = 1; info.bmiHeader.biBitCount = 32; info.bmiHeader.biCompression = BI_RGB;
        let copied = !dc.is_null() && !bitmap.is_null() && BitBlt(dc,0,0,w as i32,h as i32,source,0,0,SRCCOPY)!=0;
        SelectObject(dc,old);
        let read = copied && GetDIBits(dc,bitmap,0,h as u32,bgra.as_mut_ptr() as _,&mut info,DIB_RGB_COLORS)==h as i32;
        DeleteObject(bitmap as _); DeleteDC(dc); ReleaseDC(hwnd,source);
        if !dpi.is_null() { SetThreadDpiAwarenessContext(dpi); }
        if !read { return Err("BitBlt/GetDIBits failed".into()); }
        Ok(bgra.chunks_exact(4).flat_map(|p| [p[2],p[1],p[0]]).collect())
    }
}
#[cfg(not(windows))]
fn capture(_:usize,_:usize,_:usize,_:bool)->Result<Vec<u8>,String> { Err("Windows capture unavailable".into()) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matches_channels_and_exact_location() {
        let src:Vec<u8>=(0..7*5*3).map(|i| ((i*i+17*i)%251) as u8).collect();
        let tpl:Vec<u8>=(0..2).flat_map(|y| src[((y+1)*7+3)*3..((y+1)*7+5)*3].to_vec()).collect();
        let (score,x,y)=match_rgb(&src,7,5,&tpl,2,2).unwrap();
        assert!(score>0.999999); assert_eq!((x,y),(3,1));
    }
    #[test]
    fn invalid_frames_and_flat_templates_fail() {
        assert!(match_rgb(&[0;12],2,2,&[0;3],1,1).is_err());
        assert!(match_rgb(&[0;11],2,2,&[1,2,3],1,1).is_err());
        assert!(execute(&json!({"command":"match","width":2,"height":2,"template_width":2,"template_height":2}),&[],&mut ocr::OcrEngine::default(),&mut nemu::Nemu::default(),&mut vision::Matcher::default()).is_err());
    }
    #[test]
    fn invalid_handle_is_an_error() {
        assert!(capture(0,2,2,true).is_err());
        assert!(desktop_press(0,12,9,0.01).is_err());
    }
}
