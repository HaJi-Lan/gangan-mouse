use serde_json::Value;
use std::{collections::HashSet, time::Instant};

#[derive(Clone, Default)]
pub struct Execution {
    pub task: String,
    pub owner: String,
    pub started: Option<Instant>,
    schedule: Value,
}

impl Execution {
    pub fn apply(&mut self, data: &Value) {
        if let Some(execution) = data.get("execution") {
            let task = execution["task"].as_str().unwrap_or("");
            let owner = execution["owner"].as_str().unwrap_or("");
            if task.is_empty() { self.started = None; }
            else if task != self.task || owner != self.owner || self.started.is_none() { self.started = Some(Instant::now()); }
            self.task = task.into(); self.owner = owner.into();
        }
        if let Some(schedule) = data.get("schedule") { self.schedule = schedule.clone(); }
    }
    pub fn upcoming(&self) -> Vec<(String, String)> {
        let mut tasks = vec![];
        let mut seen = HashSet::new();
        seen.insert(self.task.clone());
        seen.insert(self.owner.clone());
        let running = &self.schedule["running"];
        if let Some(name) = running["name"].as_str().filter(|name| !name.is_empty()) {
            if seen.insert(name.to_string()) { tasks.push((name.into(), "等待执行".into())); }
        }
        for (group, status) in [("pending", "就绪"), ("waiting", "")] {
            for item in self.schedule[group].as_array().into_iter().flatten() {
                if let Some(name) = item["name"].as_str().filter(|name| !name.is_empty()) {
                    if seen.insert(name.to_string()) {
                        tasks.push((name.into(), if status.is_empty() { item["next_run"].as_str().unwrap_or("等待调度").into() } else { status.into() }));
                    }
                }
            }
        }
        tasks
    }
    pub fn elapsed(&self) -> String {
        let seconds = self.started.map(|s| s.elapsed().as_secs()).unwrap_or(0);
        if seconds >= 3600 { format!("{:02}:{:02}:{:02}", seconds / 3600, seconds / 60 % 60, seconds % 60) }
        else { format!("{:02}:{:02}", seconds / 60, seconds % 60) }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn scheduler_candidates_are_not_reported_as_running_tasks() {
        let mut e = Execution::default();
        e.apply(&json!({"schedule":{"running":{"name":"Orochi"},"pending":[],"waiting":[]}}));
        assert!(e.task.is_empty()); assert!(e.started.is_none());
        assert_eq!(e.upcoming()[0].0, "Orochi");
    }
    #[test]
    fn live_phases_and_waits_are_distinct_and_schedule_updates_keep_the_clock() {
        let mut e = Execution::default();
        e.apply(&json!({"execution":{"task":"Exploration","owner":"MemoryScrolls"}}));
        let clock = e.started;
        e.apply(&json!({"schedule":{"pending":[]}}));
        assert_eq!(e.started, clock);
        e.apply(&json!({"execution":{"task":"","owner":"MemoryScrolls"}}));
        assert!(e.task.is_empty()); assert_eq!(e.owner, "MemoryScrolls"); assert!(e.started.is_none());
        e.apply(&json!({"execution":{"task":"RealmRaid","owner":"MemoryScrolls"}}));
        assert_eq!(e.task, "RealmRaid"); assert!(e.started.is_some());
        e.apply(&json!({"execution":{"task":"","owner":""}}));
        assert!(e.owner.is_empty()); assert!(e.started.is_none());
    }
    #[test]
    fn queue_deduplicates_tasks_and_excludes_the_actual_running_phase() {
        let mut e = Execution::default();
        e.apply(&json!({"execution":{"task":"Orochi","owner":"Orochi"},"schedule":{
            "running":{"name":"Orochi"},"pending":[{"name":"Exploration"},{"name":"Orochi"}],
            "waiting":[{"name":"Exploration"},{"name":"RealmRaid","next_run":"2026-11-14 18:00:00"}]}}));
        assert_eq!(e.upcoming(), vec![("Exploration".into(),"就绪".into()),("RealmRaid".into(),"2026-11-14 18:00:00".into())]);
        e.apply(&json!({"execution":{"task":"Exploration","owner":"MemoryScrolls"},
            "schedule":{"running":{"name":"MemoryScrolls"},"pending":[{"name":"Orochi"}]}}));
        assert_eq!(e.upcoming(), vec![("Orochi".into(), "就绪".into())]);
    }
}
