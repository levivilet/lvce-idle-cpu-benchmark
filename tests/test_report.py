import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import report


def sample(editor_id, utilization=25):
    return {
        "editor": {"id": editor_id},
        "protocol": {"definition": "100% is one busy logical CPU",
                     "typingDurationSeconds": 2, "typingCadenceSeconds": 1},
        "host": {"platform": "test", "cpuCount": 4},
        "trials": [{"valid": True, "repeat": 1, "cpuUsec": utilization * 20000,
                    "elapsedSeconds": 2, "utilizationPercent": utilization,
                    "cpuTicks": utilization * 2, "ticksPerSecond": 100,
                    "source": "proc-process-tree", "averageRssKb": 51200,
                    "peakRssKb": 61440, "memoryElapsedSeconds": 2,
                    "inputCount": 2, "inputOffsetsSeconds": [0, 1],
                    "cadenceSeconds": 1, "savedContentVerified": True,
                    "rssSamplesKb": [[0, 40960], [1, 61440], [2, 51200]], "memorySamples": 3}],
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

    def test_rejects_invalid_memory_and_typing_evidence(self):
        data = sample("lvce")
        data["trials"][0]["peakRssKb"] = 1
        with self.assertRaisesRegex(ValueError, "memory"):
            report.validate_result(data, "lvce")
        data = sample("lvce")
        data["trials"][0]["inputCount"] = 1
        with self.assertRaisesRegex(ValueError, "typing evidence"):
            report.validate_result(data, "lvce")
        data = sample("lvce")
        data["trials"][0]["inputOffsetsSeconds"] = [0, 1.7]
        with self.assertRaisesRegex(ValueError, "cadence"):
            report.validate_result(data, "lvce")
        data = sample("lvce")
        data["protocol"]["typingDurationSeconds"] = 180
        with self.assertRaisesRegex(ValueError, "typing evidence"):
            report.validate_result(data, "lvce")
        data = sample("lvce")
        data["trials"][0]["cpuTicks"] += 1
        with self.assertRaisesRegex(ValueError, "CPU counters"):
            report.validate_result(data, "lvce")

    def test_requires_every_editor_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for editor in report.EDITORS[:-1]:
                path = root / f"idle-cpu-{editor['id']}" / "results" / "results.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(sample(editor["id"])))
            with self.assertRaisesRegex(ValueError, "do not match the matrix"):
                report.load_results(root)

    def test_loads_complete_matrix_from_downloaded_artifact_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for editor in report.EDITORS:
                artifact = root / f"idle-cpu-{editor['id']}"
                artifact.mkdir()
                (artifact / "results.json").write_text(json.dumps(sample(editor["id"])))
            self.assertEqual(len(report.load_results(root)), len(report.EDITORS))

    def test_builds_per_editor_downloads_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            results = [{"editor": editor, "data": sample(editor["id"], 125)} for editor in report.EDITORS]
            report.build_report(results, output, "https://github.com/example/run/1", "abc123")
            page = (output / "index.html").read_text()
            report_json = json.loads((output / "report.json").read_text())
            self.assertIn("125.00%", page)
            self.assertIn("50.0 / 60.0 MiB", page)
            self.assertIn("Median average resident memory while typing", page)
            self.assertIn(f"All {len(report.EDITORS)} editors completed", page)
            self.assertIn("github.com/example/run/1", page)
            self.assertEqual(len(report_json["editors"]), len(report.EDITORS))
            self.assertTrue((output / "raw/lvce.json").is_file())

    def test_median_chart_maps_lower_values_to_the_bottom(self):
        entries = [
            {"name": "Low editor", "medianUtilizationPercent": 0},
            {"name": "High editor", "medianUtilizationPercent": 50},
        ]
        chart = report.build_median_chart(entries)
        points = re.findall(r'<circle class="marker" cx="([\d.]+)" cy="([\d.]+)"', chart)
        self.assertEqual(len(points), 2)
        self.assertGreater(float(points[0][1]), float(points[1][1]))
        self.assertIn("Low editor", chart)
        self.assertIn("High editor", chart)
        self.assertIn("50.00%", chart)

    def test_memory_chart_sorts_by_average_rss_with_zero_origin(self):
        chart = report.build_median_memory_chart([
            {"name": "Large", "medianAverageRssKb": 100 * 1024},
            {"name": "Small", "medianAverageRssKb": 20 * 1024},
        ])
        markers = re.findall(r'<circle class="marker" cx="([\d.]+)" cy="([\d.]+)" r="5"><title>([^<]+):', chart)
        self.assertEqual([marker[2] for marker in markers], ["Small", "Large"])
        self.assertGreater(float(markers[0][1]), float(markers[1][1]))

    def test_median_chart_keeps_ties_labeled_and_zero_visible(self):
        chart = report.build_median_chart([
            {"name": "Zero One", "medianUtilizationPercent": 0},
            {"name": "Zero Two", "medianUtilizationPercent": 0},
        ])
        points = re.findall(r'<circle class="marker" cx="([\d.]+)" cy="([\d.]+)"', chart)
        self.assertEqual(len(points), 2)
        self.assertNotEqual(points[0][0], points[1][0])
        self.assertEqual(points[0][1], points[1][1])
        self.assertEqual(float(points[0][1]), 326)
        self.assertIn("Zero One: 0.00%", chart)
        self.assertIn("Zero Two: 0.00%", chart)

    def test_median_chart_sorts_numeric_values_and_preserves_entry_associations(self):
        entries = [
            {"name": "Ten percent", "medianUtilizationPercent": 10},
            {"name": "Zero percent", "medianUtilizationPercent": 0},
            {"name": "Tied first", "medianUtilizationPercent": 2},
            {"name": "One percent", "medianUtilizationPercent": 1},
            {"name": "Tied second", "medianUtilizationPercent": 2},
        ]
        original_entries = [entry.copy() for entry in entries]

        chart = report.build_median_chart(entries)

        markers = re.findall(
            r'<circle class="marker" cx="([\d.]+)" cy="([\d.]+)" r="5"><title>'
            r'([^<]+): ([\d.]+%)</title></circle>',
            chart,
        )
        self.assertEqual(
            [(name, label) for _, _, name, label in markers],
            [
                ("Zero percent", "0.00%"),
                ("One percent", "1.00%"),
                ("Tied first", "2.00%"),
                ("Tied second", "2.00%"),
                ("Ten percent", "10.00%"),
            ],
        )
        self.assertEqual(
            [float(x) for x, _, _, _ in markers],
            sorted(float(x) for x, _, _, _ in markers),
        )
        self.assertEqual(entries, original_entries)

    def test_median_chart_expands_scale_above_one_hundred_percent(self):
        chart = report.build_median_chart([{"name": "Busy editor", "medianUtilizationPercent": 135}])
        point = re.search(r'<circle class="marker" cx="[\d.]+" cy="([\d.]+)"', chart)
        self.assertIsNotNone(point)
        self.assertGreater(float(point.group(1)), 38)
        self.assertIn("135.00%", chart)


if __name__ == "__main__":
    unittest.main()
