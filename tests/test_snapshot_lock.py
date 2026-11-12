"""A consumer rejects local divergence and can update without adjacent platforms."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gpmc_snapshot_tool", ROOT / "tools/sync_gpmc.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SnapshotLockTests(unittest.TestCase):
    def test_explicit_update_then_offline_check_and_drift_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            consumer = Path(temporary)
            source = consumer / "app/src/main/python"
            (source / "gpmc").mkdir(parents=True)
            (source / "gpmc/stale.py").write_text("obsolete\n")
            (source / "harbor_android.py").write_text("# platform adapter\n")
            assets = consumer / "app/src/main/assets"
            assets.mkdir(parents=True)
            (assets / "engine-extension.json").write_text(json.dumps({"files": []}))
            sync.synchronize(ROOT, consumer)
            self.assertFalse((source / "gpmc/stale.py").exists())
            first = sync.check(consumer)
            original = (source / "gpmc/api.py").read_bytes()
            # Checkout line endings do not create a different code version.
            (source / "gpmc/api.py").write_bytes(original.replace(b"\n", b"\r\n"))
            self.assertEqual(sync.check(consumer), first)
            (source / "gpmc/api.py").write_bytes(original + b"\n# local divergence\n")
            with self.assertRaisesRegex(ValueError, "snapshot changed"):
                sync.check(consumer)
            sync.synchronize(ROOT, consumer)
            self.assertEqual(sync.check(consumer), first)


if __name__ == "__main__":
    unittest.main()
