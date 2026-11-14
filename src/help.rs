use serde_json::Value;

fn translated<'a>(translations: &'a Value, key: &str) -> Option<&'a str> {
    translations.get(key).and_then(Value::as_str)
}

/// Schema descriptions can be translation keys or literal text. An explicit
/// empty translation means no note, rather than displaying its internal key.
pub fn description(translations: &Value, name: &str, schema_description: Option<&str>) -> String {
    let fallback = format!("{name}_help");
    let text = schema_description.and_then(|description| {
        if description.trim().is_empty() { return None; }
        translated(translations, description).or_else(|| {
            if description.trim_end_matches('_').ends_with("_help") { None } else { Some(description) }
        })
    }).or_else(|| translated(translations, &fallback)).unwrap_or("");
    text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn resolves_the_schema_key_instead_of_guessing_from_field_name() {
        let t = json!({"user_status_help_": "选择队长或队员", "user_status_help": "other"});
        assert_eq!(description(&t, "user_status", Some("user_status_help_")), "选择队长或队员");
    }
    #[test]
    fn displays_literal_notes_and_both_newline_formats() {
        assert_eq!(description(&json!({}), "example", Some("第一行\\n第二行\r\n第三行")), "第一行\n第二行\n第三行");
    }
    #[test]
    fn missing_keys_fall_back_and_empty_notes_remain_empty() {
        let t = json!({"serial_help": "设备说明", "blank_help": ""});
        assert_eq!(description(&t, "serial", Some("missing_help")), "设备说明");
        assert_eq!(description(&t, "unknown", Some("missing_help_")), "");
        assert_eq!(description(&t, "serial", Some("blank_help")), "");
    }
}
