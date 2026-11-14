use super::{Desktop, editor::{Draft, Key}, help, runtime::Work};
use eframe::egui::{self, Color32, RichText};
use serde_json::{json, Value};
use std::time::Duration;

const BACKGROUND: Color32 = Color32::from_rgb(245, 244, 239);
const INK: Color32 = Color32::from_rgb(45, 43, 39);
const ACCENT: Color32 = Color32::from_rgb(164, 119, 43);
const MUTED: Color32 = Color32::from_rgb(116, 112, 103);

pub fn theme(ctx: &egui::Context) {
    ctx.set_theme(egui::ThemePreference::Light);
    let mut visuals = egui::Visuals::light();
    visuals.panel_fill = Color32::WHITE;
    visuals.window_fill = Color32::WHITE;
    visuals.extreme_bg_color = BACKGROUND;
    visuals.selection.bg_fill = Color32::from_rgb(243, 226, 193);
    visuals.selection.stroke = egui::Stroke::new(1.0_f32, INK);
    visuals.widgets.hovered.bg_fill = Color32::from_rgb(245, 235, 216);
    visuals.widgets.active.bg_fill = Color32::from_rgb(235, 217, 183);
    visuals.override_text_color = Some(INK);
    ctx.set_visuals_of(egui::Theme::Light, visuals);
}

impl Desktop {
    fn busy(&self) -> bool { self.updating || self.deleting || self.editor.busy() || self.pending_action.is_some() }
    fn editable(&self) -> bool { self.online && !self.busy() }
    fn key(&self, group: &str, argument: &str) -> Key {
        Key::new(&self.selected, &self.task, group, argument)
    }
    fn action(&mut self, command: &str) {
        self.error = None;
        self.message = match command { "create" => "正在创建配置…", "delete" => "正在删除配置…", "stop" => "正在停止配置…", _ => "正在处理…" }.into();
        self.pending_action = Some(command.into());
        self.worker.request(json!({"command":command,"config":self.selected}));
    }
    pub(super) fn send_next_save(&mut self) {
        if let Some(request) = self.editor.next_request() { self.worker.request(request); }
        else if let Some(config) = self.editor.take_start() {
            self.pending_action = Some("start".into());
            self.message = "正在启动配置…".into();
            self.worker.request(json!({"command":"start","config":config}));
        }
    }
    fn save(&mut self, start: bool) {
        self.error = None;
        match self.editor.begin(&self.selected, start) {
            Ok(()) => { self.message = "正在保存修改…".into(); self.send_next_save(); }
            Err(error) => { self.error = Some(error); }
        }
    }
    fn navigate(&mut self, task: String) {
        if task == self.task { return; }
        self.task = task;
        self.groups = Value::Null;
        self.parameter_search.clear();
        self.schema();
    }
    fn fields(&mut self, ui: &mut egui::Ui) {
        let mut groups: Vec<_> = self.groups.as_object().cloned().unwrap_or_default().into_iter().collect();
        groups.sort_by_key(|(name, _)| name == "scheduler");
        let query = self.parameter_search.to_lowercase();
        let mut shown = 0;
        for (group, fields) in groups {
            let fields: Vec<_> = fields.as_array().into_iter().flatten().filter(|field| {
                if group == "scheduler" && field["name"] == "enable" { return false; }
                query.is_empty() || self.label(field["name"].as_str().unwrap_or("")).to_lowercase().contains(&query)
                    || self.label(&group).to_lowercase().contains(&query)
                    || help::description(&self.translations, field["name"].as_str().unwrap_or(""), field["description"].as_str()).to_lowercase().contains(&query)
            }).cloned().collect();
            if fields.is_empty() { continue; }
            shown += fields.len();
            egui::Frame::none().fill(Color32::WHITE).rounding(8.0)
                .stroke(egui::Stroke::new(1.0_f32, Color32::from_rgb(231, 228, 220)))
                .inner_margin(14.0).show(ui, |ui| {
                ui.set_min_width(ui.available_width());
                egui::CollapsingHeader::new(RichText::new(self.label(&group)).strong().size(16.0))
                    .id_salt((&self.selected, &self.task, &group)).default_open(group != "scheduler")
                    .open(if query.is_empty() { None } else { Some(true) }).show(ui, |ui| {
                    for field in &fields {
                        let key = field["name"].as_str().unwrap_or("");
                        let title = self.label(key);
                        let note = help::description(&self.translations, key, field["description"].as_str());
                        let id = self.key(&group, key);
                        let mut value = self.editor.value(&id).unwrap_or(&field["value"]).clone();
                        let changed = value != field["value"];
                        ui.push_id((&self.selected.clone(), &self.task.clone(), &group, key), |ui| {
                            ui.add_space(5.0);
                            ui.horizontal_wrapped(|ui| {
                                ui.label(RichText::new(&title).strong()).on_hover_text(&note);
                                if changed { ui.colored_label(ACCENT, "· 未保存"); }
                            });
                            if let Some(options) = field["enumEnum"].as_array() {
                                let current = value.as_str().map(str::to_string).unwrap_or_else(|| value.to_string());
                                egui::ComboBox::from_id_salt("value").width(300.0).selected_text(self.label(&current)).show_ui(ui, |ui| {
                                    for option in options {
                                        let text = option.as_str().map(str::to_string).unwrap_or_else(|| option.to_string());
                                        ui.selectable_value(&mut value, option.clone(), self.label(&text));
                                    }
                                });
                            } else if let Some(mut checked) = value.as_bool() {
                                ui.checkbox(&mut checked, if value == true { "已开启" } else { "已关闭" });
                                value = Value::Bool(checked);
                            } else {
                                let mut text = value.as_str().map(str::to_string).unwrap_or_else(|| value.to_string());
                                let width = ui.available_width().min(440.0);
                                if ui.add(egui::TextEdit::singleline(&mut text).desired_width(width).margin(egui::vec2(9.0, 7.0))).changed() {
                                    value = Value::String(text);
                                }
                            }
                            if self.show_help && !note.is_empty() {
                                if note.contains('\n') || note.chars().count() > 90 {
                                    let preview: String = note.lines().next().unwrap_or("").chars().take(38).collect();
                                    egui::CollapsingHeader::new(RichText::new(format!("说明：{preview}…")).size(13.0).color(MUTED))
                                        .id_salt("note").default_open(false).show(ui, |ui| {
                                        ui.add(egui::Label::new(RichText::new(&note).size(13.0).color(MUTED)).wrap());
                                    });
                                } else {
                                    ui.add(egui::Label::new(RichText::new(&note).size(13.0).color(MUTED)).wrap());
                                }
                            }
                            ui.add_space(9.0);
                        });
                        self.editor.change(id, Draft { value, kind:field["type"].as_str().unwrap_or("").into(), title }, &field["value"]);
                    }
                });
            });
            ui.add_space(10.0);
        }
        if shown == 0 && !query.is_empty() { ui.label("没有找到匹配的设置，试试其他关键词。"); }
    }
    fn visible_logs(&self) -> Vec<(String, bool)> {
        self.logs.iter().filter_map(|(config, text)| {
            if !self.show_all_logs && !config.is_empty() && config != &self.selected { return None; }
            let lower = text.to_lowercase();
            let warning = lower.contains("error") || lower.contains("warning") || text.contains("失败") || text.contains("异常");
            if self.errors_only && !warning { return None; }
            Some((if config.is_empty() { text.clone() } else { format!("[{config}] {text}") }, warning))
        }).collect()
    }
}

impl eframe::App for Desktop {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.receive();
        ctx.request_repaint_after(Duration::from_millis(100));
        if self.editable() && self.editor.count(&self.selected) > 0
            && ctx.input_mut(|input| input.consume_key(egui::Modifiers::CTRL, egui::Key::S)) { self.save(false); }
        egui::TopBottomPanel::top("toolbar").frame(egui::Frame::none().fill(Color32::WHITE)
            .inner_margin(egui::Margin::symmetric(20.0, 10.0))).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.add(egui::Image::new((self.icon.id(), egui::vec2(44.0, 44.0))));
                ui.vertical(|ui| { ui.label(RichText::new("肝肝鼠").size(22.0).strong()); ui.label(RichText::new("任务控制台").size(12.0).color(MUTED)); });
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.menu_button("更多", |ui| {
                        ui.label(format!("版本 {}", self.revision)); ui.separator();
                        if self.editor.total() > 0 { ui.label("请先保存或撤销未保存的修改"); }
                        if ui.add_enabled(!self.busy() && self.editor.total() == 0, egui::Button::new("更新软件"))
                            .on_hover_text("有未保存的修改时，请先保存或撤销").clicked() { self.confirm_update = true; ui.close_menu(); }
                        if ui.add_enabled(!self.busy(), egui::Button::new("重新连接" )).clicked() {
                            self.online = false; self.states.clear(); self.pending_action = Some("reconnect".into());
                            self.message = "正在重新连接…".into(); let _ = self.worker.tx.send(Work::Restart); ui.close_menu();
                        }
                    });
                    if self.updating { ui.spinner(); ui.label("正在更新"); }
                    else { ui.colored_label(if self.online { Color32::from_rgb(40, 125, 85) } else { MUTED },
                        if self.online { "● 已连接" } else if self.error.is_some() { "○ 未连接" } else { "○ 正在连接" }); }
                });
            });
        });
        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            if let Some(error) = self.error.clone() {
                ui.horizontal_wrapped(|ui| {
                    ui.colored_label(Color32::from_rgb(175, 54, 43), error);
                    if ui.small_button("关闭提示").clicked() { self.error = None; }
                });
            }
            ui.label(RichText::new(&self.message).size(12.0).color(MUTED));
        });
        egui::TopBottomPanel::bottom(if self.show_logs { "logs_open" } else { "logs_closed" })
            .resizable(self.show_logs).default_height(if self.show_logs { 190.0 } else { 34.0 })
            .min_height(if self.show_logs { 90.0 } else { 34.0 }).show(ctx, |ui| {
            ui.horizontal_wrapped(|ui| {
                if ui.selectable_label(self.show_logs, if self.show_logs { "收起运行日志" } else { "展开运行日志" }).clicked() { self.show_logs = !self.show_logs; }
                if self.show_logs {
                    ui.checkbox(&mut self.show_all_logs, "所有配置"); ui.checkbox(&mut self.errors_only, "只看异常"); ui.checkbox(&mut self.follow_logs, "跟随最新");
                    if ui.small_button("复制").clicked() { ctx.copy_text(self.visible_logs().into_iter().map(|(line, _)| line).collect::<Vec<_>>().join("\n")); self.message = "日志已复制".into(); }
                    if ui.small_button("清空显示").clicked() { self.logs.clear(); }
                }
            });
            if self.show_logs {
                egui::ScrollArea::vertical().stick_to_bottom(self.follow_logs).show(ui, |ui| {
                    for (line, warning) in self.visible_logs() {
                        ui.add(egui::Label::new(RichText::new(line).monospace().size(12.0)
                            .color(if warning { Color32::from_rgb(165, 73, 27) } else { MUTED })).wrap());
                    }
                });
            }
        });
        egui::TopBottomPanel::bottom("changes").frame(egui::Frame::none().fill(Color32::WHITE).inner_margin(12.0)).show(ctx, |ui| {
            let count = self.editor.count(&self.selected);
            ui.horizontal_wrapped(|ui| {
                ui.label(if count == 0 { "配置已保存".into() } else { format!("{count} 项待保存 · 切换页面会保留修改") });
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.add_enabled(self.editable() && count > 0, egui::Button::new("保存全部修改  Ctrl+S").fill(Color32::from_rgb(243, 226, 193))).clicked() { self.save(false); }
                    if ui.add_enabled(!self.busy() && count > 0, egui::Button::new("撤销修改")).clicked() { self.editor.discard(&self.selected); }
                    if self.editor.busy() { ui.spinner(); }
                });
            });
        });
        egui::SidePanel::left("navigation").exact_width(250.0).resizable(false)
            .frame(egui::Frame::none().fill(Color32::WHITE).inner_margin(16.0)).show(ctx, |ui| {
            ui.label(RichText::new("配置").size(13.0).color(MUTED));
            let old = self.selected.clone();
            ui.add_enabled_ui(!self.busy(), |ui| {
                egui::ComboBox::from_id_salt("config").selected_text(if self.selected.is_empty() { "选择配置" } else { &self.selected })
                    .width(210.0).show_ui(ui, |ui| {
                    for name in &self.configs {
                        let title = if self.editor.count(name) > 0 { format!("{name} · 未保存") } else { name.clone() };
                        ui.selectable_value(&mut self.selected, name.clone(), title);
                    }
                });
            });
            ui.horizontal(|ui| {
                if ui.add_enabled(self.editable(), egui::Button::new("新增配置")).clicked() { self.action("create"); }
                if ui.add_enabled(self.editable() && !self.selected.is_empty(), egui::Button::new("删除")).clicked() {
                    self.deleting = true; self.action("delete");
                }
            });
            if old != self.selected { self.groups = Value::Null; self.schema(); }
            ui.add_space(12.0);
            if ui.selectable_label(self.task == "Script", "设备与脚本设置").clicked() && !self.busy() { self.navigate("Script".into()); }
            ui.separator();
            ui.add(egui::TextEdit::singleline(&mut self.search).hint_text("查找任务").desired_width(210.0));
            ui.add_space(6.0);
            egui::ScrollArea::vertical().id_salt("tasks").show(ui, |ui| {
                ui.spacing_mut().button_padding = egui::vec2(10.0, 4.0);
                ui.spacing_mut().item_spacing.y = 4.0;
                let menu = self.menu.as_object().cloned().unwrap_or_default();
                let query = self.search.to_lowercase();
                let mut matches = 0;
                ui.add_enabled_ui(!self.busy(), |ui| {
                    for (category, tasks) in menu {
                        let tasks: Vec<_> = tasks.as_array().into_iter().flatten().filter_map(Value::as_str)
                            .filter(|task| self.label(task).to_lowercase().contains(&query) || task.to_lowercase().contains(&query))
                            .map(str::to_string).collect();
                        if tasks.is_empty() { continue; }
                        matches += tasks.len();
                        egui::CollapsingHeader::new(self.label(&category)).default_open(true)
                            .open(if query.is_empty() { None } else { Some(true) }).show(ui, |ui| {
                            for task in tasks {
                                if ui.selectable_label(self.task == task, self.label(&task)).clicked() { self.navigate(task); }
                            }
                        });
                    }
                });
                if matches == 0 && !query.is_empty() { ui.label("未找到任务"); }
            });
        });
        egui::CentralPanel::default().frame(egui::Frame::none().fill(BACKGROUND).inner_margin(20.0)).show(ctx, |ui| {
            if self.selected.is_empty() {
                ui.add_space(50.0); ui.heading("从一个配置开始");
                ui.label("先添加配置，设置设备并启用需要的任务。"); ui.add_space(12.0);
                if ui.add_enabled(self.editable(), egui::Button::new("创建第一个配置")).clicked() { self.action("create"); }
                return;
            }
            let running = self.states.get(&self.selected).copied().unwrap_or(0) != 0;
            egui::Frame::none().fill(Color32::WHITE).rounding(8.0).inner_margin(14.0).show(ui, |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new(&self.selected).size(19.0).strong());
                    ui.colored_label(if running { Color32::from_rgb(40, 125, 85) } else { MUTED }, if running { "运行中" } else { "已停止" });
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if self.pending_action.is_some() { ui.spinner(); }
                        if running {
                            if ui.add_enabled(self.editable(), egui::Button::new("停止配置")).clicked() { self.action("stop"); }
                        } else if ui.add_enabled(self.editable(), egui::Button::new(if self.editor.count(&self.selected) > 0 { "保存并启动配置" } else { "启动配置" })
                            .fill(Color32::from_rgb(243, 226, 193))).clicked() { self.save(true); }
                    });
                });
                ui.label(RichText::new("启动配置会按调度运行已启用的任务。可在下方逐项设置。" ).size(12.0).color(MUTED));
            });
            if self.restart_required { ui.colored_label(ACCENT, "软件已更新，请关闭并重新打开以使用新版本。"); }
            ui.add_space(18.0);
            ui.horizontal_wrapped(|ui| {
                ui.heading(self.label(&self.task));
                if let Some(field) = self.groups["scheduler"].as_array().and_then(|fields| fields.iter().find(|f| f["name"] == "enable")).cloned() {
                    let key = self.key("scheduler", "enable");
                    let mut checked = self.editor.value(&key).unwrap_or(&field["value"]).as_bool().unwrap_or(false);
                    if ui.add_enabled(self.editable(), egui::Checkbox::new(&mut checked, "启用此任务")).changed() {
                        self.editor.change(key, Draft { value:json!(checked), kind:"boolean".into(), title:"启用此任务".into() }, &field["value"]);
                    }
                }
            });
            let note = help::description(&self.translations, &self.task, None);
            if self.show_help && !note.is_empty() { ui.add(egui::Label::new(RichText::new(note).size(13.0).color(MUTED)).wrap()); }
            ui.add_space(10.0);
            ui.horizontal_wrapped(|ui| {
                ui.scope(|ui| {
                    ui.visuals_mut().extreme_bg_color = Color32::WHITE;
                    ui.add(egui::TextEdit::singleline(&mut self.parameter_search).hint_text("查找当前任务的设置")
                        .desired_width(280.0).margin(egui::vec2(9.0, 7.0)));
                });
                ui.checkbox(&mut self.show_help, "显示说明");
            });
            ui.add_space(12.0);
            if self.groups.is_null() { ui.horizontal(|ui| { ui.spinner(); ui.label("正在加载设置…"); }); }
            else {
                ui.add_enabled_ui(self.editable(), |ui| {
                    egui::ScrollArea::vertical().id_salt((&self.selected, &self.task, "settings")).show(ui, |ui| { self.fields(ui); });
                });
            }
        });
        if self.confirm_update {
            egui::Window::new("更新软件").collapsible(false).resizable(false).show(ctx, |ui| {
                ui.label("从肝肝鼠仓库获取更新，完成后重新打开软件。");
                ui.label("更新会停止当前任务，配置和日志保留。"); ui.add_space(8.0);
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
