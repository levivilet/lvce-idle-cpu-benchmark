"""CPU counter and process-tree accounting primitives."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CpuStat:
    usage_usec: int
    user_usec: int
    system_usec: int


def read_cpu_stat(path: str | Path) -> CpuStat:
    values = {}
    for line in Path(path).read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in {"usage_usec", "user_usec", "system_usec"}:
            values[fields[0]] = int(fields[1])
    required = {"usage_usec", "user_usec", "system_usec"}
    if required - values.keys() or any(value < 0 for value in values.values()):
        raise ValueError("cpu.stat is missing a non-negative CPU counter")
    return CpuStat(values["usage_usec"], values["user_usec"], values["system_usec"])


def counter_delta(before: CpuStat, after: CpuStat) -> CpuStat:
    delta = CpuStat(after.usage_usec - before.usage_usec,
                    after.user_usec - before.user_usec,
                    after.system_usec - before.system_usec)
    if min(delta.usage_usec, delta.user_usec, delta.system_usec) < 0:
        raise ValueError("CPU counter moved backwards")
    if delta.user_usec + delta.system_usec > delta.usage_usec:
        raise ValueError("CPU counter components exceed total")
    return delta


def utilization_percent(cpu_usec: int, elapsed_seconds: float) -> float:
    if cpu_usec < 0 or elapsed_seconds <= 0:
        raise ValueError("CPU time and elapsed interval must be positive")
    return cpu_usec / (elapsed_seconds * 1_000_000) * 100


def _stat(path: Path) -> tuple[int, int]:
    text = path.read_text()
    end = text.rfind(")")
    fields = text[end + 2:].split()
    if len(fields) < 20:
        raise ValueError(f"invalid process stat: {path}")
    # fields[1] is ppid and fields[11]/[12] are utime/stime after state.
    return int(fields[1]), int(fields[11]) + int(fields[12])


def process_tree(root_pid: int, proc_root: str | Path = "/proc") -> dict[int, int]:
    root = Path(proc_root)
    parents: dict[int, int] = {}
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parent, _ = _stat(entry / "stat")
        except (FileNotFoundError, PermissionError, ValueError):
            continue
        parents[int(entry.name)] = parent
    result = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in result and pid not in result:
                result.add(pid)
                changed = True
    counters = {}
    for pid in result:
        try:
            counters[pid] = _stat(root / str(pid) / "stat")[1]
        except (FileNotFoundError, PermissionError, ValueError):
            if pid == root_pid:
                raise ValueError("root process disappeared")
    return counters


def ticks_to_usec(ticks: int, ticks_per_second: int) -> int:
    if ticks < 0 or ticks_per_second <= 0:
        raise ValueError("invalid process tick counter")
    return ticks * 1_000_000 // ticks_per_second


def measure_process_tree(root_pid: int, elapsed_seconds: float, sample_interval: float = .1,
                         proc_root: str | Path = "/proc", ticks_per_second: int = 100) -> int:
    """Return CPU microseconds observed while sampling a process tree.

    A running total is kept for each PID so short-lived descendants are included
    up to their last observation instead of being mistaken for zero usage.
    """
    if elapsed_seconds <= 0 or sample_interval <= 0:
        raise ValueError("measurement interval must be positive")
    import time
    deadline = time.monotonic() + elapsed_seconds
    previous: dict[int, int] = {}
    total_ticks = 0
    while True:
        current = process_tree(root_pid, proc_root)
        for pid, ticks in current.items():
            if pid in previous:
                delta = ticks - previous[pid]
                if delta < 0:
                    raise ValueError("process CPU counter moved backwards")
                total_ticks += delta
            previous[pid] = ticks
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(sample_interval, remaining))
    return ticks_to_usec(total_ticks, ticks_per_second)


def aggregate_cpu(before: CpuStat, after: CpuStat, elapsed_seconds: float) -> dict:
    delta = counter_delta(before, after)
    return {
        "cpuUsec": delta.usage_usec,
        "userUsec": delta.user_usec,
        "systemUsec": delta.system_usec,
        "elapsedSeconds": elapsed_seconds,
        "utilizationPercent": utilization_percent(delta.usage_usec, elapsed_seconds),
    }
