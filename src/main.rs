#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod layout;
mod instance;
mod help;
mod editor;
mod execution;
mod ui;
mod runtime;
mod task_catalog;
mod native;
mod update;

use eframe::egui;
use layout::Layout;
use runtime::Worker;
use serde_json::{json, Value};
use std::{collections::{HashMap, VecDeque}, time::{Duration, Instant}};

struct Desktop {
    worker: Worker,
    revision: String,
    online: bool,
    updating: bool,
    confirm_update: bool,
    show_update_source: bool,
    update_source: update::Source,
    update_root: std::path::PathBuf,
    restart_required: bool,
    deleting: bool,
    message: String,
    error: Option<String>,
    configs: Vec<String>,
    selected: String,
    task: String,
    menu: Value,
    translations: Value,
    groups: Value,
    editor: editor::Editor,
    pending_action: Option<String>,
    show_help: bool,
    show_logs: bool,
    errors_only: bool,
    follow_logs: bool,
    icon: egui::TextureHandle,
    states: HashMap<String, i64>,
    logs: VecDeque<(String, String)>,
    task_switches: HashMap<String, HashMap<String, bool>>,
    executions: HashMap<String, execution::Execution>,
    show_all_logs: bool,
}

impl Desktop {
    fn new(cc: &eframe::CreationContext<'_>, layout: Layout) -> Self {
        let mut fonts = egui::FontDefinitions::default();
        if let Ok(data) = std::fs::read("C:/Windows/Fonts/msyh.ttc") {
            fonts.font_data.insert("chinese".into(), egui::FontData::from_owned(data));
            fonts.families.entry(egui::FontFamily::Proportional).or_default().insert(0, "chinese".into());
            fonts.families.entry(egui::FontFamily::Monospace).or_default().push("chinese".into());
        }
        cc.egui_ctx.set_fonts(fonts);
        ui::theme(&cc.egui_ctx);
        let mut style = (*cc.egui_ctx.style_of(egui::Theme::Light)).clone();
        style.spacing.item_spacing = egui::vec2(7.0, 4.0);
        style.text_styles.insert(egui::TextStyle::Body, egui::FontId::proportional(14.0));
        style.text_styles.insert(egui::TextStyle::Button, egui::FontId::proportional(14.0));
        style.spacing.button_padding = egui::vec2(8.0, 4.0);
        cc.egui_ctx.set_style_of(egui::Theme::Light, style);
        let source = update::Source::load(&layout.root);
        let source_error = source.as_ref().err().cloned();
        Self {
            update_root: layout.root.clone(), update_source: source.unwrap_or_default(), show_update_source: false,
            revision: update::revision(&layout), worker: Worker::new(layout), online: false,
            updating: false, confirm_update: false, message: "正在初始化任务运行环境…".into(),
            restart_required: false,
            deleting: false,
            error: source_error,
            configs: vec![], selected: String::new(), task: "Script".into(), menu: Value::Null,
            translations: Value::Null, groups: Value::Null, editor: editor::Editor::default(),
            pending_action: None, show_help: true, show_logs: true, errors_only: false, follow_logs: true,
            icon: cc.egui_ctx.load_texture("app", egui::ColorImage::from_rgba_unmultiplied([128, 128], include_bytes!("../assets/app-icon.rgba")), egui::TextureOptions::LINEAR),
            states: HashMap::new(), logs: VecDeque::new(), show_all_logs: false,
            task_switches: HashMap::new(), executions: HashMap::new(),
        }
    }
    fn label(&self, key: &str) -> String {
        self.translations.get(key).and_then(Value::as_str).unwrap_or(key).to_string()
    }
    fn schema(&self) {
        if !self.selected.is_empty() {
            self.worker.request(json!({"command":"schema","config":self.selected,"task":self.task}));
        }
    }
    fn log(&mut self, config: String, text: String) {
        self.logs.push_back((config, text));
        while self.logs.len() > 1500 { self.logs.pop_front(); }
    }
    fn receive(&mut self) {
        for _ in 0..500 {
            let Ok(event) = self.worker.events.try_recv() else { break; };
            match event["event"].as_str().unwrap_or("") {
                "ready" => {
                    self.pending_action = None;
                    let completing_update = self.updating;
                    self.online = true;
                    self.updating = false;
                    if self.restart_required { self.message = "肝肝鼠更新完成，请关闭并重新打开软件。".into(); }
                    else if !completing_update { self.message = "运行环境已连接".into(); }
                    self.worker.request(json!({"command":"hello"}));
                }
                "log" => self.log(event["config"].as_str().unwrap_or("").into(), event["text"].as_str().unwrap_or("").into()),
                "state" => {
                    let name = event["config"].as_str().unwrap_or("").to_string();
                    let state = event["state"].as_i64().unwrap_or(0);
                    if state == 0 { self.executions.remove(&name); }
                    self.states.insert(name, state);
                }
                "task" => {
                    let name = event["config"].as_str().unwrap_or("").to_string();
                    self.executions.entry(name.clone()).or_default().apply(&event["data"]);
                    if let Some(state) = event["data"]["state"].as_i64() {
                        if state == 0 { self.executions.remove(&name); }
                        self.states.insert(name, state);
                    }
                }
                "fatal" => { self.online = false; self.updating = false; self.deleting = false; self.pending_action = None; self.editor.cancel(); self.states.clear(); self.executions.clear(); self.error = Some(event["error"].as_str().unwrap_or("运行环境退出").into()); }
                "error" => { self.pending_action = None; self.editor.cancel(); self.updating = false; self.error = Some(event["error"].as_str().unwrap_or("操作失败").into()); }
                "updated" => {
                    self.states.clear(); self.executions.clear();
                    self.restart_required |= event["data"]["restart_required"] == true;
                    self.message = if self.restart_required { "肝肝鼠更新完成，请关闭并重新打开软件。" } else { "肝肝鼠已是最新版本。" }.into();
                    self.revision = event["data"]["after"].as_str().unwrap_or("").chars().take(8).collect();
                    self.log(String::new(), format!("更新完成：{}", event["data"]));
                }
                "response" => {
                    if event["ok"] == true {
                        if let (Some(config), Some(switches)) = (event["data"]["config"].as_str(), event["data"]["task_switches"].as_object()) {
                            self.task_switches.insert(config.into(), switches.iter().filter_map(|(task, value)| value.as_bool().map(|v| (task.clone(), v))).collect());
                        }
                    }
                    if self.editor.acknowledge(event["id"].as_str(), event["ok"] == true) {
                        if event["ok"] == true {
                            let data = &event["data"];
                            if data["config"] == self.selected && data["task"] == self.task { self.groups = data["groups"].clone(); }
                            self.message = format!("已保存 {} 项修改", self.editor.saved);
                            self.send_next_save();
                            if !self.editor.busy() { self.groups = Value::Null; self.schema(); }
                        } else {
                            self.message = format!("已保存 {} 项，其余修改仍保留", self.editor.saved);
                            self.error = Some(event["error"].as_str().unwrap_or("保存失败，请检查输入").into());
                            self.groups = Value::Null; self.schema();
                        }
                        continue;
                    }
                    if self.pending_action.as_deref() == event["command"].as_str() { self.pending_action = None; }
                    if event["ok"] != true {
                        if event["command"] == "delete" { self.deleting = false; }
                        self.error = Some(event["error"].as_str().unwrap_or("操作失败").into());
                        continue;
                    }
                    let data = &event["data"];
                    match event["command"].as_str().unwrap_or("") {
                        "hello" | "create" | "delete" => {
                            self.groups = Value::Null;
                            if event["command"] == "delete" {
                                self.deleting = false;
                                if let Some(name) = data["deleted"].as_str() { self.states.remove(name); self.executions.remove(name); self.editor.discard(name); }
                                self.groups = Value::Null;
                                self.log(String::new(), format!("已删除配置 {}", data["deleted"]));
                            }
                            self.configs = data["configs"].as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_string).collect();
                            if !data["menu"].is_null() { self.menu = data["menu"].clone(); self.translations = data["translations"].clone(); }
                            if let Some(name) = data["selected"].as_str() { self.selected = name.into(); }
                            if !self.configs.contains(&self.selected) { self.selected = self.configs.first().cloned().unwrap_or_default(); }
                            self.schema();
                        }
                        "schema" | "set" => {
                            if data["config"] == self.selected && data["task"] == self.task {
                                self.groups = data["groups"].clone();
                            }
                        }
                        "start" => { self.message = "配置已启动，运行情况见日志".into(); self.show_logs = true; },
                        "stop" => self.message = "配置已停止".into(),
                        "export_logs" => {
                            let path = data["path"].as_str().unwrap_or("");
                            self.message = format!("诊断日志已导出：{path}");
                            self.log(self.selected.clone(), format!("诊断日志已导出：{path}"));
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }
    }

}


fn check(layout: Layout) -> Result<(), String> {
    let worker = Worker::new(layout);
    let until = Instant::now() + Duration::from_secs(60);
    while Instant::now() < until {
        let event = worker.events.recv_timeout(Duration::from_secs(1));
        match event {
            Ok(event) if event["event"] == "ready" => worker.request(json!({"command":"hello"})),
            Ok(event) if event["event"] == "response" => {
                if event["ok"] != true { return Err(event.to_string()); }
                println!("{event}");
                return Ok(());
            }
            Ok(event) if event["event"] == "fatal" => return Err(event.to_string()),
            _ => {}
        }
    }
    Err("运行环境检查超时".into())
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    if std::env::args().any(|arg| arg == "--native-worker") {
        return native::serve().map_err(|e| std::io::Error::other(e).into());
    }
    if std::env::args().any(|arg| arg == "--task-catalog") {
        task_catalog::TaskCatalog::bundled();
        println!("{}", task_catalog::SOURCE);
        return Ok(());
    }
    let layout = Layout::discover().map_err(std::io::Error::other)?;
    #[cfg(windows)]
    let _instance = instance::Instance::acquire(&layout.root).map_err(std::io::Error::other)?;
    if std::env::args().any(|arg| arg == "--check") {
        return check(layout).map_err(|e| std::io::Error::other(e).into());
    }
    if std::env::args().any(|arg| arg == "--update") {
        let result = update::sync(&layout).map_err(std::io::Error::other)?;
        println!("{result}");
        return Ok(());
    }
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1160.0, 740.0]).with_min_inner_size([1050.0, 640.0])
            .with_icon(egui::IconData { rgba: include_bytes!("../assets/app-icon.rgba").to_vec(), width: 128, height: 128 }),
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };
    eframe::run_native("肝肝鼠", options, Box::new(move |cc| Ok(Box::new(Desktop::new(cc, layout)))))?;
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        #[cfg(windows)]
        if !std::env::args().any(|arg| arg == "--check" || arg == "--update" || arg == "--task-catalog" || arg == "--native-worker") {
            let message: Vec<u16> = format!("肝肝鼠启动失败：{error}\0").encode_utf16().collect();
            let title: Vec<u16> = "肝肝鼠\0".encode_utf16().collect();
            unsafe {
                use windows_sys::Win32::UI::WindowsAndMessaging::*;
                MessageBoxW(std::ptr::null_mut(), message.as_ptr(), title.as_ptr(), MB_OK | MB_ICONERROR);
            }
        }
        std::process::exit(1);
    }
}
