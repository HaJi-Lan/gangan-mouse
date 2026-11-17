//! PPOCR CTC decoding: blank removal, consecutive-token collapse and mean confidence.
use serde_json::{json, Value};

pub fn decode(request: &Value, bytes: &[u8]) -> Result<Value, String> {
    let shape = request["shape"].as_array().ok_or("missing CTC shape")?;
    let dims = shape.iter().map(|v| v.as_u64().and_then(|n| usize::try_from(n).ok())
        .ok_or("invalid CTC dimension")).collect::<Result<Vec<_>, _>>()?;
    if dims.len() != 3 { return Err("CTC requires batch, time, classes".into()); }
    let (batch, steps, classes) = (dims[0], dims[1], dims[2]);
    if classes == 0 || batch.checked_mul(steps).and_then(|n| n.checked_mul(classes))
        .and_then(|n| n.checked_mul(4)) != Some(bytes.len()) {
        return Err("invalid CTC payload".into());
    }
    let characters = request["characters"].as_array().ok_or("missing CTC dictionary")?;
    if characters.len() != classes || characters.iter().any(|c| !c.is_string()) {
        return Err("CTC dictionary size mismatch".into());
    }
    let mut mask = vec![true; classes];
    if !request["allowed"].is_null() {
        mask.fill(false);
        for index in request["allowed"].as_array().ok_or("invalid CTC whitelist")? {
            let index = index.as_u64().and_then(|n| usize::try_from(n).ok())
                .filter(|n| *n < classes).ok_or("invalid CTC whitelist index")?;
            mask[index] = true;
        }
    }
    let values: Vec<f32> = bytes.chunks_exact(4).map(|b| f32::from_le_bytes(b.try_into().unwrap())).collect();
    if values.iter().any(|v| !v.is_finite()) { return Err("non-finite CTC probability".into()); }
    let mut results = Vec::with_capacity(batch);
    for row in values.chunks(steps.checked_mul(classes).filter(|n| *n > 0).unwrap_or(1)) {
        let mut text = String::new();
        let (mut previous, mut sum, mut count) = (usize::MAX, 0.0f64, 0usize);
        for frame in row.chunks_exact(classes) {
            let (mut index, mut score) = (0, if mask[0] { frame[0] } else { 0.0 });
            for c in 1..classes {
                let value = if mask[c] { frame[c] } else { 0.0 };
                if value > score { index = c; score = value; }
            }
            if index != 0 && index != previous {
                text.push_str(characters[index].as_str().unwrap());
                sum += score as f64;
                count += 1;
            }
            previous = index;
        }
        results.push(json!({"text":text, "score":if count == 0 {None} else {Some((sum/count as f64) as f32)}}));
    }
    // Empty time axes still produce one empty result per batch, like NumPy's decoder.
    if steps == 0 { results = (0..batch).map(|_| json!({"text":"","score":null})).collect(); }
    Ok(json!(results))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blank_separates_repeated_characters_and_ties_keep_first_class() {
        let values: Vec<f32> = vec![0.0,1.0,0.0, 0.0,1.0,0.0, 1.0,0.0,0.0, 0.0,1.0,0.0, 0.0,0.5,0.5];
        let bytes: Vec<u8> = values.iter().flat_map(|v| v.to_le_bytes()).collect();
        let request = json!({"shape":[1,5,3],"characters":["blank","八","/"]});
        assert_eq!(decode(&request,&bytes).unwrap()[0]["text"], "八八");
    }

    #[test]
    fn invalid_dictionary_and_tensor_are_rejected() {
        assert!(decode(&json!({"shape":[1,1,2],"characters":["blank"]}),&[0;8]).is_err());
        assert!(decode(&json!({"shape":[1,1,2],"characters":["blank","8"]}),&[]).is_err());
    }
}
