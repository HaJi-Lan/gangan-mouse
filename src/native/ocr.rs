//! Native ONNX inference using the same PPOCR weights as the Python reference.
use std::collections::VecDeque;
use tract_onnx::prelude::*;

type Plan = SimplePlan<TypedFact, Box<dyn TypedOp>, TypedModel>;

#[derive(Default)]
pub struct OcrEngine {
    plans: VecDeque<(String, Vec<usize>, Plan)>,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_tensor_is_rejected_before_loading_model() {
        let mut engine=OcrEngine::default();
        assert!(engine.infer("missing.onnx",vec![1,3,32,78],&[]).unwrap_err().contains("payload"));
        assert!(engine.infer("missing.onnx",vec![0,3,32,78],&[]).is_err());
        assert!(engine.plans.is_empty());
    }
}

impl OcrEngine {
    pub fn infer(&mut self, model: &str, shape: Vec<usize>, bytes: &[u8]) -> Result<(Vec<usize>,Vec<u8>),String> {
        let count = shape.iter().try_fold(1usize, |n,d| n.checked_mul(*d)).ok_or("invalid tensor shape")?;
        if shape.len()!=4 || count.checked_mul(4)!=Some(bytes.len()) || count==0 {
            return Err("invalid OCR tensor payload".into());
        }
        let values:Vec<f32>=bytes.chunks_exact(4).map(|b|f32::from_le_bytes(b.try_into().unwrap())).collect();
        self.infer_values(model,shape,&values)
    }

    pub fn infer_values(&mut self, model:&str, shape:Vec<usize>, values:&[f32])->Result<(Vec<usize>,Vec<u8>),String> {
        if shape.len()!=4 || shape.iter().try_fold(1usize,|n,d|n.checked_mul(*d))!=Some(values.len()) || values.is_empty() {
            return Err("invalid OCR tensor payload".into());
        }
        let key = self.plans.iter().position(|(name,dims,_)|name==model && dims==&shape);
        let index = if let Some(index)=key {index} else {
            let plan = tract_onnx::onnx().model_for_path(model)
                .and_then(|m|m.with_input_fact(0,InferenceFact::dt_shape(f32::datum_type(),shape.clone())))
                .and_then(|m|m.into_optimized()).and_then(|m|m.into_runnable())
                .map_err(|e|format!("ONNX model preparation failed: {e:#}"))?;
            // Tensor widths/batch sizes vary. Bound cached weight/plan memory.
            if self.plans.len()==4 { self.plans.pop_front(); }
            self.plans.push_back((model.to_owned(),shape.clone(),plan));
            self.plans.len()-1
        };
        let input=Tensor::from_shape(&shape,values).map_err(|e|e.to_string())?;
        let output=self.plans[index].2.run(tvec!(input.into())).map_err(|e|format!("ONNX inference failed: {e:#}"))?;
        let tensor=&output[0];
        let result=tensor.as_slice::<f32>().map_err(|e|e.to_string())?;
        Ok((tensor.shape().to_vec(),result.iter().flat_map(|v|v.to_le_bytes()).collect()))
    }
}
