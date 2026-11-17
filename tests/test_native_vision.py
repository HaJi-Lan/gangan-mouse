"""Compare complete native template score maps and RuleImage calls against OpenCV."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bridge'))
from native_backend import NativeClient


@unittest.skipUnless(os.environ.get('GANGAN_TEST_NATIVE'),'requires the freshly built native worker')
class NativeVisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=NativeClient(os.environ['GANGAN_TEST_NATIVE'])

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def compare(self,source,template,mask=None):
        expected=cv2.matchTemplate(source,template,cv2.TM_CCOEFF_NORMED,mask=mask)
        actual=self.client.match_map(source,template,mask)
        a=np.nan_to_num(actual,nan=-1,posinf=-1,neginf=-1)
        b=np.nan_to_num(expected,nan=-1,posinf=-1,neginf=-1)
        np.testing.assert_allclose(a,b,atol=0.0001,rtol=0.0001)
        self.assertEqual(cv2.minMaxLoc(a)[3],cv2.minMaxLoc(b)[3])
        return actual

    def test_small_gray_rgb_and_binary_masks(self):
        rng=np.random.default_rng(315)
        for channels in (1,3):
            shape=(29,41) if channels==1 else (29,41,3)
            source=rng.integers(0,256,shape,dtype=np.uint8)
            template=source[9:19,17:29].copy()
            for mask in (None,rng.choice(np.array([0,1,17,255],np.uint8),(10,12))):
                with self.subTest(channels=channels,masked=mask is not None):
                    self.compare(source,template,mask)
                    self.compare(source,rng.integers(0,256,template.shape,dtype=np.uint8),mask)

    def test_fft_rgb_gray_mask_and_search_edges(self):
        rng=np.random.default_rng(316)
        for channels in (1,3):
            shape=(121,163) if channels==1 else (121,163,3)
            source=rng.integers(0,256,shape,dtype=np.uint8)
            template=source[-19:,-23:].copy()
            for mask in (None,rng.choice(np.array([0,255],np.uint8),(19,23))):
                with self.subTest(channels=channels,masked=mask is not None):
                    self.compare(source,template,mask)

    def test_flat_regions_and_empty_masks(self):
        rng=np.random.default_rng(317)
        source=rng.integers(0,256,(33,45,3),dtype=np.uint8)
        source[:20,:20]=127
        template=rng.integers(0,256,(7,9,3),dtype=np.uint8)
        self.compare(source,template)
        self.compare(source,template,np.ones((7,9),np.uint8))
        self.compare(source,np.full((7,9,3),127,np.uint8))
        actual=self.client.match_map(source,template,np.zeros((7,9),np.uint8))
        self.assertTrue(np.isnan(actual).all())

    def test_large_gray_rgb_mask_and_search_edge_coordinates(self):
        rng=np.random.default_rng(320)
        for channels in (1,3):
            shape=(531,611) if channels==1 else (531,611,3)
            source=rng.integers(0,256,shape,dtype=np.uint8)
            for y,x in ((231,484),(512,588)):
                template=source[y:y+19,x:x+23].copy()
                for mask in (None,rng.integers(0,2,(19,23),dtype=np.uint8)):
                    with self.subTest(channels=channels,y=y,x=x,masked=mask is not None):
                        actual=self.compare(source,template,mask)
                        self.assertEqual(cv2.minMaxLoc(actual)[3],(x,y))

    def test_low_contrast_templates_and_windows_keep_double_precision_scores(self):
        rng=np.random.default_rng(324)
        source=rng.integers(126,129,(121,163,3),dtype=np.uint8)
        template=source[-19:,-23:].copy()
        actual=self.client.match_map(source,template)
        for y,x in ((0,0),(41,87),(102,140)):
            a=source[y:y+19,x:x+23].astype(np.float64)
            b=template.astype(np.float64)
            a-=a.mean((0,1));b-=b.mean((0,1))
            expected=np.sum(a*b)/np.sqrt(np.sum(a*a)*np.sum(b*b))
            self.assertAlmostEqual(actual[y,x],expected,delta=0.000001)
        # A high-contrast template may still search almost uniform source windows.
        source[:80,:100]=127
        source[27,31,0]=128
        template=rng.integers(0,256,(19,23,3),dtype=np.uint8)
        actual=self.client.match_map(source,template)
        for y,x in ((10,10),(20,20),(27,31)):
            a=source[y:y+19,x:x+23].astype(np.float64)
            b=template.astype(np.float64)
            a-=a.mean((0,1));b-=b.mean((0,1))
            expected=np.sum(a*b)/np.sqrt(np.sum(a*a)*np.sum(b*b))
            self.assertAlmostEqual(actual[y,x],expected,delta=0.000001)

    def test_fft_template_spanning_a_side(self):
        rng=np.random.default_rng(321)
        source=rng.integers(0,256,(83,167,3),dtype=np.uint8)
        for template in (source[:,31:133].copy(),source[21:73,:].copy()):
            self.compare(source,template)
            self.compare(source,template,np.ones(template.shape[:2],np.uint8))

    def test_direct_rgb_gray_dot_product_tails_and_small_searches(self):
        rng=np.random.default_rng(325)
        for channels in (1,3):
            for width in (5,6,7,8,13):
                shape=(17,width+4) if channels==1 else (17,width+4,3)
                source=rng.integers(0,256,shape,dtype=np.uint8)
                template=source[2:15,2:width+2].copy()
                with self.subTest(channels=channels,width=width):
                    self.compare(source,template)

    def test_fft_mask_does_not_turn_constant_background_into_a_match(self):
        rng=np.random.default_rng(319)
        source=rng.integers(0,256,(121,163,3),dtype=np.uint8)
        template=source[-19:,-23:].copy()
        source[:60,:70]=127
        mask=rng.choice(np.array([0,255],np.uint8),(19,23))
        actual=self.client.match_map(source,template,mask)
        self.assertTrue(np.isnan(actual[:42,:48]).all())
        clean=np.nan_to_num(actual,nan=-1,posinf=-1,neginf=-1)
        self.assertEqual(cv2.minMaxLoc(clean)[3],(140,102))
        self.assertAlmostEqual(float(clean[102,140]),1.0,delta=0.0001)

    def test_existing_full_game_frame(self):
        frame=cv2.imread(str(ROOT/'tests/fixtures/exploration/reward-stuck.png'))
        self.assertIsNotNone(frame)
        # The real frame covers constant backgrounds as well as UI artwork.
        template=frame[172:192,455:485].copy()
        actual=self.client.match_map(frame,template)
        expected=cv2.matchTemplate(frame,template,cv2.TM_CCOEFF_NORMED)
        # OpenCV's float correlation loses precision in nearly constant patches.
        # Check those against the double-precision formula, not its rounding noise.
        diff=np.abs(actual-expected)
        y,x=np.unravel_index(np.argmax(diff),diff.shape)
        a=frame[y:y+template.shape[0],x:x+template.shape[1]].astype(np.float64)
        b=template.astype(np.float64)
        a-=a.mean(axis=(0,1));b-=b.mean(axis=(0,1))
        exact=np.sum(a*b)/np.sqrt(np.sum(a*a)*np.sum(b*b))
        self.assertAlmostEqual(actual[y,x],exact,delta=0.000001)
        np.testing.assert_allclose(actual,expected,atol=0.003,rtol=0)
        candidates=(actual>0.5)|(expected>0.5)
        np.testing.assert_allclose(actual[candidates],expected[candidates],atol=0.0001,rtol=0.0001)
        self.assertEqual(cv2.minMaxLoc(actual)[3],cv2.minMaxLoc(expected)[3])

    def test_rule_image_mask_multiscale_and_multiple_matches_use_rust(self):
        code='''
import sys
sys.path.insert(0,sys.argv[1])
import numpy as np
from module.atom.image import RuleImage
from native_backend import install_native_backend,NativeClient
rng=np.random.default_rng(318)
image=rng.integers(0,256,(75,99,3),dtype=np.uint8)
template=image[17:33,23:43].copy()
image[48:64,64:84]=template
def rule(method):
    r=RuleImage((0,0,20,16),(0,0,99,75),method,0.99,'native-rule.png')
    r.roi_back=[0,0,99,75]
    r._image=template.copy()
    if method=='Masked template matching':
        r._mask_loaded=True
        r._mask=np.ones((16,20),np.uint8)
        r._mask[3:6,7:11]=0
    return r
def results():
    out=[]
    for method in ('Template matching','Masked template matching','Multi-scale template matching'):
        r=rule(method)
        ok=r.match_multi_scale(image,scales=[0.8,1.0,1.2]) if method.startswith('Multi') else r.match(image)
        out.append((ok,r.roi_front.copy(),r.last_score,r.last_scale))
    r=rule('Template matching')
    out.append([(x,y,w,h) for x,y,w,h,score in r.match_all(image)])
    out.append([(x,y,w,h) for x,y,w,h,score in r.match_all_any(image)])
    return out
expected=results()
calls=[]
original=NativeClient.match_map
def observed(self,*args,**kwargs):
    calls.append(True)
    return original(self,*args,**kwargs)
NativeClient.match_map=observed
install_native_backend()
actual=results()
for a,b in zip(actual[:3],expected[:3]):
    assert a[:2]==b[:2],(a,b)
    assert abs(a[2]-b[2])<0.0001,(a,b)
    assert a[3]==b[3],(a,b)
assert actual[3:]==expected[3:],(actual,expected)
assert len(calls)>=7,len(calls)
'''
        env=dict(os.environ,GANGAN_NATIVE_EXE=os.environ['GANGAN_TEST_NATIVE'],GANGAN_NATIVE_MODE='rust')
        result=subprocess.run([sys.executable,'-c',code,str(ROOT/'bridge')],cwd=ROOT/'runtime/upstream',
            env=env,capture_output=True,text=True,encoding='utf-8',timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
