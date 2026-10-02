import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from metrics import CpuStat, aggregate_cpu, counter_delta, measure_process_tree, utilization_percent


class MetricsTest(unittest.TestCase):
    def test_counter_conversion_defines_one_cpu_as_100_percent(self):
        self.assertEqual(utilization_percent(250_000, 1), 25)
        self.assertEqual(utilization_percent(1_000_000, 1), 100)

    def test_invalid_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            utilization_percent(1, 0)
        with self.assertRaises(ValueError):
            aggregate_cpu(CpuStat(20, 10, 10), CpuStat(19, 10, 9), 1)

    def test_aggregation_returns_raw_components_and_utilization(self):
        result = aggregate_cpu(CpuStat(100, 60, 40), CpuStat(350, 210, 140), 1)
        self.assertEqual(result["cpuUsec"], 250)
        self.assertEqual(result["userUsec"], 150)
        self.assertEqual(result["systemUsec"], 100)
        self.assertEqual(result["utilizationPercent"], .025)

    def test_missing_or_inconsistent_counter_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cpu.stat"
            path.write_text("usage_usec 4\nuser_usec 3\n")
            from metrics import read_cpu_stat
            with self.assertRaises(ValueError):
                read_cpu_stat(path)
        with self.assertRaises(ValueError):
            counter_delta(CpuStat(10, 8, 2), CpuStat(11, 10, 3))

    def test_controlled_busy_process_has_nonzero_descendant_cpu(self):
        process = subprocess.Popen([sys.executable, "-c", "while True: pass"])
        try:
            time.sleep(.1)
            cpu_usec = measure_process_tree(process.pid, .35, sample_interval=.05)
            self.assertGreater(cpu_usec, 0)
        finally:
            process.terminate()
            process.wait(timeout=5)
