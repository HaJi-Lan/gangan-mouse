#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod layout;
mod instance;
mod help;
mod runtime;
mod update;

use eframe::egui;
use layout::Layout;
use runtime::{Work, Worker};
use serde_json::{json, Value};
use std::{collections::{HashMap, VecDeque}, time::{Duration, Instant}};

struct Desktop {
    worker: Worker,
    revision: String,
    online: bool,
    updating: bool,
    confirm_update: bool,
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
    edits: HashMap<String, Value>,
    states: HashMap<String, i64>,
    logs: VecDeque<(String, String)>,
    search: String,
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
        cc.egui_ctx.set_visuals(egui::Visuals::dark());
        let mut style = (*cc.egui_ctx.style()).clone();
        style.spacing.item_spacing = egui::vec2(10.0, 8.0);
        style.text_styles.insert(egui::TextStyle::Body, egui::FontId::proportional(16.0));
        style.text_styles.insert(egui::TextStyle::Button, egui::FontId::proportional(16.0));
        cc.egui_ctx.set_style(style);
        Self {
            revision: update::revision(&layout), worker: Worker::new(layout), online: false,
            updating: false, confirm_update: false, message: "正在初始化任务运行环境…".into(),
            restart_required: false,
            deleting: false,
            error: None,
            configs: vec![], selected: String::new(), task: "Script".into(), menu: Value::Null,
            translations: Value::Null, groups: Value::Null, edits: HashMap::new(),
            states: HashMap::new(), logs: VecDeque::new(), search: String::new(), show_all_logs: false,
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
                    let completing_update = self.updating;
                    self.online = true;
                    self.updating = false;
                    if self.restart_required { self.message = "肝肝鼠更新完成，请关闭并重新打开软件。".into(); }
                    else if !completing_update { self.message = "运行环境已连接".into(); }
                    self.worker.request(json!({"command":"hello"}));
                }
                "log" => self.log(event["config"].as_str().unwrap_or("").into(), event["text"].as_str().unwrap_or("").into()),
                "state" => { self.states.insert(event["config"].as_str().unwrap_or("").into(), event["state"].as_i64().unwrap_or(0)); }
                "task" => {
                    if let Some(state) = event["data"]["state"].as_i64() {
                        self.states.insert(event["config"].as_str().unwrap_or("").into(), state);
                    }
                }
                "fatal" => { self.online = false; self.updating = false; self.deleting = false; self.states.clear(); self.error = Some(event["error"].as_str().unwrap_or("运行环境退出").into()); }
                "error" => { self.updating = false; self.error = Some(event["error"].as_str().unwrap_or("操作失败").into()); }
                "updated" => {
                    self.states.clear();
                    self.restart_required |= event["data"]["restart_required"] == true;
                    self.message = if self.restart_required { "肝肝鼠更新完成，请关闭并重新打开软件。" } else { "肝肝鼠已是最新版本。" }.into();
                    self.revision = event["data"]["after"].as_str().unwrap_or("").chars().take(8).collect();
                    self.log(String::new(), format!("更新完成：{}", event["data"]));
                }
                "response" => {
                    if event["ok"] != true {
                        if event["command"] == "delete" { self.deleting = false; }
                        self.error = Some(event["error"].as_str().unwrap_or("操作失败").into());
                        continue;
                    }
                    let data = &event["data"];
                    match event["command"].as_str().unwrap_or("") {
                        "hello" | "create" | "delete" => {
                            if event["command"] == "delete" {
                                self.deleting = false;
                                if let Some(name) = data["deleted"].as_str() { self.states.remove(name); }
                                self.groups = Value::Null;
                                self.edits.clear();
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
                                self.edits.clear();
                                self.message = if event["command"] == "set" { "配置已保存" } else { "就绪" }.into();
                            }
                        }
                        "start" => self.message = "任务启动请求已执行，请查看日志".into(),
                        "stop" => self.message = "任务已停止".into(),
                        _ => {}
                    }
                }
                _ => {}
            }
        }
    }
    fn fields(&mut self, ui: &mut egui::Ui) {
        let groups = self.groups.as_object().cloned().unwrap_or_default();
        for (group, fields) in groups {
            egui::CollapsingHeader::new(self.label(&group)).id_salt((&self.selected, &self.task, &group))
                .default_open(true).show(ui, |ui| {
                for field in fields.as_array().into_iter().flatten() {
                    let key = field["name"].as_str().unwrap_or("");
                    let note = help::description(&self.translations, key, field["description"].as_str());
                    let id = format!("{group}/{key}");
                    let mut value = self.edits.get(&id).unwrap_or(&field["value"]).clone();
                    ui.push_id(&id, |ui| {
                        ui.horizontal_wrapped(|ui| {
                            let title = self.label(key);
                            ui.label(title).on_hover_text(&note);
                            if let Some(options) = field["enumEnum"].as_array() {
                                let current = value.as_str().map(str::to_string).unwrap_or_else(|| value.to_string());
                                egui::ComboBox::from_id_salt("value").selected_text(self.label(&current)).show_ui(ui, |ui| {
                                    for option in options {
                                        let label = option.as_str().map(str::to_string).unwrap_or_else(|| option.to_string());
                                        ui.selectable_value(&mut value, option.clone(), self.label(&label));
                                    }
                                });
                            } else if let Some(mut checked) = value.as_bool() {
                                ui.checkbox(&mut checked, "");
                                value = Value::Bool(checked);
                            } else {
                                let mut text = value.as_str().map(str::to_string).unwrap_or_else(|| value.to_string());
                                if ui.add(egui::TextEdit::singleline(&mut text).desired_width(320.0)).changed() {
                                    value = Value::String(text);
                                }
                            }
                            if value != field["value"] && ui.button("保存").clicked() {
                                let mut parsed = value.clone();
                                let kind = field["type"].as_str().unwrap_or("");
                                if kind == "integer" || kind == "number" {
                                    match serde_json::from_str::<Value>(value.as_str().unwrap_or("")) {
                                        Ok(number) if number.is_number() && (kind != "integer" || number.is_i64() || number.is_u64()) => parsed = number,
                                        _ => { self.message = "请输入有效的数字".into(); return; }
                                    }
                                }
                                self.worker.request(json!({"command":"set","config":self.selected,"task":self.task,"group":group,"argument":key,"value":parsed}));
                            }
                        });
                        if !note.is_empty() {
                            ui.add(egui::Label::new(egui::RichText::new(&note).size(14.0)
                                .color(ui.visuals().weak_text_color())).wrap());
                            ui.add_space(6.0);
                        }
                    });
                    if value != field["value"] { self.edits.insert(id, value); }
                    else { self.edits.remove(&id); }
                }
            });
            ui.separator();
        }
    }
}

impl eframe::App for Desktop {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.receive();
        ctx.request_repaint_after(Duration::from_millis(100));
        egui::TopBottomPanel::top("toolbar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.heading("肝肝鼠");
                ui.label("统一桌面 · Rust");
                ui.separator();
                ui.colored_label(if self.online { egui::Color32::LIGHT_GREEN } else { egui::Color32::YELLOW }, if self.online { "运行环境已连接" } else { "运行环境未连接" });
                ui.label(format!("版本 {}", self.revision));
                if ui.add_enabled(!self.updating, egui::Button::new("更新")).clicked() { self.confirm_update = true; }
                if ui.add_enabled(!self.updating, egui::Button::new("重连运行环境")).clicked() {
                    self.online = false; self.states.clear(); let _ = self.worker.tx.send(Work::Restart);
                }
            });
        });
        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            if let Some(error) = self.error.clone() {
                ui.horizontal_wrapped(|ui| {
                    ui.colored_label(egui::Color32::LIGHT_RED, error);
                    if ui.small_button("关闭提示").clicked() { self.error = None; }
                });
            }
            ui.label(&self.message);
        });
        egui::SidePanel::left("navigation").default_width(230.0).show(ctx, |ui| {
            ui.heading("配置与任务");
            let old = self.selected.clone();
            egui::ComboBox::from_id_salt("config").selected_text(&self.selected).width(180.0).show_ui(ui, |ui| {
                for name in &self.configs { ui.selectable_value(&mut self.selected, name.clone(), name); }
            });
            ui.horizontal(|ui| {
                if ui.add_enabled(self.online && !self.deleting, egui::Button::new("新增配置")).clicked() {
                    self.worker.request(json!({"command":"create"}));
                }
                if ui.add_enabled(self.online && !self.updating && !self.deleting && !self.selected.is_empty(),
                    egui::Button::new("删除配置")).clicked() {
                    self.deleting = true;
                    self.worker.request(json!({"command":"delete","config":self.selected}));
                }
            });
            if old != self.selected { self.groups = Value::Null; self.edits.clear(); self.schema(); }
            let running = self.states.get(&self.selected).copied().unwrap_or(0) != 0;
            ui.horizontal(|ui| {
                if ui.add_enabled(self.online && !running && !self.selected.is_empty(), egui::Button::new("启动任务")).clicked() {
                    self.worker.request(json!({"command":"start","config":self.selected}));
                }
                if ui.add_enabled(self.online && running, egui::Button::new("停止任务")).clicked() {
                    self.worker.request(json!({"command":"stop","config":self.selected}));
                }
            });
            ui.label(if running { "状态：运行中" } else { "状态：已停止" });
            ui.separator();
            ui.add(egui::TextEdit::singleline(&mut self.search).hint_text("搜索任务"));
            egui::ScrollArea::vertical().show(ui, |ui| {
                let menu = self.menu.as_object().cloned().unwrap_or_default();
                for (category, tasks) in menu {
                    let tasks: Vec<String> = tasks.as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_string).collect();
                    if tasks.is_empty() { continue; }
                    egui::CollapsingHeader::new(self.label(&category)).default_open(true).show(ui, |ui| {
                        for task in tasks {
                            let label = self.label(&task);
                            if !self.search.is_empty() && !label.to_lowercase().contains(&self.search.to_lowercase()) && !task.to_lowercase().contains(&self.search.to_lowercase()) { continue; }
                            if ui.selectable_label(self.task == task, label).clicked() {
                                self.task = task; self.groups = Value::Null; self.edits.clear(); self.schema();
                            }
                        }
                    });
                }
            });
        });
        egui::TopBottomPanel::bottom("logs").resizable(true).default_height(190.0).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.strong("运行日志");
                ui.checkbox(&mut self.show_all_logs, "所有配置");
                if ui.button("清空显示").clicked() { self.logs.clear(); }
            });
            egui::ScrollArea::vertical().stick_to_bottom(true).show(ui, |ui| {
                for (config, text) in &self.logs {
                    if self.show_all_logs || config.is_empty() || config == &self.selected {
                        ui.label(egui::RichText::new(if config.is_empty() { text.clone() } else { format!("[{config}] {text}") }).monospace().size(13.0));
                    }
                }
            });
        });
        egui::CentralPanel::default().show(ctx, |ui| {
            if self.selected.is_empty() {
                ui.heading("暂无配置");
                ui.label("点击左侧“新增配置”开始设置任务。");
                return;
            }
            ui.heading(self.label(&self.task));
            let task_note = help::description(&self.translations, &self.task, None);
            if !task_note.is_empty() {
                ui.add(egui::Label::new(egui::RichText::new(task_note).color(ui.visuals().weak_text_color())).wrap());
            }
            ui.label("修改后点击对应字段的“保存”，任务规则与校验跟随上游版本。");
            ui.separator();
            ui.add_enabled_ui(self.online && !self.updating, |ui| {
                egui::ScrollArea::vertical().id_salt("settings").show(ui, |ui| { self.fields(ui); });
            });
        });
        if self.confirm_update {
            egui::Window::new("更新").collapsible(false).resizable(false).show(ctx, |ui| {
                ui.label("更新来源：HaJi-Lan/gangan-mouse · main");
                ui.label("更新前会停止本程序管理的任务；完成后请重新打开软件。");
                ui.label("配置和日志保留。存在本地修改或 Python 依赖变化时会取消更新。");
                ui.horizontal(|ui| {
                    if ui.button("停止任务并更新").clicked() {
                        self.confirm_update = false; self.updating = true; self.online = false;
                        self.states.clear(); self.message = "正在更新并构建肝肝鼠…".into();
                        let _ = self.worker.tx.send(Work::Sync);
                    }
                    if ui.button("取消").clicked() { self.confirm_update = false; }
                });
            });
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
    let layout = Layout::discover().map_err(std::io::Error::other)?;
    #[cfg(windows)]
    let _instance = instance::Instance::acquire(&layout.root).map_err(std::io::Error::other)?;
    if std::env::args().any(|arg| arg == "--check") {
        return check(layout).map_err(|e| std::io::Error::other(e).into());
    }
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1180.0, 820.0]).with_min_inner_size([900.0, 620.0]),
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
        if !std::env::args().any(|arg| arg == "--check") {
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
