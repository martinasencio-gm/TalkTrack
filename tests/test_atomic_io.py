# tests/test_atomic_io.py
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_text_creates_file(self):
        from app.utils.atomic_io import atomic_write_text
        path = self.dir / "out.txt"
        atomic_write_text(path, "hello")
        self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_write_text_replaces_existing(self):
        from app.utils.atomic_io import atomic_write_text
        path = self.dir / "out.txt"
        path.write_text("old", encoding="utf-8")
        atomic_write_text(path, "new")
        self.assertEqual(path.read_text(encoding="utf-8"), "new")

    def test_no_tmp_file_left_behind(self):
        from app.utils.atomic_io import atomic_write_text
        atomic_write_text(self.dir / "out.txt", "x")
        leftovers = [p.name for p in self.dir.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_write_json_round_trips(self):
        from app.utils.atomic_io import atomic_write_json
        path = self.dir / "out.json"
        data = {"segments": [{"text": "héllo", "start": 1.5}]}
        atomic_write_json(path, data, indent=2, ensure_ascii=False)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), data)

    def test_write_text_retries_transient_permission_error(self):
        # Windows holds transient locks on just-touched files (OneDrive
        # sync, Defender, indexer) — os.replace can fail with WinError 5
        # (PermissionError) for a moment even though nothing is actually
        # wrong. Retry should ride through it instead of surfacing an error.
        from app.utils import atomic_io
        path = self.dir / "out.txt"
        path.write_text("old", encoding="utf-8")

        real_replace = __import__("os").replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, "Access is denied")
            real_replace(src, dst)

        with unittest.mock.patch.object(atomic_io.os, "replace", side_effect=flaky_replace), \
             unittest.mock.patch.object(atomic_io.time, "sleep"):
            atomic_io.atomic_write_text(path, "new")

        self.assertEqual(path.read_text(encoding="utf-8"), "new")
        self.assertEqual(len(calls), 3)

    def test_write_text_gives_up_after_max_retries(self):
        from app.utils import atomic_io
        path = self.dir / "out.txt"

        with unittest.mock.patch.object(
            atomic_io.os, "replace",
            side_effect=PermissionError(5, "Access is denied"),
        ), unittest.mock.patch.object(atomic_io.time, "sleep"):
            with self.assertRaises(PermissionError):
                atomic_io.atomic_write_text(path, "new")

    def test_failed_write_leaves_no_tmp_file(self):
        # A dead .tmp beside the real file is confusing on inspection and
        # gets picked up by folder-syncing tools. Observed for real next to
        # settings.json after a failed save.
        from app.utils import atomic_io
        path = self.dir / "out.txt"

        with unittest.mock.patch.object(
            atomic_io.os, "replace",
            side_effect=PermissionError(5, "Access is denied"),
        ), unittest.mock.patch.object(atomic_io.time, "sleep"):
            with self.assertRaises(PermissionError):
                atomic_io.atomic_write_text(path, "new")

        leftovers = [p.name for p in self.dir.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_write_text_clears_readonly_destination(self):
        # OneDrive can leave a synced file with the read-only attribute set,
        # which makes os.replace fail with the same WinError 5 forever — no
        # amount of waiting clears it, so retrying alone is not enough.
        import os as _os
        import stat as _stat
        from app.utils import atomic_io
        path = self.dir / "out.txt"
        path.write_text("old", encoding="utf-8")
        _os.chmod(path, _stat.S_IREAD)

        try:
            atomic_io.atomic_write_text(path, "new")
        finally:
            _os.chmod(path, _stat.S_IWRITE)

        self.assertEqual(path.read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
