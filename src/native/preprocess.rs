//! PPOCR input normalization and final DB quadrilateral filtering.
//! Resizing and contour/unclip operations keep their Python reference paths.
use super::vision::Frame;

pub fn normalize(frame:Frame<'_>,padded_width:usize,kind:&str)->Result<Vec<f32>,String> {
    if frame.channels!=3 || padded_width<frame.width
        || frame.height.checked_mul(padded_width).and_then(|n|n.checked_mul(3))
            .filter(|n|*n<=16_777_216).is_none() {
        return Err("invalid OCR normalization dimensions".into());
    }
    let detection=match kind {
        "recognition"=>false,"detection"=>true,
        _=>return Err("unsupported OCR normalization".into()),
    };
    let plane=frame.height*padded_width;
    let mut result=vec![0.0;3*plane];
    let means=[0.485_f32,0.456,0.406];
    let deviations=[0.229_f32,0.224,0.225];
    for y in 0..frame.height {for x in 0..frame.width {for channel in 0..3 {
        let pixel=frame.pixels[(y*frame.width+x)*3+channel] as f32;
        result[channel*plane+y*padded_width+x]=if detection {
            (pixel*(1.0_f32/255.0)-means[channel])/deviations[channel]
        } else {
            (pixel/255.0-0.5)/0.5
        };
    }}}
    Ok(result)
}

pub fn filter_boxes(data:&[u8],width:usize,height:usize)->Result<Vec<f32>,String> {
    if width==0 || height==0 || data.len()%32!=0 {return Err("invalid OCR detection boxes".into());}
    let values:Vec<f32>=data.chunks_exact(4).map(|p|f32::from_le_bytes(p.try_into().unwrap())).collect();
    if values.iter().any(|v|!v.is_finite()) {return Err("non-finite OCR detection box".into());}
    let mut result=Vec::with_capacity(values.len());
    for source in values.chunks_exact(8) {
        let mut points=[[source[0],source[1]],[source[2],source[3]],[source[4],source[5]],[source[6],source[7]]];
        // Four-point argsort preserves original order for equal coordinates.
        points.sort_by(|a,b|a[0].partial_cmp(&b[0]).unwrap());
        points[..2].sort_by(|a,b|a[1].partial_cmp(&b[1]).unwrap());
        points[2..].sort_by(|a,b|a[1].partial_cmp(&b[1]).unwrap());
        let mut ordered=[points[0],points[2],points[3],points[1]];
        for point in &mut ordered {
            point[0]=point[0].clamp(0.0,(width-1) as f32).trunc();
            point[1]=point[1].clamp(0.0,(height-1) as f32).trunc();
        }
        let length=|a:[f32;2],b:[f32;2]| ((a[0]-b[0]).powi(2)+(a[1]-b[1]).powi(2)).sqrt() as usize;
        if length(ordered[0],ordered[1])>3 && length(ordered[0],ordered[3])>3 {
            result.extend(ordered.into_iter().flatten());
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalization_keeps_channel_order_and_zero_padding() {
        let pixels=[0,127,255,255,0,127];
        let frame=Frame::new(&pixels,2,1,3).unwrap();
        let values=normalize(frame,3,"recognition").unwrap();
        assert_eq!(values[0],-1.0); assert_eq!(values[1],1.0);
        assert_eq!(values[6],1.0);
        assert_eq!([values[2],values[5],values[8]],[0.0;3]);
    }

    #[test]
    fn detection_filter_orders_clips_and_rejects_small_boxes() {
        let values=[20_f32,20.9, -2.0,20.1, 20.0,-2.0, -2.0,-2.0, 0.0,0.0, 3.9,0.0, 3.9,9.0, 0.0,9.0];
        let bytes:Vec<u8>=values.iter().flat_map(|v|v.to_le_bytes()).collect();
        assert_eq!(filter_boxes(&bytes,15,15).unwrap(),vec![0.0,0.0,14.0,0.0,14.0,14.0,0.0,14.0]);
        assert!(filter_boxes(&bytes,0,15).is_err());
    }
}
