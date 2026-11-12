"""Run the explicit offline suite; legacy *_test.py includes live account operations."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
names = ["tests.media_key_decode_test", "tests.upload_features_test", "tests.test_prepared_upload", "tests.test_unified_core", "tests.test_snapshot_lock"]
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
