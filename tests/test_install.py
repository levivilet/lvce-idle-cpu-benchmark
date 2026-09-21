import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class EditorLockTest(unittest.TestCase):
    def test_matrix_is_pinned_or_explicitly_runner_provided(self):
        editors = json.loads((ROOT / "config/editors.lock.json").read_text())
        self.assertEqual(len(editors), len({editor["id"] for editor in editors}))
        for editor in editors:
            self.assertTrue(editor["version"])
            self.assertTrue(editor["binary"])
            if editor.get("package"):
                self.assertEqual(editor["id"], "geany")
            else:
                self.assertRegex(editor["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(editor["url"].startswith("https://"))
