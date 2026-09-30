"""Measure process-tree CPU and memory while typing in one installed editor."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import tempfile
import time

from metrics import process_tree, process_tree_memory_kb, summarize_memory, ticks_to_usec, utilization_percent
from input_driver import expected_text, make_input, type_at_cadence

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "config/editors.lock.json"
CURSOR_WELCOME_VALUES = {
    "cursorai/donotchange/privacyMode": "true",
    "workbench.services.onFirstStartupService.isVeryFirstTime": "false",
    "cursorAuth/stripeMembershipType": "free",
    "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser": json.dumps(
        {"authenticationSettings": {"githubLoggedIn": False}}, separators=(",", ":")
    ),
}


def load_editor(editor_id):
    editors = json.loads(LOCK.read_text())
    try:
        return next(editor for editor in editors if editor["id"] == editor_id)
    except StopIteration as error:
        raise ValueError(f"unknown editor: {editor_id}") from error


def command_for(editor, home):
    binary = ROOT / ".tmp/apps" / editor["id"] / editor["binary"]
    if editor.get("package"):
        return [editor["binary"]]
    if not binary.exists():
        raise FileNotFoundError(f"install first: {binary}")
    common = ["--disable-gpu"] if editor["id"] in {"lvce", "vscode", "cursor", "basic-electron", "theia", "atom"} else []
    if editor["id"] in {"lvce", "vscode", "cursor", "theia", "atom", "basic-electron"}:
        common += ["--no-sandbox"]
    if editor["id"] in {"lvce", "vscode", "cursor"}:
        common += ["--user-data-dir", str(home / "profile")]
    if editor["id"] == "vscode":
        common += ["--disable-extensions", "--skip-welcome", "--skip-release-notes"]
    if editor["id"] == "cursor":
        common += ["--disable-extensions", "--disable-workspace-trust", "--skip-welcome", "--skip-release-notes", "--new-window"]
    if editor["id"] == "basic-electron":
        common += ["--ozone-platform=x11", str(ROOT / editor["app"])]
    if editor["id"] == "zed":
        common += ["--user-data-dir", str(home / "profile")]
    if editor["id"] == "eclipse":
        common += ["-nosplash", "-data", str(home / "eclipse-workspace"), "-application", "org.eclipse.ui.ide.workbench"]
    if editor["id"] == "atom":
        common += ["--new-window"]
    if editor["id"] == "lapce":
        # Lapce launches a detached child unless --wait is supplied.
        common += ["--new", "--wait"]
    return [str(binary), *common]


def terminate_process_group(process):
    if not process:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise RuntimeError("editor process did not stop")


def prepare_cursor_profile(editor, home, environment, stdout, stderr):
    """Initialize Cursor once, then seed its version-specific welcome state."""
    database = home / "profile/User/globalStorage/state.vscdb"
    if not database.exists():
        workspace = home / "bootstrap-workspace"
        workspace.mkdir()
        process = None
        try:
            with stdout.open("w") as stdout_file, stderr.open("w") as stderr_file:
                process = subprocess.Popen(
                    [*command_for(editor, home), str(workspace)], cwd=home,
                    env=environment, stdout=stdout_file, stderr=stderr_file,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 30
                while not database.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"Cursor exited while creating its profile ({process.returncode})")
                    time.sleep(0.25)
                if not database.exists():
                    raise RuntimeError("Cursor did not create its profile database")
        finally:
            terminate_process_group(process)

    with sqlite3.connect(database, timeout=10) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ItemTable)")}
        if not {"key", "value"}.issubset(columns):
            raise RuntimeError("Cursor profile database has no compatible ItemTable")
        connection.executemany(
            "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
            CURSOR_WELCOME_VALUES.items(),
        )
        actual = dict(connection.execute(
            "SELECT key, value FROM ItemTable WHERE key IN (%s)" % ",".join("?" for _ in CURSOR_WELCOME_VALUES),
            tuple(CURSOR_WELCOME_VALUES),
        ))
        if actual != CURSOR_WELCOME_VALUES:
            raise RuntimeError("Cursor profile welcome state was not saved")


def wait_for_cursor_workbench(fixture, timeout=30):
    """Require a visible Cursor window whose title names the opened fixture."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        windows = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", fixture.name],
            capture_output=True, text=True, check=False,
        )
        if windows.returncode == 0 and windows.stdout.strip():
            for window_id in windows.stdout.splitlines():
                title = subprocess.run(
                    ["xdotool", "getwindowname", window_id],
                    capture_output=True, text=True, check=False,
                )
                if title.returncode == 0 and fixture.name in title.stdout:
                    return
        time.sleep(0.5)
    raise RuntimeError(f"Cursor did not show the benchmark fixture {fixture.name}")


def typing_measurement(process, keyboard, fixture, duration_seconds, cadence_seconds):
    """Type into the editor while sampling its complete process tree."""
    started = time.monotonic()
    previous = process_tree(process.pid)
    tick_total = 0
    memory_samples = []

    def sample(elapsed):
        nonlocal previous, tick_total
        if process.poll() is not None:
            raise RuntimeError(f"editor exited during typing ({process.returncode})")
        current = process_tree(process.pid)
        for pid, ticks in current.items():
            if pid in previous:
                delta = ticks - previous[pid]
                if delta < 0:
                    raise ValueError("process CPU counter moved backwards")
                tick_total += delta
        previous = current
        memory_samples.append((elapsed, process_tree_memory_kb(process.pid)))

    characters = expected_text(math.ceil(duration_seconds / cadence_seconds))
    offsets = type_at_cadence(characters, cadence_seconds, duration_seconds,
                              keyboard.type_character, sample)
    elapsed = time.monotonic() - started
    keyboard.save()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and fixture.read_text(errors="replace") != characters:
        time.sleep(.1)
    saved_text = fixture.read_text(errors="replace")
    if saved_text not in {characters, characters + "\n", characters + "\r\n"}:
        raise RuntimeError(
            "saved editor contents did not match the typed characters "
            f"(expected {characters!r}, found {saved_text[:120]!r})"
        )
    if process.poll() is not None:
        raise RuntimeError(f"editor exited during typing ({process.returncode})")
    memory = summarize_memory(memory_samples)
    cpu_usec = ticks_to_usec(tick_total, os.sysconf("SC_CLK_TCK"))
    return {
        "cpuUsec": cpu_usec,
        "cpuTicks": tick_total,
        "ticksPerSecond": os.sysconf("SC_CLK_TCK"),
        "elapsedSeconds": elapsed,
        "utilizationPercent": utilization_percent(cpu_usec, elapsed),
        "source": "proc-process-tree",
        **memory,
        "rssSamplesKb": memory_samples,
        "inputCount": len(offsets),
        "inputOffsetsSeconds": offsets,
        "cadenceSeconds": cadence_seconds,
        "savedContentVerified": True,
        "savedTrailingNewline": saved_text.endswith(("\n", "\r")),
    }


def trial(editor, settle_seconds, sample_seconds, input_driver, cadence_seconds):
    with tempfile.TemporaryDirectory(prefix=f"typing-cpu-{editor['id']}-") as directory:
        home = Path(directory) / "home"
        for name in ("config", "data", "cache", "state"):
            (home / name).mkdir(parents=True)
        runtime = home / "runtime"
        runtime.mkdir()
        runtime.chmod(0o700)
        environment = {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_RUNTIME_DIR": str(runtime),
            "ELECTRON_NO_ATTACH_CONSOLE": "1",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "GALLIUM_DRIVER": "llvmpipe",
            "ZED_ALLOW_EMULATED_GPU": "1",
            "ELECTRON_OZONE_PLATFORM_HINT": "wayland" if input_driver == "ydotool" else "x11",
        }
        workspace = home / "typing-cpu"
        workspace.mkdir()
        fixture = workspace / "typing-cpu.txt"
        fixture.write_text("Typing CPU benchmark fixture.\n")
        command = command_for(editor, home)
        if editor["id"] == "eclipse":
            workspace_index = command.index("-data") + 1
            command[workspace_index] = str(fixture.parent)
        if editor["id"] == "basic-electron" and input_driver == "ydotool":
            command.remove("--ozone-platform=x11")
            command.append("--ozone-platform=wayland")
        if editor["id"] == "eclipse":
            command[1:1] = ["--launcher.openFile", str(fixture)]
        elif editor["id"] in {"idea", "theia"}:
            command.append(str(fixture.parent))
        else:
            command.append(str(fixture))
        stdout_path = home / "stdout.log"
        stderr_path = home / "stderr.log"
        process = None
        try:
            if editor["id"] == "cursor":
                prepare_cursor_profile(editor, home, environment,
                                       home / "cursor-bootstrap.stdout.log",
                                       home / "cursor-bootstrap.stderr.log")
            with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
                process = subprocess.Popen(command, cwd=workspace, env=environment,
                                           stdout=stdout, stderr=stderr,
                                           start_new_session=True)
            if editor["id"] == "cursor":
                wait_for_cursor_workbench(fixture)
            time.sleep(settle_seconds)
            if process.poll() is not None:
                raise RuntimeError(f"editor exited during startup ({process.returncode})")
            if editor["id"] == "cursor":
                wait_for_cursor_workbench(fixture, timeout=1)
            window_patterns = {
                "atom": "typing-cpu",
                "eclipse": "Eclipse SDK",
                "idea": ("IntelliJ", "Data Sharing", "typing-cpu"),
                "lapce": "Lapce",
                "lvce": "typing-cpu",
                "theia": "typing-cpu",
            }
            click_positions = {
                "basic-electron": (200, 160),
                "lapce": (500, 150),
                "lvce": (200, 160),
                "theia": (500, 150),
            }
            keyboard = make_input(input_driver, fixture.name,
                                  window_patterns.get(editor["id"], fixture.name),
                                  click_positions.get(editor["id"], (800, 250)))
            if editor["id"] == "idea":
                keyboard.accept_idea_onboarding()
                time.sleep(10)
                keyboard.click_editor((800, 250))
            if editor["id"] == "eclipse":
                keyboard.close_welcome()
                keyboard.click_editor((500, 155))
            if editor["id"] == "theia":
                keyboard.press_key("Return")
                time.sleep(.5)
            if editor["id"] == "idea":
                keyboard.open_idea_file(fixture.name)
            elif editor["id"] == "zed":
                keyboard.open_file(fixture.name)
            if editor["id"] == "idea":
                keyboard.click_editor((800, 250))
            if editor["id"] == "theia":
                keyboard.open_selected_file()
            if editor["id"] == "theia":
                keyboard.click_editor(click_positions["theia"])
            keyboard.clear()
            time.sleep(.5)
            measurement = typing_measurement(process, keyboard, fixture,
                                             sample_seconds, cadence_seconds)
            measurement.update({"valid": True, "pid": process.pid})
            return measurement
        except (FileNotFoundError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return {
                "valid": False,
                "error": str(error),
                "pid": process.pid if process else None,
                "stdout": stdout_path.read_text(errors="replace")[-4000:],
                "stderr": stderr_path.read_text(errors="replace")[-4000:],
            }
        finally:
            terminate_process_group(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--settle-seconds", type=float, default=10)
    parser.add_argument("--sample-seconds", type=float, default=180)
    parser.add_argument("--cadence-seconds", type=float, default=1)
    parser.add_argument("--input-driver", choices=("xdotool", "ydotool"), default="xdotool")
    parser.add_argument("--output", type=Path, default=ROOT / "results/results.json")
    args = parser.parse_args()
    if args.repeats < 1 or args.settle_seconds < 0 or args.sample_seconds <= 0 or args.cadence_seconds <= 0:
        parser.error("repeats must be positive; duration and typing cadence must be positive")
    editor = load_editor(args.editor)
    results = []
    for repeat in range(args.repeats):
        print(f"Trial {repeat + 1}/{args.repeats}: {args.editor}", flush=True)
        result = trial(editor, args.settle_seconds, args.sample_seconds,
                       args.input_driver, args.cadence_seconds)
        result["repeat"] = repeat + 1
        results.append(result)
    payload = {
        "editor": editor,
        "protocol": {"settleSeconds": args.settle_seconds, "typingDurationSeconds": args.sample_seconds,
                      "typingCadenceSeconds": args.cadence_seconds, "inputDriver": args.input_driver,
                      "definition": "100% is one fully busy logical CPU",
                      "memoryDefinition": "sum of resident set size for the editor process tree",
                      "descendants": True},
        "host": {"platform": platform.platform(), "machine": platform.machine(),
                 "python": platform.python_version(), "cpuCount": os.cpu_count()},
        "trials": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(args.output)
    if not all(result["valid"] for result in results):
        raise SystemExit("one or more trials were invalid")


if __name__ == "__main__":
    main()
