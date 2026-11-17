"""Run PPOCR normalization, inference, box filtering and CTC in Rust; keep resize/contours compatible."""
from pathlib import Path
from types import SimpleNamespace
from functools import cached_property
import os


def uses_native_ocr(deploy):
    if not os.environ.get('GANGAN_NATIVE_EXE') or os.environ.get('GANGAN_NATIVE_MODE', 'rust') != 'rust':
        return False
    address = str(deploy.OcrClientAddress or '127.0.0.1:22268').removeprefix('tcp://')
    return not deploy.UseOcrServer or address.split(':')[0] in ('127.0.0.1', 'localhost')


class NativePredictor:
    def __init__(self, client, model):
        self.client, self.model = client, str(model)

    def run(self, outputs, inputs):
        import numpy as np
        tensor = next(iter(inputs.values())).astype('<f4', copy=False)
        reply, data = self.client.request('ocr_infer', tensor.tobytes(),
                                          model=self.model, shape=list(tensor.shape))
        return [np.frombuffer(data, dtype='<f4').reshape(reply['shape']).copy()]

    def run_images(self, images, padded_width, kind):
        import numpy as np
        reply, data = self.client.request('ocr_image_infer', b''.join(image.tobytes() for image in images),
            model=self.model, widths=[image.shape[1] for image in images], height=images[0].shape[0],
            padded_width=padded_width, kind=kind)
        return np.frombuffer(data, dtype='<f4').reshape(reply['shape']).copy()


class NativeCTC:
    def __init__(self, client, reference):
        self.client, self.reference = client, reference

    def __getattr__(self, key):
        return getattr(self.reference, key)

    def __call__(self, predictions, label=None, *args, **kwargs):
        import numpy as np
        # The training-label API and non-finite inputs retain their Python behavior.
        if label is not None or predictions.dtype != np.float32 or not np.isfinite(predictions).all():
            return self.reference(predictions, label, *args, **kwargs)
        mask = self.reference.char_mask
        reply, _ = self.client.request('ctc_decode', predictions.astype('<f4', copy=False).tobytes(),
            shape=list(predictions.shape), characters=self.reference.character,
            allowed=None if mask is None else np.flatnonzero(mask).tolist())
        return [(r['text'], float('nan') if r['score'] is None else r['score']) for r in reply['results']]


class NativeTextSystem:
    def __init__(self, client):
        import ppocronnx
        self.client = client
        self.models = Path(ppocronnx.__file__).parent/'model'
        self.use_angle_cls = False

    @cached_property
    def text_recognizer(self):
        from native_preprocess import NativeTextRecognizer
        from ppocronnx.rec.rec_decoder import CTCLabelDecode
        recognizer = NativeTextRecognizer.__new__(NativeTextRecognizer)
        recognizer.rec_image_shape = [3,32,320]
        recognizer.character_type = 'ch'
        recognizer.rec_batch_num = 6
        recognizer.rec_algorithm = 'CRNN'
        dictionary = (self.models/'ppocr_keys_v1.txt').read_text('utf-8').splitlines()
        decoder = CTCLabelDecode(character_dict=dictionary, character_type='ch', use_space_char=True)
        recognizer.postprocess_op = NativeCTC(self.client, decoder)
        recognizer.predictor = NativePredictor(self.client, self.models/'ch_PP-OCRv2_rec_infer.onnx')
        recognizer.input_tensor = SimpleNamespace(name='input')
        recognizer.output_tensors = None
        return recognizer

    @cached_property
    def text_detector(self):
        from native_preprocess import NativeTextDetector
        from ppocronnx.det.preprocess import preprocess_op
        from ppocronnx.det.postprocess import DBPostProcess
        detector = NativeTextDetector.__new__(NativeTextDetector)
        detector.det_algorithm = 'DB'
        detector.box_thresh = 0.6
        detector.unclip_ratio = 1.6
        detector.preprocess_op = preprocess_op
        detector.postprocess_op = DBPostProcess(thresh=0.3, max_candidates=1000, use_dilation=True)
        detector.predictor = NativePredictor(self.client, self.models/'ch_PP-OCRv3_det_infer.onnx')
        detector.input_tensor = SimpleNamespace(name='input')
        detector.output_tensors = None
        return detector

    def set_char_whitelist(self, chars):
        self.text_recognizer.set_char_whitelist(chars)

    def ocr_lines(self, images):
        from ppocronnx.predict_system import TextSystem
        return TextSystem.ocr_lines(self, images)

    def ocr_single_line(self, image):
        result = self.ocr_lines([image])
        return result[0] if result else None

    def detect_and_ocr(self, image, **kwargs):
        from ppocronnx.predict_system import TextSystem
        return TextSystem.detect_and_ocr(self, image, **kwargs)


def install_native_ocr(client, mode):
    from module.ocr import models, base_ocr
    from module.server.setting import State
    from module.logger import logger
    original = models.get_ocr_model
    native = NativeTextSystem(client)

    class Comparison:
        def __getattr__(self, key):
            return getattr(native, key)

        def set_char_whitelist(self, chars):
            native.set_char_whitelist(chars)
            original('ch').set_char_whitelist(chars)

        def ocr_single_line(self, image):
            result = native.ocr_single_line(image)
            reference = original('ch').ocr_single_line(image)
            if result[0] != reference[0] or abs(result[1]-reference[1]) > 0.002:
                logger.warning(f'Rust OCR comparison: rust={result}, python={reference}')
            return reference

        def detect_and_ocr(self, image, **kwargs):
            result = native.detect_and_ocr(image, **kwargs)
            reference = original('ch').detect_and_ocr(image, **kwargs)
            if [r.ocr_text for r in result] != [r.ocr_text for r in reference]:
                logger.warning('Rust OCR detection comparison: results differ')
            return reference

    comparison = Comparison()

    def provider(lang='ch'):
        if lang == 'ch' and uses_native_ocr(State.deploy_config):
            return native
        if lang == 'ch' and mode == 'compare':
            return comparison
        return original(lang)

    models.get_ocr_model = provider
    base_ocr.get_ocr_model = provider
