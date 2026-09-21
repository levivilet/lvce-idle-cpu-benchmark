"""Measure idle CPU time for one installed editor."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import tempfile
import time

from metrics import aggregate_cpu, measure_process_tree, read_cpu_stat, utilization_percent

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "config/editors.lock.json"


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
    common = ["--disable-gpu"] if editor["id"] in {"lvce", "vscode", "basic-electron", "theia", "atom"} else []
    if editor["id"] in {"lvce", "vscode", "theia", "atom", "basic-electron"}:
        common += ["--no-sandbox"]
    if editor["id"] in {"lvce", "vscode"}:
        common += ["--user-data-dir", str(home / "profile")]
    if editor["id"] == "vscode":
        common += ["--disable-extensions", "--skip-welcome", "--skip-release-notes"]
    if editor["id"] == "basic-electron":
        common += ["--ozone-platform=x11", str(ROOT / editor["app"])]
    if editor["id"] == "zed":
        common += ["--user-data-dir", str(home / "profile")]
    if editor["id"] == "eclipse":
        common += ["-nosplash", "-data", str(home / "workspace"), "-application", "org.eclipse.ui.ide.workbench"]
    if editor["id"] == "idea":
        common += ["nosplash", "dontReopenProjects", "-e"]
    if editor["id"] == "atom":
        common += ["--new-window"]
    if editor["id"] == "lapce":
        # Lapce launches a detached child unless --wait is supplied.
        common += ["--new", "--wait"]
    return [str(binary), *common]


def cgroup_measurement(editor, elapsed):
    path = os.environ.get("CPU_CGROUP_PATH")
    if not path:
        return None
    stat = Path(path) / "cpu.stat"
    before = read_cpu_stat(stat)
    started = time.monotonic()
    time.sleep(elapsed)
    actual_elapsed = time.monotonic() - started
    after = read_cpu_stat(stat)
    result = aggregate_cpu(before, after, actual_elapsed)
    result["source"] = "cgroup-cpu.stat"
    return result


def process_measurement(process, elapsed):
    started = time.monotonic()
    cpu_usec = measure_process_tree(process.pid, elapsed)
    actual_elapsed = time.monotonic() - started
    return {
        "cpuUsec": cpu_usec,
        "elapsedSeconds": actual_elapsed,
        "utilizationPercent": utilization_percent(cpu_usec, actual_elapsed),
        "source": "proc-process-tree",
    }


def trial(editor, settle_seconds, sample_seconds):
    with tempfile.TemporaryDirectory(prefix=f"idle-cpu-{editor['id']}-") as directory:
        home = Path(directory) / "home"
        for name in ("config", "data", "cache", "state"):
            (home / name).mkdir(parents=True)
        environment = {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_STATE_HOME": str(home / "state"),
            "ELECTRON_NO_ATTACH_CONSOLE": "1",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "GALLIUM_DRIVER": "llvmpipe",
            "ZED_ALLOW_EMULATED_GPU": "1",
            "ELECTRON_OZONE_PLATFORM_HINT": "x11",
        }
        fixture = home / "idle-cpu.txt"
        fixture.write_text("Idle CPU benchmark fixture.\n")
        command = command_for(editor, home)
        if editor["id"] != "theia":
            command.append(str(fixture))
        stdout_path = home / "stdout.log"
        stderr_path = home / "stderr.log"
        process = None
        try:
            with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
                process = subprocess.Popen(command, cwd=home, env=environment,
                                           stdout=stdout, stderr=stderr,
                                           start_new_session=True)
            time.sleep(settle_seconds)
            if process.poll() is not None:
                raise RuntimeError(f"editor exited during startup ({process.returncode})")
            measurement = cgroup_measurement(editor, sample_seconds)
            if measurement is None:
                measurement = process_measurement(process, sample_seconds)
            measurement.update({"valid": True, "pid": process.pid})
            return measurement
        except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
            return {
                "valid": False,
                "error": str(error),
                "pid": process.pid if process else None,
                "stdout": stdout_path.read_text(errors="replace")[-4000:],
                "stderr": stderr_path.read_text(errors="replace")[-4000:],
            }
        finally:
            if process:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--settle-seconds", type=float, default=10)
    parser.add_argument("--sample-seconds", type=float, default=10)
    parser.add_argument("--output", type=Path, default=ROOT / "results/results.json")
    args = parser.parse_args()
    if args.repeats < 1 or args.settle_seconds < 0 or args.sample_seconds <= 0:
        parser.error("repeats must be positive; sample interval must be positive")
    editor = load_editor(args.editor)
    results = []
    for repeat in range(args.repeats):
        print(f"Trial {repeat + 1}/{args.repeats}: {args.editor}", flush=True)
        result = trial(editor, args.settle_seconds, args.sample_seconds)
        result["repeat"] = repeat + 1
        results.append(result)
    payload = {
        "editor": editor,
        "protocol": {"settleSeconds": args.settle_seconds, "sampleSeconds": args.sample_seconds,
                      "definition": "100% is one fully busy logical CPU", "descendants": True},
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
