"""Keyboard input drivers for the desktop typing benchmark."""
from __future__ import annotations

import os
import re
import subprocess
import time


class XdotoolInput:
    """Send input to the exact visible window containing the fixture name."""

    def __init__(self, fixture_name: str, window_pattern: str | tuple[str, ...] | None = None,
                 wait_seconds: float = 30, click_position: tuple[int, int] = (800, 250)):
        self.fixture_name = fixture_name
        self.window_patterns = ((window_pattern,) if isinstance(window_pattern, str)
                                else window_pattern or (fixture_name,))
        self.window_pattern = " or ".join(self.window_patterns)
        self.focus_patterns = list(self.window_patterns)
        self.click_position = click_position
        deadline = time.monotonic() + wait_seconds
        while True:
            for pattern in self.window_patterns:
                result = subprocess.run(
                    ["xdotool", "search", "--onlyvisible", "--name", pattern],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    break
            else:
                result = None
            if result is not None:
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
        active = focused.stdout.strip() if focused.returncode == 0 else "unknown"
        if focused.returncode != 0 or not any(
                re.search(pattern, active) for pattern in self.focus_patterns):
            raise RuntimeError(
                f"editor window lost focus for {self.window_pattern} (active window: {active})"
            )

    def open_file(self, filename: str, shortcut: str = "ctrl+p") -> None:
        self._require_focus()
        subprocess.run(["xdotool", "key", "--clearmodifiers", shortcut], check=True)
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", filename],
            check=True,
        )
        time.sleep(.5)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "Return"], check=True)
        time.sleep(.5)
        self._require_focus()

    def open_idea_file(self, filename: str) -> None:
        """Wait for the project editor replacing IDEA's initial LightEdit window."""
        pattern = r"^typing-cpu .*" + re.escape(filename) + r"$"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            window = self._visible_window(pattern)
            if window:
                self.window_id = window
                self.focus_patterns = [pattern]
                subprocess.run(["xdotool", "windowfocus", "--sync", window], check=True)
                self._require_focus()
                return
            time.sleep(.25)
        raise RuntimeError(f"IntelliJ IDEA did not open the fixture {filename} in a project")

    def press_key(self, key: str) -> None:
        subprocess.run(["xdotool", "key", "--clearmodifiers", key], check=True)

    def open_selected_file(self, shortcut: str = "ctrl+o") -> None:
        self._require_focus()
        subprocess.run(["xdotool", "key", "--clearmodifiers", shortcut], check=True)
        deadline = time.monotonic() + 5
        dialog_id = None
        while time.monotonic() < deadline:
            dialog = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", "^Open File$"],
                capture_output=True, text=True, check=False,
            )
            if dialog.returncode == 0 and dialog.stdout.strip():
                dialog_id = dialog.stdout.splitlines()[-1]
                break
            time.sleep(.1)
        if dialog_id is None:
            raise RuntimeError("editor did not show the open-file dialog")
        subprocess.run(["xdotool", "windowfocus", "--sync", dialog_id], check=True)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "alt+o"], check=True)
        time.sleep(.5)
        dialog_still_open = self._visible_window("^Open File$")
        if dialog_still_open:
            geometry = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", dialog_still_open],
                capture_output=True, text=True, check=True,
            )
            dimensions = dict(
                line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line
            )
            x = int(dimensions["X"]) + int(dimensions["WIDTH"]) - 50
            y = int(dimensions["Y"]) + int(dimensions["HEIGHT"]) - 42
            subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=True)
            subprocess.run(["xdotool", "click", "1"], check=True)
            time.sleep(.5)
        self._require_focus()

    def click_editor(self, position: tuple[int, int]) -> None:
        self._require_focus()
        x, y = position
        subprocess.run(["xdotool", "mousemove", "--window", self.window_id,
                        str(x), str(y)], check=True)
        subprocess.run(["xdotool", "click", "--window", self.window_id, "1"], check=True)
        self._require_focus()

    def close_welcome(self) -> None:
        self._require_focus()
        subprocess.run(["xdotool", "mousemove", "132", "100"], check=True)
        subprocess.run(["xdotool", "click", "1"], check=True)
        time.sleep(.5)
        self._require_focus()

    def _visible_window(self, title_pattern: str | tuple[str, ...]) -> str | None:
        patterns = (title_pattern,) if isinstance(title_pattern, str) else title_pattern
        for pattern in patterns:
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", pattern],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.splitlines()[-1]
        return None

    def _click_window(self, window_id: str, x: int, y: int) -> None:
        geometry = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", window_id],
            capture_output=True, text=True, check=True,
        )
        dimensions = dict(
            line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line
        )
        origin_x = int(dimensions["X"])
        origin_y = int(dimensions["Y"])
        width = int(dimensions["WIDTH"])
        height = int(dimensions["HEIGHT"])
        subprocess.run(["xdotool", "windowfocus", "--sync", window_id], check=True)
        subprocess.run(["xdotool", "mousemove", str(origin_x + min(x, width - 1)),
                        str(origin_y + min(y, height - 1))], check=True)
        subprocess.run(["xdotool", "click", "1"], check=True)

    def accept_idea_onboarding(self) -> None:
        agreement = self._visible_window("^IntelliJ IDEA User Agreement$")
        if agreement:
            geometry = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", agreement],
                capture_output=True, text=True, check=True,
            )
            dimensions = dict(
                line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line
            )
            width = int(dimensions["WIDTH"])
            height = int(dimensions["HEIGHT"])
            self._click_window(agreement, 43, height - 88)
            self._click_window(agreement, width - 61, height - 49)
            time.sleep(.5)
        sharing = self._visible_window("^Data Sharing$")
        if sharing:
            geometry = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", sharing],
                capture_output=True, text=True, check=True,
            )
            dimensions = dict(
                line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line
            )
            width = int(dimensions["WIDTH"])
            height = int(dimensions["HEIGHT"])
            self._click_window(sharing, width // 2 + 20, height - 49)
            time.sleep(1)
        deadline = time.monotonic() + 15
        import_settings = None
        while time.monotonic() < deadline:
            import_settings = self._visible_window("^IntelliJ IDEA$")
            if import_settings:
                break
            time.sleep(.25)
        if import_settings:
            geometry = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", import_settings],
                capture_output=True, text=True, check=True,
            )
            dimensions = dict(
                line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line
            )
            width = int(dimensions["WIDTH"])
            height = int(dimensions["HEIGHT"])
            self._click_window(import_settings, width // 2, height // 2 + 8)
            time.sleep(1)
            focused = subprocess.run(
                ["xdotool", "getwindowfocus"], capture_output=True, text=True, check=True,
            ).stdout.strip()
            focused_name = subprocess.run(
                ["xdotool", "getwindowname", focused],
                capture_output=True, text=True, check=False,
            )
            if focused_name.returncode == 0 and not focused_name.stdout.strip():
                geometry = subprocess.run(
                    ["xdotool", "getwindowgeometry", "--shell", focused],
                    capture_output=True, text=True, check=True,
                )
                dimensions = dict(
                    line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line
                )
                width = int(dimensions["WIDTH"])
                height = int(dimensions["HEIGHT"])
                self._click_window(focused, width // 2 - 45, height - 35)
            else:
                self.press_key("Return")
            time.sleep(2)
        focused = subprocess.run(
            ["xdotool", "getwindowfocus"], capture_output=True, text=True, check=True,
        ).stdout.strip()
        if "^$" not in self.focus_patterns:
            self.focus_patterns.append("^$")
        refreshed = self._visible_window(self.window_patterns)
        self.window_id = refreshed or focused

    def accept_idea_open_project(self) -> None:
        """Open a file launched in IDEA's simplified LightEdit mode as a project."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            prompt = self._visible_window("^Open in Project$")
            if prompt:
                break
            if self._visible_window("^Choose Project Root Directory$"):
                self.accept_idea_project_root()
                return
            if self._visible_window(r"^typing-cpu .*typing-cpu[.]txt$"):
                return
            time.sleep(.25)
        else:
            raise RuntimeError("IDEA did not offer to open the fixture in a project")
        geometry = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", prompt],
            capture_output=True, text=True, check=True,
        )
        dimensions = dict(
            line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line
        )
        self._click_window(prompt, int(dimensions["WIDTH"]) - 78,
                           int(dimensions["HEIGHT"]) - 29)
        self.accept_idea_project_root()

    def accept_idea_project_root(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            root = self._visible_window("^Choose Project Root Directory$")
            if root:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("IDEA did not show its project directory chooser")
        if root:
            geometry = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", root],
                capture_output=True, text=True, check=True,
            )
            dimensions = dict(line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line)
            subprocess.run(["xdotool", "windowfocus", "--sync", root], check=True)
            subprocess.run(["xdotool", "mousemove", "--window", root,
                            str(int(dimensions["WIDTH"]) - 135),
                            str(int(dimensions["HEIGHT"]) - 27)], check=True)
            subprocess.run(["xdotool", "click", "1"], check=True)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                title = subprocess.run(["xdotool", "getwindowfocus", "getwindowname"],
                                       capture_output=True, text=True, check=True).stdout.strip()
                if not title:
                    # IDEA's Trust Project modal has an empty title and its
                    # default button trusts only this temporary fixture project.
                    self.press_key("Return")
                    break
                time.sleep(.25)

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

    def open_file(self, filename: str, shortcut: str = "ctrl+p") -> None:
        self._run("key", shortcut)
        self._run("type", "--key-delay", "0", "--", filename)
        self._run("key", "Return")

    def press_key(self, key: str) -> None:
        self._run("key", key)

    def open_selected_file(self, shortcut: str = "ctrl+o") -> None:
        self._run("key", shortcut)
        self._run("key", "Return")

    def click_editor(self, position: tuple[int, int]) -> None:
        del position

    def type_character(self, character: str) -> None:
        self._run("type", "--key-delay", "0", "--", character)

    def save(self) -> None:
        self._run("key", "ctrl+s")


def make_input(driver: str, fixture_name: str,
               window_pattern: str | tuple[str, ...] | None = None,
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
