# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
# Adapted from PPOCR's recognition batch and detection filter paths.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""PPOCR adapters: keep OpenCV resizing, normalize and filter DB boxes in Rust."""
import math
import time
import numpy as np
import cv2
from ppocronnx.rec import TextRecognizer
from ppocronnx.det import TextDetector


def supported_image(image):
    return image.dtype == np.uint8 and image.ndim == 3 and image.shape[2] == 3


def normalized(client, image, padded_width, kind):
    reply, data = client.request('ocr_normalize', image.tobytes(), width=image.shape[1],
        height=image.shape[0], padded_width=padded_width, kind=kind)
    return np.frombuffer(data, dtype='<f4').reshape(reply['shape']).copy()


class NativeTextRecognizer(TextRecognizer):
    def resized(self, image, max_wh_ratio):
        channels, height, width = self.rec_image_shape
        assert channels == image.shape[2]
        if self.character_type == 'ch':
            width = int(32 * max_wh_ratio)
        ratio = image.shape[1] / float(image.shape[0])
        resized_width = min(width, math.ceil(height * ratio))
        return cv2.resize(image, (resized_width, height)), width

    def resize_norm_img(self, image, max_wh_ratio):
        if not supported_image(image):
            return super().resize_norm_img(image, max_wh_ratio)
        resized, width = self.resized(image, max_wh_ratio)
        return normalized(self.predictor.client, resized, width, 'recognition')

    def __call__(self, images):
        if any(not supported_image(image) for image in images):
            return super().__call__(images)
        # Retain NumPy's original ordering for equal aspect ratios and batches.
        ratios = [image.shape[1] / float(image.shape[0]) for image in images]
        indices = np.argsort(np.array(ratios))
        results = [['', 0.0]] * len(images)
        elapsed = 0
        for begin in range(0, len(images), self.rec_batch_num):
            batch = indices[begin:begin + self.rec_batch_num]
            ratio = max(ratios[i] for i in batch)
            resized = [self.resized(images[i], ratio) for i in batch]
            start = time.time()
            # Send bytes once; the normalized float tensor stays inside Rust.
            predictions = self.predictor.run_images([image for image, _ in resized],
                resized[0][1], 'recognition')
            decoded = self.postprocess_op(predictions)
            for index, result in zip(batch, decoded):
                results[index] = result
            elapsed += time.time() - start
        return results, elapsed


class NativeTextDetector(TextDetector):
    def filter_tag_det_res(self, boxes, image_shape):
        values = np.asarray(boxes)
        if (values.size and (values.dtype != np.float32 or values.ndim != 3
                or values.shape[1:] != (4, 2) or not np.isfinite(values).all())):
            return super().filter_tag_det_res(boxes, image_shape)
        reply, data = self.predictor.client.request('ocr_filter_boxes', values.astype('<f4', copy=False).tobytes(),
            width=image_shape[1], height=image_shape[0])
        # The reference returns a one-dimensional float64 array for no boxes.
        if not reply['count']:
            return np.array([])
        return np.frombuffer(data, dtype='<f4').reshape(reply['count'], 4, 2).copy()

    def __call__(self, image, unclip_ratio=None, box_thresh=None):
        if not supported_image(image) or self.det_algorithm != 'DB':
            return super().__call__(image, unclip_ratio, box_thresh)
        ratio = self.unclip_ratio if unclip_ratio is None else unclip_ratio
        threshold = self.box_thresh if box_thresh is None else box_thresh
        # Reuse the installed resize operator, including its actual side-limit
        # configuration. Native normalization replaces only the following ops.
        data = self.preprocess_op[0]({'image': image})
        resized = data['image']
        start = time.time()
        predictions = self.predictor.run_images([resized], resized.shape[1], 'detection')
        post = self.postprocess_op({'maps': predictions}, data['shape'][None], ratio, threshold)
        boxes = self.filter_tag_det_res(post[0]['points'], image.shape)
        return boxes, time.time() - start
