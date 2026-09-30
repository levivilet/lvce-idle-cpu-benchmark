from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from input_driver import XdotoolInput, check_cadence, expected_text, type_at_cadence


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class InputDriverTests(unittest.TestCase):
    def test_expected_text_is_deterministic_and_ascii(self):
        self.assertEqual(expected_text(40), "abcdefghijklmnopqrstuvwxyz0123456789abcd")

    def test_schedule_sends_every_character_at_the_requested_cadence(self):
        clock = Clock()
        sent = []
        samples = []
        offsets = type_at_cadence(
            "abc", 1, 3, sent.append, samples.append,
            clock=clock.monotonic, sleep=clock.sleep,
        )
        self.assertEqual(sent, list("abc"))
        self.assertEqual(offsets, [0, 1, 2])
        self.assertEqual(samples[0], 0)
        self.assertEqual(samples[-1], 3)

    def test_cadence_validation_rejects_missed_or_reversed_input(self):
        check_cadence([0, 1, 2], 1)
        for offsets in ([0, 1.6, 2], [0, 1, .5]):
            with self.subTest(offsets=offsets), self.assertRaisesRegex(ValueError, "cadence"):
                check_cadence(offsets, 1)

    def test_xdotool_driver_focuses_fixture_window_and_verifies_focus(self):
        search = mock.Mock(returncode=0, stdout="123\n", stderr="")
        focus = mock.Mock(returncode=0, stdout="typing-cpu.txt - Editor\n", stderr="")
        with mock.patch("input_driver.subprocess.run", side_effect=[
                search, mock.Mock(), mock.Mock(), mock.Mock(), focus, focus,
                mock.Mock(), mock.Mock()]) as run:
            keyboard = XdotoolInput("typing-cpu.txt")
            keyboard.clear()
        self.assertEqual(keyboard.window_id, "123")
        self.assertEqual(run.call_args_list[1].args[0], ["xdotool", "windowfocus", "--sync", "123"])
        self.assertEqual(run.call_args_list[2].args[0], ["xdotool", "mousemove", "--window", "123", "200", "160"])
        self.assertEqual(run.call_args_list[4].args[0], ["xdotool", "getwindowfocus", "getwindowname"])

    def test_xdotool_driver_rejects_missing_fixture_window(self):
        with mock.patch("input_driver.subprocess.run", return_value=mock.Mock(
                returncode=1, stdout="", stderr="")):
            with self.assertRaisesRegex(RuntimeError, "no visible editor window"):
                XdotoolInput("typing-cpu.txt", wait_seconds=0)


if __name__ == "__main__":
    unittest.main()
