//! Normalized cross correlation for byte images and binary masks.
//! Small searches use a direct kernel; large searches use f64 FFT correlation.
use rustfft::{num_complex::Complex, FftPlanner};

pub struct Frame<'a> {
    pub pixels: &'a [u8],
    pub width: usize,
    pub height: usize,
    pub channels: usize,
}

impl<'a> Frame<'a> {
    pub fn new(pixels: &'a [u8], width: usize, height: usize, channels: usize) -> Result<Self,String> {
        if !matches!(channels,1|3) || width == 0 || height == 0
            || width.checked_mul(height).and_then(|n| n.checked_mul(channels)) != Some(pixels.len()) {
            return Err("invalid template image dimensions".into());
        }
        Ok(Self {pixels,width,height,channels})
    }
}

pub struct Matcher {
    planner:FftPlanner<f64>,
}

impl Default for Matcher {
    fn default()->Self {Self {planner:FftPlanner::new()}}
}

impl Matcher {
    pub fn map(&mut self, source: Frame<'_>, template: Frame<'_>, mask: Option<&[u8]>) -> Result<Vec<f32>,String> {
        let (w,h,tw,th,c) = (source.width,source.height,template.width,template.height,source.channels);
        if template.channels != c || tw>w || th>h || mask.is_some_and(|m| m.len()!=tw*th) {
            return Err("template or mask dimensions do not match".into());
        }
        let count = (0..tw*th).filter(|i| mask.is_none_or(|m|m[*i]!=0)).count();
        let out_len = (w-tw+1)*(h-th+1);
        if count == 0 { return Ok(vec![f32::NAN;out_len]); }
        let mut mean = [0.0;3];
        for (i,pixel) in template.pixels.chunks_exact(c).enumerate() {
            if mask.is_none_or(|m|m[i]!=0) {
                for channel in 0..c { mean[channel]+=pixel[channel] as f64; }
            }
        }
        for v in &mut mean { *v/=count as f64; }
        let centered: Vec<f64> = template.pixels.iter().enumerate().map(|(i,v)| {
            if mask.is_none_or(|m|m[i/c]!=0) { *v as f64-mean[i%c] } else {0.0}
        }).collect();
        let variance: f64 = centered.iter().map(|v|v*v).sum();
        if variance <= f64::EPSILON {
            return Ok(vec![if mask.is_some() {f32::NAN} else {1.0};out_len]);
        }
        if out_len.saturating_mul(count).saturating_mul(c) <= 4_000_000 {
            return Ok(direct(&source,&template,mask,&centered,count as f64,variance));
        }
        self.fft(&source,&template,mask,&centered,count as f64,variance)
    }

    fn transform(&mut self,buffer:&mut [Complex<f64>],w:usize,h:usize,inverse:bool) {
        let rows=if inverse {self.planner.plan_fft_inverse(w)} else {self.planner.plan_fft_forward(w)};
        let columns=if inverse {self.planner.plan_fft_inverse(h)} else {self.planner.plan_fft_forward(h)};
        // Reuse one scratch allocation across all rows/columns of this transform.
        let mut scratch=vec![Complex::default();rows.get_inplace_scratch_len().max(columns.get_inplace_scratch_len())];
        for row in buffer.chunks_exact_mut(w) {rows.process_with_scratch(row,&mut scratch);}
        let mut column=vec![Complex::default();h];
        for x in 0..w {
            for y in 0..h {column[y]=buffer[y*w+x];}
            columns.process_with_scratch(&mut column,&mut scratch);
            for y in 0..h {buffer[y*w+x]=column[y];}
        }
    }

    fn extract(&mut self, mut spectrum:Vec<Complex<f64>>,aw:usize,ah:usize,w:usize,h:usize,tw:usize,th:usize)->Vec<f64> {
        self.transform(&mut spectrum,aw,ah,true);
        let scale = (aw*ah) as f64;
        let mut out=Vec::with_capacity((w-tw+1)*(h-th+1));
        for y in 0..=h-th { for x in 0..=w-tw {
            out.push(spectrum[(y+th-1)*aw+x+tw-1].re/scale);
        }}
        out
    }

    fn fft(&mut self,s:&Frame<'_>,t:&Frame<'_>,mask:Option<&[u8]>,centered:&[f64],count:f64,variance:f64)->Result<Vec<f32>,String> {
        let (w,h,tw,th,c)=(s.width,s.height,t.width,t.height,s.channels);
        let (aw,ah)=(fft_size(w+tw-1),fft_size(h+th-1));
        let len=aw.checked_mul(ah).filter(|n|*n<=16_777_216).ok_or("template FFT exceeds image workspace")?;
        let out_len=(w-tw+1)*(h-th+1);
        let mut numerator=vec![Complex::default();len];
        let mut stats=vec![[0.0;4];out_len];
        let mut mask_spectrum=mask.map(|m| {
            let mut spectrum=vec![Complex::default();len];
            for y in 0..th {for x in 0..tw {
                spectrum[y*aw+x].re=if m[(th-1-y)*tw+tw-1-x]!=0 {1.0} else {0.0};
            }}
            self.transform(&mut spectrum,aw,ah,false);
            spectrum
        });
        for channel in 0..c {
            let mut source=vec![Complex::default();len];
            for y in 0..h {for x in 0..w {source[y*aw+x].re=s.pixels[(y*w+x)*c+channel] as f64;}}
            // Pack two real transforms into one complex transform.
            for y in 0..th {for x in 0..tw {source[y*aw+x].im=centered[((th-1-y)*tw+tw-1-x)*c+channel];}}
            self.transform(&mut source,aw,ah,false);
            let mut means=mask_spectrum.as_ref().map(|_|vec![Complex::default();len]);
            for y in 0..ah {for x in 0..aw {
                let i=y*aw+x;
                let mirror=(if y==0 {0} else {ah-y})*aw+if x==0 {0} else {aw-x};
                let a=source[i];let b=source[mirror].conj();
                numerator[i]+=(a+b)*(a-b)*Complex::new(0.0,-0.25);
                if let Some(ref mut values)=means {values[i]=(a+b)*0.5;}
            }}
            if let Some(m)=mask_spectrum.as_ref() {
                let mut means=means.unwrap();
                for i in 0..len {means[i]*=m[i];}
                for (i,value) in self.extract(means,aw,ah,w,h,tw,th).into_iter().enumerate() {stats[i][channel]=value;}
            }
        }
        if let Some(m)=mask_spectrum.take() {
            let mut squares=vec![Complex::default();len];
            for y in 0..h {for x in 0..w {
                squares[y*aw+x].re=s.pixels[(y*w+x)*c..(y*w+x+1)*c].iter().map(|v|(*v as f64).powi(2)).sum();
            }}
            self.transform(&mut squares,aw,ah,false);
            for i in 0..len {squares[i]*=m[i];}
            for (i,value) in self.extract(squares,aw,ah,w,h,tw,th).into_iter().enumerate() {stats[i][3]=value;}
        } else {
            stats=plain_stats(s,tw,th);
        }
        let numerator=self.extract(numerator,aw,ah,w,h,tw,th);
        Ok(numerator.iter().zip(stats).map(|(dot,stat)| score(*dot,stat,count,variance,mask.is_some())).collect())
    }

}

fn plain_stats(s:&Frame<'_>,tw:usize,th:usize)->Vec<[f64;4]> {
    let (w,h,c)=(s.width,s.height,s.channels);
    let stride=w+1;
    let mut integral=vec![[0.0;4];stride*(h+1)];
    for y in 0..h {
        let mut row=[0.0;4];
        for x in 0..w {
            for channel in 0..c {
                let v=s.pixels[(y*w+x)*c+channel] as f64;
                row[channel]+=v; row[3]+=v*v;
            }
            for channel in 0..4 {integral[(y+1)*stride+x+1][channel]=row[channel]+integral[y*stride+x+1][channel];}
        }
    }
    let mut result=Vec::with_capacity((w-tw+1)*(h-th+1));
    for y in 0..=h-th {for x in 0..=w-tw {
        let mut stat=[0.0;4];
        for channel in 0..4 {
            stat[channel]=integral[(y+th)*stride+x+tw][channel]-integral[y*stride+x+tw][channel]
                -integral[(y+th)*stride+x][channel]+integral[y*stride+x][channel];
        }
        result.push(stat);
    }}
    result
}

// Smooth FFT lengths avoid padding a 1280x720 search to a 2048x1024 grid.
fn fft_size(minimum:usize)->usize {
    let mut value=minimum;
    loop {
        let mut rest=value;
        for prime in [2,3,5] {while rest>1 && rest%prime==0 {rest/=prime;}}
        if rest==1 {return value;}
        value+=1;
    }
}

fn score(dot:f64,stats:[f64;4],count:f64,variance:f64,masked:bool)->f32 {
    let source_variance=(stats[3]-stats[..3].iter().map(|v|v*v/count).sum::<f64>()).max(0.0);
    let roundoff=(64.0*f64::EPSILON*stats[3].abs()).max(1e-7);
    if source_variance<=roundoff {return if masked {f32::NAN} else {0.0};}
    (dot/(variance*source_variance).sqrt()).clamp(-1.0,1.0) as f32
}

fn direct(s:&Frame<'_>,t:&Frame<'_>,mask:Option<&[u8]>,centered:&[f64],count:f64,variance:f64)->Vec<f32> {
    if mask.is_none() {
        let stats=plain_stats(s,t.width,t.height);
        let row_len=t.width*s.channels;
        let mut result=Vec::with_capacity(stats.len());
        for y in 0..=s.height-t.height {for x in 0..=s.width-t.width {
            let mut sums=[0.0;4];
            for ty in 0..t.height {
                let start=((y+ty)*s.width+x)*s.channels;
                let a=&s.pixels[start..start+row_len];
                let b=&centered[ty*row_len..(ty+1)*row_len];
                let mut src=a.chunks_exact(4);let mut tpl=b.chunks_exact(4);
                for (a,b) in src.by_ref().zip(tpl.by_ref()) {
                    sums[0]+=a[0] as f64*b[0];sums[1]+=a[1] as f64*b[1];
                    sums[2]+=a[2] as f64*b[2];sums[3]+=a[3] as f64*b[3];
                }
                for (a,b) in src.remainder().iter().zip(tpl.remainder()) {sums[0]+=*a as f64*b;}
            }
            let stat=stats[y*(s.width-t.width+1)+x];
            result.push(score(sums.iter().sum(),stat,count,variance,false));
        }}
        return result;
    }
    let mut out=Vec::with_capacity((s.width-t.width+1)*(s.height-t.height+1));
    for y in 0..=s.height-t.height {for x in 0..=s.width-t.width {
        let (mut stats,mut dot)=([0.0;4],0.0);
        for ty in 0..t.height {for tx in 0..t.width {
            let pixel=ty*t.width+tx;
            if mask.is_some_and(|m|m[pixel]==0) {continue;}
            let start=((y+ty)*s.width+x+tx)*s.channels;
            for channel in 0..s.channels {
                let v=s.pixels[start+channel] as f64;
                stats[channel]+=v;stats[3]+=v*v;dot+=v*centered[pixel*s.channels+channel];
            }
        }}
        out.push(score(dot,stats,count,variance,mask.is_some()));
    }}
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn masked_pixels_do_not_affect_matching() {
        let src=[1,2,100, 3,4,200];
        let tpl=[1,2,0,3,4,0];
        let result=Matcher::default().map(Frame::new(&src,3,2,1).unwrap(),Frame::new(&tpl,3,2,1).unwrap(),Some(&[1,1,0,1,1,0])).unwrap();
        assert!((result[0]-1.0).abs()<1e-6);
    }
    #[test]
    fn flat_and_empty_masks_keep_opencv_semantics() {
        let src=[1,2,3,4];let tpl=[7,7];
        let mut matcher=Matcher::default();
        assert_eq!(matcher.map(Frame::new(&src,2,2,1).unwrap(),Frame::new(&tpl,2,1,1).unwrap(),None).unwrap(),vec![1.0,1.0]);
        assert!(matcher.map(Frame::new(&src,2,2,1).unwrap(),Frame::new(&tpl,2,1,1).unwrap(),Some(&[0,0])).unwrap().iter().all(|v|v.is_nan()));
    }
}
