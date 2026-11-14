use std::{env, fs, path::PathBuf};

fn resource(output: &mut Vec<u8>, kind: u16, id: u16, data: &[u8]) {
    output.extend_from_slice(&(data.len() as u32).to_le_bytes());
    output.extend_from_slice(&32u32.to_le_bytes());
    for value in [0xffff, kind, 0xffff, id] { output.extend_from_slice(&value.to_le_bytes()); }
    output.extend_from_slice(&0u32.to_le_bytes());
    output.extend_from_slice(&0x1030u16.to_le_bytes());
    output.extend_from_slice(&0u16.to_le_bytes());
    output.extend_from_slice(&[0; 8]);
    output.extend_from_slice(data);
    while output.len() % 4 != 0 { output.push(0); }
}

fn main() {
    println!("cargo:rerun-if-changed=assets/app.ico");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") { return; }
    let icon = fs::read("assets/app.ico").expect("read app icon");
    assert_eq!(&icon[..4], &[0, 0, 1, 0], "invalid ICO header");
    let count = u16::from_le_bytes(icon[4..6].try_into().unwrap());
    let mut resources = vec![];
    resource(&mut resources, 0, 0, &[]); // Required empty .res header.
    let mut group = icon[..6].to_vec();
    for index in 0..count {
        let entry = &icon[6 + index as usize * 16..6 + (index as usize + 1) * 16];
        let size = u32::from_le_bytes(entry[8..12].try_into().unwrap()) as usize;
        let offset = u32::from_le_bytes(entry[12..16].try_into().unwrap()) as usize;
        let id = index + 1;
        resource(&mut resources, 3, id, &icon[offset..offset + size]);
        group.extend_from_slice(&entry[..12]);
        group.extend_from_slice(&id.to_le_bytes());
    }
    resource(&mut resources, 14, 1, &group);
    let path = PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("app-icon.res");
    fs::write(&path, resources).expect("write icon resources");
    // Both the bundled MinGW LLD and the MSVC linker accept Windows .res files.
    println!("cargo:rustc-link-arg-bin=GanganMouse={}", path.display());
}
