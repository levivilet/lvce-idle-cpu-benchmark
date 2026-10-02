from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark


class CursorBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.editor = benchmark.load_editor("cursor")

    def test_cursor_command_uses_an_isolated_profile_and_skips_welcome(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / ".tmp/apps/cursor/usr/share/cursor/cursor"
            binary.parent.mkdir(parents=True)
            binary.touch()
            with mock.patch.object(benchmark, "ROOT", root):
                command = benchmark.command_for(self.editor, root / "home")
        self.assertIn("--user-data-dir", command)
        self.assertIn("--skip-welcome", command)
        self.assertIn("--skip-release-notes", command)
        self.assertIn("--disable-workspace-trust", command)
        self.assertIn("--disable-extensions", command)

    def test_profile_welcome_state_is_repeat_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            database = home / "profile/User/globalStorage/state.vscdb"
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO ItemTable VALUES (?, ?)", (
                    "cursorai/donotchange/privacyMode", "false"
                ))
            benchmark.prepare_cursor_profile(self.editor, home, {}, home / "out.log", home / "err.log")
            benchmark.prepare_cursor_profile(self.editor, home, {}, home / "out.log", home / "err.log")
            with sqlite3.connect(database) as connection:
                values = dict(connection.execute("SELECT key, value FROM ItemTable"))
            self.assertEqual(values, benchmark.CURSOR_WELCOME_VALUES)

    def test_workbench_wait_rejects_missing_fixture_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "idle-cpu.txt"
            with mock.patch.object(benchmark.time, "monotonic", side_effect=[0, 1]), \
                    mock.patch.object(benchmark.subprocess, "run", return_value=mock.Mock(
                        returncode=1, stdout="", stderr="")):
                with self.assertRaisesRegex(RuntimeError, "did not show the benchmark fixture"):
                    benchmark.wait_for_cursor_workbench(fixture, timeout=0.5)

    def test_workbench_wait_requires_fixture_in_visible_window_title(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "idle-cpu.txt"
            visible = mock.Mock(returncode=0, stdout="17\n", stderr="")
            title = mock.Mock(returncode=0, stdout="idle-cpu.txt - Cursor", stderr="")
            with mock.patch.object(benchmark.subprocess, "run", side_effect=[visible, title]) as run:
                benchmark.wait_for_cursor_workbench(fixture)
            self.assertEqual(run.call_args_list[0].args[0][:4], [
                "xdotool", "search", "--onlyvisible", "--name"
            ])

    def test_idle_trial_only_launches_and_measures_the_editor(self):
        editor = benchmark.load_editor("basic-electron")
        process = mock.Mock(pid=123, returncode=None)
        process.poll.return_value = None
        measurement = {"valid": True, "cpuUsec": 0}
        with mock.patch.object(benchmark.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(benchmark, "command_for", return_value=["editor"]), \
                mock.patch.object(benchmark, "cgroup_measurement", return_value=None), \
                mock.patch.object(benchmark, "process_measurement", return_value=measurement) as sample, \
                mock.patch.object(benchmark, "terminate_process_group"):
            result = benchmark.trial(editor, settle_seconds=0, sample_seconds=180)

        command = popen.call_args.args[0]
        self.assertTrue(result["valid"])
        self.assertEqual(sample.call_args.args, (process, 180))
        self.assertNotIn("xdotool", command)
        self.assertNotIn("ydotool", command)


if __name__ == "__main__":
    unittest.main()
