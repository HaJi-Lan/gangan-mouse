import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bridge'))
sys.path.insert(0, str(ROOT))
from diagnostics import export_logs, save_counter_frame, save_counter_failure
from module.device.control import Control


class DiagnosticsTests(unittest.TestCase):
    def test_failed_frame_template_comparison_and_export(self):
        import os
        import cv2
        import numpy as np
        from module.atom.image import RuleImage
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = Path.cwd()
            try:
                os.chdir(root)
                image = np.random.default_rng(1).integers(0, 255, (100, 150, 3), dtype=np.uint8)
                cv2.imwrite('entrance.png', cv2.cvtColor(image[10:30, 20:50], cv2.COLOR_RGB2BGR))
                rule = RuleImage(roi_front=(20, 10, 30, 20), roi_back=(10, 0, 60, 50),
                                 threshold=.8, method='Template matching', file='entrance.png')
                task = NS(config=NS(config_name='oas1'), device=NS(image=image),
                          I_E_EXPLORATION_CLICK=rule, I_EXP_CREATE_TEAM=rule)
                path = Path(save_counter_failure(task, 'counter', image[:20, :40], '', .3, (0, 0, 40, 20)))
                import json
                report = json.loads((path / 'comparison.json').read_text('utf-8'))
                self.assertTrue(report['template_comparisons'][0]['matched'])
                self.assertGreater(report['template_comparisons'][0]['score'], .99)
                self.assertTrue((path / 'screen.png').is_file())
                result = export_logs(root, root, 'oas1')
                with zipfile.ZipFile(result['path']) as bundle:
                    self.assertTrue(any(n.endswith('/comparison.json') for n in bundle.namelist()))
                    self.assertTrue(any(n.endswith('/screen.png') for n in bundle.namelist()))
            finally:
                os.chdir(previous)

    def test_export_contains_only_selected_recent_logs_and_crops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'runtime/upstream'
            logs = runtime / 'log'
            logs.mkdir(parents=True)
            (logs / '2026-11-15_oas1.txt').write_text('INFO start\nWARNING bad OCR\n突破券 8/30', encoding='utf-8')
            (logs / '2026-11-15_oas2.txt').write_text('other account', encoding='utf-8')
            (runtime / 'config').mkdir()
            (runtime / 'config/oas1.json').write_text('private settings')
            (logs / 'ocr/oas1').mkdir(parents=True)
            (logs / 'ocr/oas1/sample.png').write_bytes(b'image')
            result = export_logs(runtime, root, 'oas1')
            with zipfile.ZipFile(result['path']) as bundle:
                self.assertIsNone(bundle.testzip())
                self.assertIn('ocr/sample.png', bundle.namelist())
                self.assertIn('WARNING bad OCR', bundle.read('异常与识别摘要.txt').decode())
                self.assertFalse(any('oas2' in n or 'config/' in n for n in bundle.namelist()))
            with self.assertRaises(ValueError):
                export_logs(runtime, root, '../oas1')

    def test_clicks_wait_for_device_interval_before_dispatch(self):
        method = Mock()
        device = NS(config=NS(script=NS(device=NS(control_method='minitouch'))),
                    click_methods={'minitouch': method}, click_adb=Mock(), handle_control_check=Mock())
        with patch('module.device.control.monotonic', side_effect=[0, 0, .1, 1]), \
             patch('module.device.control.uniform', return_value=1), \
             patch('module.device.control.sleep') as sleep:
            Control.click(device, 1, 2)
            Control.click(device, 3, 4)
            sleep.assert_called_once_with(.9)
        self.assertEqual(method.call_count, 2)


if __name__ == '__main__':
    unittest.main()
