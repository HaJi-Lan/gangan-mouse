"""Run this fork's desktop checks without collecting upstream pytest suites."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ('test_bridge.py', 'test_exploration_scrolls.py',
            'test_scrolls_cycle.py', 'test_scrolls_supervisor.py')

if __name__ == '__main__':
    suite = unittest.TestSuite()
    for pattern in PATTERNS:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=pattern))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
