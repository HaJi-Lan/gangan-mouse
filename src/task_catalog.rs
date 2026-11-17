//! Phase one of the native task engine: task ownership and admission.
//! Game actions remain in the Python executor during the migration.
use serde_json::{json, Value};
use std::collections::HashSet;

pub const SOURCE: &str = include_str!("../assets/task-catalog.json");
pub const ENVIRONMENT: &str = "GANGAN_TASK_CATALOG";

pub struct TaskCatalog {
    keys: HashSet<String>,
}

impl TaskCatalog {
    pub fn bundled() -> Self {
        let menu: Value = serde_json::from_str(SOURCE).expect("invalid bundled task catalog");
        let mut keys = HashSet::new();
        for tasks in menu.as_object().expect("task catalog must be an object").values() {
            for task in tasks.as_array().expect("task category must be an array") {
                let task = task.as_str().expect("task name must be a string");
                assert!(keys.insert(config_key(task)), "duplicate task in catalog: {task}");
            }
        }
        Self { keys }
    }

    pub fn allows(&self, task: &str) -> bool {
        self.keys.contains(&config_key(task))
    }

    /// Reject settings requests before they reach the legacy executor.
    /// Return the normal protocol envelope so the UI can finish its pending action.
    pub fn rejection(&self, request: &Value) -> Option<Value> {
        if !matches!(request["command"].as_str(), Some("schema" | "set")) {
            return None;
        }
        if request["task"].as_str().is_some_and(|task| self.allows(task)) {
            return None;
        }
        Some(json!({"event":"response", "command":request["command"],
            "id":request["id"], "ok":false, "error":"此任务不在当前版本的迁移范围内"}))
    }
}

// Keep configuration names compatible with upstream convert_to_underscore.
fn config_key(task: &str) -> String {
    if task.contains('_') { return task.to_owned(); }
    let chars: Vec<char> = task.chars().filter(|c| *c != ' ').collect();
    let mut key = String::new();
    for (i, &c) in chars.iter().enumerate() {
        let before = i.checked_sub(1).map(|index| chars[index]);
        let after = chars.get(i + 1).copied();
        if (c.is_ascii_uppercase() && before.is_some_and(|p| p.is_ascii_lowercase()
                || after.is_some_and(|n| n.is_ascii_lowercase())))
            || (c.is_ascii_digit() && before.is_some_and(|p| p.is_ascii_alphabetic()
                || after.is_some_and(|n| n.is_ascii_alphabetic()))) {
            key.push('_');
        }
        key.push(c.to_ascii_lowercase());
    }
    key
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_scope_includes_guild_and_scroll_tasks_only_once() {
        let catalog = TaskCatalog::bundled();
        assert_eq!(catalog.keys.len(), 33);
        for task in ["Hunt", "RyouToppa", "GuildActivityMonitor", "MemoryScrolls", "RealmRaid"] {
            assert!(catalog.allows(task));
            assert!(catalog.allows(&config_key(task)));
        }
        let menu: Value = serde_json::from_str(SOURCE).unwrap();
        assert!(menu["Liver Emperor Exclusive"].as_array().unwrap().contains(&json!("RealmRaid")));
        assert!(!menu["Guild"].as_array().unwrap().contains(&json!("RealmRaid")));
    }

    #[test]
    fn removed_and_new_upstream_tasks_are_not_admitted() {
        let catalog = TaskCatalog::bundled();
        for task in ["DailyTrifles", "WeeklyTrifles", "HeroTest", "FindJade", "FloatParade", "FutureUpstreamTask"] {
            assert!(!catalog.allows(task));
            let rejected = catalog.rejection(&json!({"command":"set", "task":task, "id":17})).unwrap();
            assert_eq!(rejected["id"], 17);
            assert_eq!(rejected["ok"], false);
        }
        assert!(catalog.rejection(&json!({"command":"schema", "task":"Hunt"})).is_none());
        assert!(catalog.rejection(&json!({"command":"stop", "config":"oas1"})).is_none());
    }

    #[test]
    fn names_match_upstream_configuration_keys() {
        for (name, key) in [("RealmRaid", "realm_raid"), ("GuildActivityMonitor", "guild_activity_monitor"),
                ("OCRTask", "ocr_task"), ("Task2", "task_2"), ("realm_raid", "realm_raid")] {
            assert_eq!(config_key(name), key);
        }
    }
}
