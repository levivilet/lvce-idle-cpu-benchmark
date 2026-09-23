import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import report


def sample(editor_id, utilization=25):
    return {
        "editor": {"id": editor_id},
        "protocol": {"definition": "100% is one busy logical CPU"},
        "host": {"platform": "test", "cpuCount": 4},
        "trials": [{"valid": True, "repeat": 1, "cpuUsec": 250000,
                    "elapsedSeconds": 1, "utilizationPercent": utilization,
                    "source": "proc-process-tree"}],
    }


class ReportTests(unittest.TestCase):
    def test_rejects_missing_and_invalid_trials(self):
        with self.assertRaisesRegex(ValueError, "no trials"):
            report.validate_result({"editor": {"id": "lvce"}, "trials": []}, "lvce")
        data = sample("lvce")
        data["trials"][0]["valid"] = False
        with self.assertRaisesRegex(ValueError, "invalid"):
            report.validate_result(data, "lvce")

    def test_rejects_non_numeric_and_non_finite_measurements(self):
        for value in (None, "0", float("nan"), float("inf"), True):
            data = sample("lvce")
            data["trials"][0]["utilizationPercent"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "utilizationPercent"):
                report.validate_result(data, "lvce")

    def test_accepts_values_above_one_hundred_percent(self):
        self.assertEqual(report.validate_result(sample("lvce", 135), "lvce")["trials"][0]["utilizationPercent"], 135)

    def test_requires_every_editor_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for editor in report.EDITORS[:-1]:
                path = root / f"idle-cpu-{editor['id']}" / "results" / "results.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(sample(editor["id"])))
            with self.assertRaisesRegex(ValueError, "do not match the matrix"):
                report.load_results(root)

    def test_builds_per_editor_downloads_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            results = [{"editor": editor, "data": sample(editor["id"], 125)} for editor in report.EDITORS]
            report.build_report(results, output, "https://github.com/example/run/1", "abc123")
            page = (output / "index.html").read_text()
            report_json = json.loads((output / "report.json").read_text())
            self.assertIn("125.00%", page)
            self.assertIn("All 10 editors completed", page)
            self.assertIn("github.com/example/run/1", page)
            self.assertEqual(len(report_json["editors"]), 10)
            self.assertTrue((output / "raw/lvce.json").is_file())


if __name__ == "__main__":
    unittest.main()
