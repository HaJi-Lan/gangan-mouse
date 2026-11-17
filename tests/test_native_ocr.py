"""Compare native ONNX inference against the existing ONNX Runtime reference."""
import os
from pathlib import Path
import sys
import unittest
from itertools import permutations

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'bridge'))
from native_backend import NativeClient
from native_ocr import NativeTextSystem, NativePredictor, NativeCTC
from native_preprocess import normalized, NativeTextDetector, NativeTextRecognizer


@unittest.skipUnless(os.environ.get('GANGAN_TEST_NATIVE'), 'requires a freshly built native OCR worker')
class NativeOcrTests(unittest.TestCase):
    def test_normalization_layout_all_byte_values_and_zero_padding(self):
        from ppocronnx.det.preprocess import NormalizeImage
        client=NativeClient(os.environ['GANGAN_TEST_NATIVE'])
        try:
            values=np.arange(256,dtype=np.uint8)
            image=np.stack((values,values[::-1],np.roll(values,71)),axis=-1).reshape(8,32,3)
            expected=((image.astype(np.float32).transpose(2,0,1)/255)-0.5)/0.5
            actual=normalized(client,image[:,::2],20,'recognition')
            np.testing.assert_array_equal(actual[:,:,:16],expected[:,:,::2])
            np.testing.assert_array_equal(actual[:,:,16:],0)
            operation=NormalizeImage(scale='1./255.',order='hwc')
            reference=operation({'image':image})['image'].transpose(2,0,1)
            np.testing.assert_array_equal(normalized(client,image,32,'detection'),reference)
            with self.assertRaisesRegex(RuntimeError,'dimensions'):
                normalized(client,image,31,'recognition')
            self.assertTrue(client.request('ping')[0]['ok'])
        finally:client.close()

    def test_detector_box_order_clipping_small_and_empty_results(self):
        from ppocronnx.det import TextDetector
        client=NativeClient(os.environ['GANGAN_TEST_NATIVE'])
        native=NativeTextDetector.__new__(NativeTextDetector)
        native.predictor=NativePredictor(client,'unused.onnx')
        reference=TextDetector.__new__(TextDetector)
        rng=np.random.default_rng(322)
        boxes=list(permutations(((-3.2,-7.1),(38.9,-7.1),(38.9,45.8),(-3.2,45.8))))
        boxes+=list(rng.uniform(-10,200,(50,4,2)))
        boxes+=[((1,1),(4.9,1),(4.9,20),(1,20)),((1,1),(20,1),(20,4.9),(1,4.9))]
        try:
            for source in (np.array(boxes,np.float32),np.empty((0,4,2),np.float32),
                    np.array([boxes[-1]],np.float32),np.array(boxes,np.float64)):
                expected=reference.filter_tag_det_res(source.copy(),(79,81,3))
                actual=native.filter_tag_det_res(source.copy(),(79,81,3))
                np.testing.assert_array_equal(actual,expected)
                self.assertEqual(actual.dtype,expected.dtype)
        finally:client.close()

    def test_recognizer_preprocessing_matches_reference_for_varied_shapes(self):
        from ppocronnx.rec import TextRecognizer
        client=NativeClient(os.environ['GANGAN_TEST_NATIVE'])
        native=NativeTextSystem(client).text_recognizer
        reference=TextRecognizer.__new__(TextRecognizer)
        reference.rec_image_shape=[3,32,320]
        reference.character_type='ch'
        rng=np.random.default_rng(323)
        try:
            self.assertIsInstance(native,NativeTextRecognizer)
            for height,width in ((1,3),(37,79),(7,3),(32,120),(54,3)):
                image=rng.integers(0,256,(height,width,3),dtype=np.uint8)
                for ratio in (width/height,width/height+1):
                    actual=native.resize_norm_img(image,ratio)
                    expected=reference.resize_norm_img(image,ratio)
                    np.testing.assert_array_equal(actual,expected)
        finally:client.close()

    def test_fused_image_inference_matches_float_tensor_inference_for_batches(self):
        import ppocronnx
        from ppocronnx.rec import TextRecognizer
        client=NativeClient(os.environ['GANGAN_TEST_NATIVE'])
        reference=TextRecognizer()
        native=NativeTextSystem(client).text_recognizer
        try:
            images=[]
            for name in ('ten-tickets.png','full-tickets.png','eight-tickets.png'):
                image=cv2.imread(str(ROOT/'tests/fixtures/scrolls'/name))[14:48,26:104]
                images.append(cv2.cvtColor(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),cv2.COLOR_GRAY2BGR))
            images[1]=np.pad(images[1],((0,0),(4,7),(0,0)),mode='edge')
            # More than one batch, mixed/repeated aspect ratios and restored input order.
            images=(images*3)[:7]
            calls=[]
            request=client.request
            def observed(command,payload=b'',**fields):
                calls.append(command)
                return request(command,payload,**fields)
            client.request=observed
            actual,_=native(images)
            expected,_=reference(images)
            self.assertEqual(calls.count('ocr_image_infer'),2)
            self.assertNotIn('ocr_normalize',calls)
            self.assertNotIn('ocr_infer',calls)
            for a,b in zip(actual,expected):
                self.assertEqual(a[0],b[0])
                self.assertAlmostEqual(a[1],b[1],delta=0.002)
            self.assertEqual(native([])[0],[])
        finally:client.close()

    def test_native_ctc_blank_duplicates_whitelist_and_batch(self):
        from ppocronnx.rec.rec_decoder import CTCLabelDecode
        client=NativeClient(os.environ['GANGAN_TEST_NATIVE'])
        reference=CTCLabelDecode(character_dict=['8','/','中'],character_type='ch',use_space_char=True)
        native=NativeCTC(client,reference)
        try:
            values=np.zeros((2,8,5),np.float32)
            for row,tokens in enumerate(([1,1,0,1,2,2,3,0],[0,0,0,0,0,0,0,0])):
                for i,token in enumerate(tokens):values[row,i,token]=0.5+i*0.05
            for whitelist in (None,'8/','中',''):
                native.set_char_mask(whitelist)
                actual=native(values.copy())
                expected=reference(values.copy())
                for (a,score),(b,ref_score) in zip(actual,expected):
                    self.assertEqual(a,b)
                    if np.isnan(ref_score):self.assertTrue(np.isnan(score))
                    else:self.assertAlmostEqual(score,ref_score,delta=0.000001)
            native.set_char_mask(None)
            ties=np.array([[[0,.5,.5,0,0]]],np.float32)
            self.assertEqual(native(ties.copy())[0][0],reference(ties.copy())[0][0])
            self.assertEqual(native(np.zeros((0,2,5),np.float32)),[])
            self.assertEqual([t for t,_ in native(np.zeros((2,0,5),np.float32))],['',''])
        finally:client.close()

    def test_complete_text_and_detection_paths_match_python(self):
        sys.path.insert(0,str(ROOT/'runtime/upstream'))
        from module.ocr.ppocr import TextSystem
        reference = TextSystem()
        client = NativeClient(os.environ['GANGAN_TEST_NATIVE'])
        native = NativeTextSystem(client)
        try:
            images = []
            for name in ('eight-tickets.png','ten-tickets.png','full-tickets.png'):
                image = cv2.imread(str(ROOT/'tests/fixtures/scrolls'/name))[14:48,26:104]
                images.append(cv2.cvtColor(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),cv2.COLOR_GRAY2BGR))
            expected = reference.ocr_lines(images)
            actual = native.ocr_lines(images)
            self.assertIsInstance(native.text_recognizer.predictor, NativePredictor)
            for (text,score),(expected_text,expected_score) in zip(actual,expected):
                self.assertEqual(text,expected_text)
                self.assertAlmostEqual(score,expected_score,delta=0.002)
            native.set_char_whitelist('0123456789/')
            reference.set_char_whitelist('0123456789/')
            self.assertEqual(native.ocr_single_line(images[0])[0],reference.ocr_single_line(images[0])[0])
            frame = cv2.imread(str(ROOT/'tests/fixtures/scrolls/eight-tickets.png'))
            actual = native.detect_and_ocr(frame)
            expected = reference.detect_and_ocr(frame)
            self.assertIsInstance(native.text_detector.predictor, NativePredictor)
            self.assertGreater(len(expected),0)
            self.assertEqual([r.ocr_text for r in actual],[r.ocr_text for r in expected])
            for a,b in zip(actual,expected):
                np.testing.assert_allclose(a.box,b.box,atol=1)
                self.assertAlmostEqual(a.score,b.score,delta=0.002)
        finally:
            client.close()

    def test_existing_recognition_model_matches_python_outputs(self):
        import ppocronnx
        from ppocronnx.rec import TextRecognizer
        model = Path(ppocronnx.__file__).parent/'model/ch_PP-OCRv2_rec_infer.onnx'
        reference = TextRecognizer()
        client = NativeClient(os.environ['GANGAN_TEST_NATIVE'])
        try:
            for name, expected_text in (('eight-tickets.png','8/30'),('ten-tickets.png','10/30'),('full-tickets.png','30/30')):
                image = cv2.imread(str(ROOT/'tests/fixtures/scrolls'/name))
                image = image[14:48,26:104]
                image = cv2.cvtColor(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),cv2.COLOR_GRAY2BGR)
                h,w = image.shape[:2]
                tensor = reference.resize_norm_img(image,w/h)[None].copy()
                expected = reference.predictor.run(None,{reference.input_tensor.name:tensor})[0]
                response,data = client.request('ocr_infer',tensor.astype('<f4').tobytes(),
                    model=str(model),shape=list(tensor.shape))
                actual = np.frombuffer(data,dtype='<f4').reshape(response['shape']).copy()
                np.testing.assert_allclose(actual,expected,rtol=0.002,atol=0.0002)
                native_text,native_score = reference.postprocess_op(actual)[0]
                text,score = reference.postprocess_op(expected)[0]
                self.assertEqual(native_text,text)
                self.assertEqual(native_text,expected_text)
                self.assertAlmostEqual(native_score,score,delta=0.002)
        finally:
            client.close()
