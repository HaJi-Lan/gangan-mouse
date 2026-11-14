use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Key {
    pub config: String,
    pub task: String,
    pub group: String,
    pub argument: String,
}

impl Key {
    pub fn new(config: &str, task: &str, group: &str, argument: &str) -> Self {
        let (task, group) = if task == "MemoryScrolls" {
            match group {
                "scrolls" | "exploration_config" | "invite_config" => ("Exploration", group),
                "cycle_exploration_battle" => ("Exploration", "general_battle_config"),
                "cycle_exploration_soul" => ("Exploration", "switch_soul_config"),
                "raid_config" => ("RealmRaid", "raid_config"),
                "cycle_raid_battle" => ("RealmRaid", "general_battle_config"),
                "cycle_raid_soul" => ("RealmRaid", "switch_soul_config"),
                _ => (task, group),
            }
        } else { (task, group) };
        Self { config:config.into(), task:task.into(), group:group.into(), argument:argument.into() }
    }
}

#[derive(Clone)]
pub struct Draft { pub value: Value, pub kind: String, pub title: String }

impl Draft {
    fn parsed(&self) -> Result<Value, String> {
        if self.kind != "integer" && self.kind != "number" { return Ok(self.value.clone()); }
        let number = if self.value.is_number() { Some(self.value.clone()) }
            else { serde_json::from_str::<Value>(self.value.as_str().unwrap_or("").trim()).ok() };
        match number {
            Some(v) if v.is_number() && (self.kind != "integer" || v.is_i64() || v.is_u64()) => Ok(v),
            _ => Err(format!("{}：请输入有效的{}", self.title, if self.kind == "integer" { "整数" } else { "数字" })),
        }
    }
}

#[derive(Default)]
pub struct Editor {
    drafts: BTreeMap<Key, Draft>,
    queue: VecDeque<(Key, Draft)>,
    active: Option<(String, Key, Draft)>,
    next_id: u64,
    start_after: Option<String>,
    pub saved: usize,
}

impl Editor {
    pub fn value(&self, key: &Key) -> Option<&Value> { self.drafts.get(key).map(|d| &d.value) }
    pub fn count(&self, config: &str) -> usize { self.drafts.keys().filter(|k| k.config == config).count() }
    pub fn total(&self) -> usize { self.drafts.len() }
    pub fn busy(&self) -> bool { self.active.is_some() || !self.queue.is_empty() }
    pub fn change(&mut self, key: Key, draft: Draft, original: &Value) {
        if draft.value == *original { self.drafts.remove(&key); }
        else { self.drafts.insert(key, draft); }
    }
    pub fn discard(&mut self, config: &str) { self.drafts.retain(|k, _| k.config != config); }
    pub fn begin(&mut self, config: &str, start: bool) -> Result<(), String> {
        if self.busy() { return Err("正在保存，请稍候。".into()); }
        let selected: Vec<_> = self.drafts.iter().filter(|(k, _)| k.config == config)
            .map(|(k, d)| (k.clone(), d.clone())).collect();
        for (_, draft) in &selected { draft.parsed()?; }
        self.queue = selected.into();
        self.saved = 0;
        self.start_after = start.then(|| config.to_string());
        Ok(())
    }
    pub fn next_request(&mut self) -> Option<Value> {
        if self.active.is_some() { return None; }
        let (key, draft) = self.queue.pop_front()?;
        self.next_id += 1;
        let id = format!("save-{}", self.next_id);
        let request = json!({"id":id,"command":"set","config":key.config,"task":key.task,
            "group":key.group,"argument":key.argument,"value":draft.parsed().expect("validated before saving")});
        self.active = Some((id, key, draft));
        Some(request)
    }
    pub fn acknowledge(&mut self, id: Option<&str>, ok: bool) -> bool {
        if self.active.as_ref().map(|(id, _, _)| id.as_str()) != id || id.is_none() { return false; }
        let (_, key, sent) = self.active.take().unwrap();
        if ok {
            if self.drafts.get(&key).is_some_and(|draft| draft.value == sent.value) { self.drafts.remove(&key); }
            self.saved += 1;
        } else { self.cancel(); }
        true
    }
    pub fn cancel(&mut self) { self.active = None; self.queue.clear(); self.start_after = None; }
    pub fn take_start(&mut self) -> Option<String> {
        if self.busy() { None } else { self.start_after.take() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn key(task: &str) -> Key { Key { config:"a".into(), task:task.into(), group:"group".into(), argument:"value".into() } }
    fn draft(value: Value) -> Draft { Draft { value, kind:"integer".into(), title:"次数".into() } }
    #[test]
    fn page_changes_and_one_ack_do_not_lose_other_edits() {
        let mut editor = Editor::default();
        for task in ["Exploration", "RealmRaid"] { editor.change(key(task), draft(json!("12")), &json!(0)); }
        assert_eq!(editor.count("a"), 2);
        editor.begin("a", true).unwrap();
        let request = editor.next_request().unwrap();
        assert_eq!(request["value"], 12);
        assert!(editor.take_start().is_none());
        assert!(editor.acknowledge(request["id"].as_str(), true));
        assert_eq!(editor.count("a"), 1);
        let request = editor.next_request().unwrap();
        assert!(editor.acknowledge(request["id"].as_str(), true));
        assert_eq!(editor.count("a"), 0);
        assert_eq!(editor.take_start().as_deref(), Some("a"));
    }
    #[test]
    fn failure_keeps_unsaved_values_and_never_starts() {
        let mut e = Editor::default();
        for task in ["a", "b"] { e.change(key(task), draft(json!("2")), &json!(0)); }
        e.begin("a", true).unwrap();
        let first = e.next_request().unwrap();
        assert!(!e.acknowledge(Some("stale"), true));
        assert!(e.acknowledge(first["id"].as_str(), true));
        let second = e.next_request().unwrap();
        assert!(e.acknowledge(second["id"].as_str(), false));
        assert_eq!(e.count("a"), 1);
        assert!(!e.busy());
        assert!(e.take_start().is_none());
    }
    #[test]
    fn invalid_number_prevents_all_writes_and_other_configs_are_untouched() {
        let mut e = Editor::default();
        e.change(key("a"), draft(json!("1.5")), &json!(0));
        let mut other = key("b"); other.config = "b".into();
        e.change(other, draft(json!(3)), &json!(0));
        assert!(e.begin("a", false).is_err());
        assert!(e.next_request().is_none());
        e.discard("a");
        assert_eq!(e.count("b"), 1);
    }
    #[test]
    fn old_ack_cannot_erase_a_newer_value() {
        let mut e = Editor::default();
        e.change(key("a"), draft(json!("2")), &json!(0));
        e.begin("a", false).unwrap(); let request = e.next_request().unwrap();
        e.change(key("a"), draft(json!("3")), &json!(0));
        e.acknowledge(request["id"].as_str(), true);
        assert_eq!(e.value(&key("a")), Some(&json!("3")));
    }
    #[test]
    fn collection_and_individual_pages_share_one_draft_and_last_edit_wins() {
        let collection = Key::new("a", "MemoryScrolls", "cycle_raid_battle", "limit_count");
        let individual = Key::new("a", "RealmRaid", "general_battle_config", "limit_count");
        assert_eq!(collection, individual);
        let mut e = Editor::default();
        e.change(collection, draft(json!("2")), &json!(0));
        e.change(individual, draft(json!("3")), &json!(0));
        assert_eq!(e.count("a"), 1);
        e.begin("a", false).unwrap();
        let request = e.next_request().unwrap();
        assert_eq!(request["task"], "RealmRaid");
        assert_eq!(request["group"], "general_battle_config");
        assert_eq!(request["value"], 3);
        assert_eq!(Key::new("a", "MemoryScrolls", "scrolls", "scrolls_enable"),
                   Key::new("a", "Exploration", "scrolls", "scrolls_enable"));
    }
}
