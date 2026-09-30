"""Keyboard input drivers for the desktop typing benchmark."""
from __future__ import annotations

import os
import subprocess
import time


class XdotoolInput:
    """Send input to the exact visible window containing the fixture name."""

    def __init__(self, fixture_name: str, window_pattern: str | None = None,
                 wait_seconds: float = 30, click_position: tuple[int, int] = (800, 250)):
        self.fixture_name = fixture_name
        self.window_pattern = window_pattern or fixture_name
        self.click_position = click_position
        deadline = time.monotonic() + wait_seconds
        while True:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", self.window_pattern],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"no visible editor window matching {self.window_pattern}")
            time.sleep(.25)
        self.window_id = result.stdout.splitlines()[-1]
        subprocess.run(["xdotool", "windowfocus", "--sync", self.window_id], check=True)
        x, y = self.click_position
        subprocess.run(["xdotool", "mousemove", "--window", self.window_id, str(x), str(y)], check=True)
        subprocess.run(["xdotool", "click", "--window", self.window_id, "1"], check=True)
        self._require_focus()

    def _require_focus(self) -> None:
        focused = subprocess.run(
            ["xdotool", "getwindowfocus", "getwindowname"],
            capture_output=True, text=True, check=False,
        )
        if focused.returncode != 0 or self.window_pattern not in focused.stdout:
            raise RuntimeError(f"editor window lost focus for {self.window_pattern}")

    def open_file(self, filename: str) -> None:
        self._require_focus()
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+p"], check=True)
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", filename],
            check=True,
        )
        subprocess.run(["xdotool", "key", "--clearmodifiers", "Return"], check=True)
        time.sleep(.5)
        self._require_focus()

    def clear(self) -> None:
        self._require_focus()
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+a"], check=True)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "BackSpace"], check=True)

    def type_character(self, character: str) -> None:
        self._require_focus()
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", character],
            check=True,
        )

    def save(self) -> None:
        self._require_focus()
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+s"], check=True)


class YdotoolInput:
    """Send global Wayland keyboard events through a running ydotool daemon."""

    def __init__(self):
        self.socket = None

    def _run(self, *arguments: str) -> None:
        environment = None if self.socket is None else {**os.environ, "YDOTOOL_SOCKET": self.socket}
        subprocess.run(["ydotool", *arguments], check=True, env=environment)

    def clear(self) -> None:
        self._run("key", "ctrl+a")
        self._run("key", "BackSpace")

    def open_file(self, filename: str) -> None:
        self._run("key", "ctrl+p")
        self._run("type", "--key-delay", "0", "--", filename)
        self._run("key", "Return")

    def type_character(self, character: str) -> None:
        self._run("type", "--key-delay", "0", "--", character)

    def save(self) -> None:
        self._run("key", "ctrl+s")


def make_input(driver: str, fixture_name: str, window_pattern: str | None = None,
               click_position: tuple[int, int] = (800, 250)):
    if driver == "xdotool":
        return XdotoolInput(fixture_name, window_pattern, click_position=click_position)
    if driver == "ydotool":
        return YdotoolInput()
    raise ValueError(f"unknown input driver: {driver}")


def expected_text(length: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return (alphabet * ((length + len(alphabet) - 1) // len(alphabet)))[:length]


def check_cadence(offsets: list[float], cadence_seconds: float, tolerance_seconds: float = 0.5) -> None:
    if len(offsets) < 2:
        raise ValueError("typing trial must include at least two input events")
    gaps = [following - current for current, following in zip(offsets, offsets[1:])]
    if any(gap <= 0 or abs(gap - cadence_seconds) > tolerance_seconds for gap in gaps):
        raise ValueError("typing input missed the configured cadence")


def type_at_cadence(characters: str, cadence_seconds: float, duration_seconds: float,
                    send_character, sample, clock=time.monotonic, sleep=time.sleep) -> list[float]:
    """Schedule characters while `sample` observes the editor process tree."""
    if not characters or cadence_seconds <= 0 or duration_seconds <= 0:
        raise ValueError("typing text, cadence, and duration must be positive")
    started = clock()
    offsets = []
    next_index = 0
    while True:
        now = clock()
        elapsed = now - started
        if elapsed >= duration_seconds:
            break
        sample(elapsed)
        if next_index < len(characters) and elapsed >= next_index * cadence_seconds:
            send_character(characters[next_index])
            offsets.append(clock() - started)
            next_index += 1
            continue
        next_input = next_index * cadence_seconds if next_index < len(characters) else duration_seconds
        next_sample = min(duration_seconds, next_input, elapsed + 0.1)
        sleep(max(0, next_sample - (clock() - started)))
    sample(clock() - started)
    check_cadence(offsets, cadence_seconds)
    if next_index != len(characters):
        raise ValueError(f"only {next_index} of {len(characters)} characters were scheduled")
    return offsets
