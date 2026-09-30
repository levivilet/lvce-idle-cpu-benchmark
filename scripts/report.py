"""Build a static, validated report from the editor matrix artifacts."""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent.parent
EDITORS = json.loads((ROOT / "config/editors.lock.json").read_text())


def validate_result(data: dict, editor_id: str) -> dict:
    """Reject incomplete measurements instead of interpreting them as zero."""
    if not isinstance(data, dict) or not isinstance(data.get("editor"), dict) or data["editor"].get("id") != editor_id:
        raise ValueError(f"{editor_id}: result has the wrong editor identity")
    trials = data.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError(f"{editor_id}: result has no trials")
    protocol = data.get("protocol")
    if not isinstance(protocol, dict):
        raise ValueError(f"{editor_id}: result is missing protocol metadata")
    duration = protocol.get("typingDurationSeconds")
    protocol_cadence = protocol.get("typingCadenceSeconds")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0
            or isinstance(protocol_cadence, bool) or not isinstance(protocol_cadence, (int, float))
            or protocol_cadence <= 0):
        raise ValueError(f"{editor_id}: result has invalid typing protocol")
    for index, trial in enumerate(trials, 1):
        if not isinstance(trial, dict) or trial.get("valid") is not True:
            raise ValueError(f"{editor_id}: trial {index} is invalid")
        for key in ("cpuUsec", "elapsedSeconds", "utilizationPercent"):
            value = trial.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{editor_id}: trial {index} has invalid {key}")
        if trial["cpuUsec"] < 0 or trial["elapsedSeconds"] <= 0 or trial["utilizationPercent"] < 0:
            raise ValueError(f"{editor_id}: trial {index} has out-of-range measurements")
        cpu_ticks, ticks_per_second = trial.get("cpuTicks"), trial.get("ticksPerSecond")
        if (isinstance(cpu_ticks, bool) or not isinstance(cpu_ticks, int) or cpu_ticks < 0
                or isinstance(ticks_per_second, bool) or not isinstance(ticks_per_second, int)
                or ticks_per_second <= 0):
            raise ValueError(f"{editor_id}: trial {index} has invalid raw CPU counters")
        expected_cpu_usec = cpu_ticks * 1_000_000 // ticks_per_second
        expected_utilization = expected_cpu_usec / (trial["elapsedSeconds"] * 1_000_000) * 100
        if (expected_cpu_usec != trial["cpuUsec"]
                or not math.isclose(expected_utilization, trial["utilizationPercent"], rel_tol=.01, abs_tol=.001)):
            raise ValueError(f"{editor_id}: trial {index} has inconsistent CPU counters")
        if not isinstance(trial.get("source"), str) or not trial["source"]:
            raise ValueError(f"{editor_id}: trial {index} is missing its measurement source")
        for key in ("averageRssKb", "peakRssKb", "memoryElapsedSeconds"):
            value = trial.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{editor_id}: trial {index} has invalid {key}")
        if trial["averageRssKb"] < 0 or trial["peakRssKb"] < trial["averageRssKb"] or trial["memoryElapsedSeconds"] <= 0:
            raise ValueError(f"{editor_id}: trial {index} has out-of-range memory measurements")
        offsets = trial.get("inputOffsetsSeconds")
        cadence = trial.get("cadenceSeconds")
        if (not isinstance(offsets, list) or trial.get("inputCount") != len(offsets)
                or len(offsets) < 2 or trial.get("savedContentVerified") is not True
                or isinstance(cadence, bool) or not isinstance(cadence, (int, float)) or cadence <= 0
                or cadence != protocol_cadence or trial["inputCount"] != math.ceil(duration / protocol_cadence)
                or trial["elapsedSeconds"] < duration):
            raise ValueError(f"{editor_id}: trial {index} has invalid typing evidence")
        if any(isinstance(offset, bool) or not isinstance(offset, (int, float))
               or not math.isfinite(offset) for offset in offsets):
            raise ValueError(f"{editor_id}: trial {index} has invalid input timing")
        if any(gap <= 0 or abs(gap - cadence) > .5
               for gap in (following - current for current, following in zip(offsets, offsets[1:]))):
            raise ValueError(f"{editor_id}: trial {index} missed the typing cadence")
        rss_samples = trial.get("rssSamplesKb")
        if (not isinstance(rss_samples, list) or len(rss_samples) < 2
                or trial.get("memorySamples") != len(rss_samples)):
            raise ValueError(f"{editor_id}: trial {index} has invalid raw memory samples")
        if any(not isinstance(sample, list) or len(sample) != 2
               or isinstance(sample[0], bool) or not isinstance(sample[0], (int, float))
               or not math.isfinite(sample[0]) or isinstance(sample[1], bool)
               or not isinstance(sample[1], int) or sample[1] < 0 for sample in rss_samples):
            raise ValueError(f"{editor_id}: trial {index} has malformed RSS samples")
        memory_elapsed = rss_samples[-1][0] - rss_samples[0][0]
        if (memory_elapsed <= 0 or any(second[0] <= first[0]
                                      for first, second in zip(rss_samples, rss_samples[1:]))
                or not math.isclose(memory_elapsed, trial["memoryElapsedSeconds"], rel_tol=.01)
                or max(sample[1] for sample in rss_samples) != trial["peakRssKb"]):
            raise ValueError(f"{editor_id}: trial {index} has inconsistent RSS samples")
        average_rss = sum((second[0] - first[0]) * first[1]
                          for first, second in zip(rss_samples, rss_samples[1:])) / memory_elapsed
        if not math.isclose(average_rss, trial["averageRssKb"], rel_tol=.01):
            raise ValueError(f"{editor_id}: trial {index} has an inconsistent average RSS")
    if not isinstance(data.get("host"), dict):
        raise ValueError(f"{editor_id}: result is missing protocol or host metadata")
    return data


def load_results(results_root: Path) -> list[dict]:
    """Load exactly one valid result for every editor in the checked-in matrix."""
    # upload-artifact stores paths relative to the uploaded directory, so the
    # benchmark's results/results.json becomes results.json in each artifact.
    paths = list(results_root.glob("idle-cpu-*/results.json"))
    found: dict[str, Path] = {}
    for path in paths:
        try:
            data = json.loads(path.read_text())
            editor_id = data["editor"]["id"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"malformed benchmark artifact {path}: {error}") from error
        if path.parent.name != f"idle-cpu-{editor_id}":
            raise ValueError(f"artifact directory does not match editor {editor_id}: {path}")
        if editor_id in found:
            raise ValueError(f"duplicate benchmark artifact for {editor_id}")
        found[editor_id] = path
    expected = {editor["id"] for editor in EDITORS}
    if found.keys() != expected:
        missing = sorted(expected - found.keys())
        extra = sorted(found.keys() - expected)
        raise ValueError(f"editor artifacts do not match the matrix (missing: {missing}; extra: {extra})")
    results = []
    for editor in EDITORS:
        editor_id = editor["id"]
        data = validate_result(json.loads(found[editor_id].read_text()), editor_id)
        results.append({"editor": editor, "data": data})
    return results


def build_median_chart(entries: list[dict]) -> str:
    """Render a labeled SVG comparison with lower medians near the bottom."""
    sorted_entries = sorted(entries, key=lambda entry: entry["medianUtilizationPercent"])
    width, height = 1000, 470
    left, right, top, bottom = 64, 24, 38, 326
    plot_width, plot_height = width - left - right, bottom - top
    maximum = max((entry["medianUtilizationPercent"] for entry in entries), default=0)
    # Leave headroom above the largest observation and keep the all-zero chart
    # useful, while never clipping measurements above 100%.
    axis_max = max(1, math.ceil(maximum * 1.1))
    tick_count = 4
    x_step = plot_width / max(1, len(sorted_entries))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="chart-title chart-description" xmlns="http://www.w3.org/2000/svg">',
        '<title id="chart-title">Median CPU utilization while typing by editor</title>',
        '<desc id="chart-description">Each editor is labeled below its marker. '
        'The shared vertical scale starts at zero at the bottom, so lower markers '
        'represent less median CPU utilization. Values are also shown next to markers.</desc>',
    ]
    for tick in range(tick_count + 1):
        value = axis_max * tick / tick_count
        y = bottom - plot_height * tick / tick_count
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}"/>')
        parts.append(f'<text class="tick" x="{left-10}" y="{y+5:.2f}" text-anchor="end">{value:.2f}%</text>')
    parts.append(f'<text class="axis-label" x="{left}" y="{top-14}">CPU utilization (%)</text>')

    for index, entry in enumerate(sorted_entries):
        x = left + x_step * (index + 0.5)
        value = entry["medianUtilizationPercent"]
        y = bottom - (value / axis_max) * plot_height
        name = html.escape(entry["name"])
        label = f'{value:.2f}%'
        parts.append(f'<line class="stem" x1="{x:.2f}" y1="{bottom}" x2="{x:.2f}" y2="{y:.2f}"/>')
        parts.append(f'<circle class="marker" cx="{x:.2f}" cy="{y:.2f}" r="5"><title>{name}: {label}</title></circle>')
        parts.append(f'<text class="value" x="{x:.2f}" y="{max(top+14, y-10):.2f}" text-anchor="middle">{label}</text>')
        parts.append(f'<text class="editor" transform="translate({x:.2f} {bottom+14}) rotate(48)" text-anchor="start">{name}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def build_median_memory_chart(entries: list[dict]) -> str:
    """Render a labeled comparison of median average process-tree RSS."""
    sorted_entries = sorted(entries, key=lambda entry: entry["medianAverageRssKb"])
    width, height = 1000, 470
    left, right, top, bottom = 64, 24, 38, 326
    plot_width, plot_height = width - left - right, bottom - top
    maximum = max((entry["medianAverageRssKb"] for entry in entries), default=0)
    axis_max = max(1, math.ceil(maximum / 1024 / 50) * 50)
    x_step = plot_width / max(1, len(sorted_entries))
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="memory-chart-title memory-chart-description" xmlns="http://www.w3.org/2000/svg">',
        '<title id="memory-chart-title">Median average resident memory while typing by editor</title>',
        '<desc id="memory-chart-description">The shared scale starts at zero; lower markers represent less average resident memory.</desc>',
    ]
    for tick in range(5):
        value = axis_max * tick / 4
        y = bottom - plot_height * tick / 4
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}"/>')
        parts.append(f'<text class="tick" x="{left-10}" y="{y+5:.2f}" text-anchor="end">{value:.0f} MiB</text>')
    parts.append(f'<text class="axis-label" x="{left}" y="{top-14}">Average resident memory (MiB)</text>')
    for index, entry in enumerate(sorted_entries):
        x = left + x_step * (index + .5)
        value = entry["medianAverageRssKb"] / 1024
        y = bottom - value / axis_max * plot_height
        name = html.escape(entry["name"])
        label = f"{value:.1f} MiB"
        parts.append(f'<line class="stem" x1="{x:.2f}" y1="{bottom}" x2="{x:.2f}" y2="{y:.2f}"/>')
        parts.append(f'<circle class="marker" cx="{x:.2f}" cy="{y:.2f}" r="5"><title>{name}: {label}</title></circle>')
        parts.append(f'<text class="value" x="{x:.2f}" y="{max(top+14, y-10):.2f}" text-anchor="middle">{label}</text>')
        parts.append(f'<text class="editor" transform="translate({x:.2f} {bottom+14}) rotate(48)" text-anchor="start">{name}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def build_report(results: list[dict], output: Path, run_url: str, commit: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / "raw"
    raw_dir.mkdir(exist_ok=True)
    entries = []
    rows = []
    for item in results:
        editor, data = item["editor"], item["data"]
        trials = data["trials"]
        median = statistics.median(trial["utilizationPercent"] for trial in trials)
        median_average_rss = statistics.median(trial["averageRssKb"] for trial in trials)
        median_peak_rss = statistics.median(trial["peakRssKb"] for trial in trials)
        filename = f"{editor['id']}.json"
        (raw_dir / filename).write_text(json.dumps(data, indent=2) + "\n")
        entry = {
            "id": editor["id"], "name": editor["name"], "version": editor["version"],
            "medianUtilizationPercent": median,
            "medianAverageRssKb": median_average_rss,
            "medianPeakRssKb": median_peak_rss,
            "trials": [{"repeat": trial.get("repeat"), "utilizationPercent": trial["utilizationPercent"],
                        "cpuUsec": trial["cpuUsec"], "elapsedSeconds": trial["elapsedSeconds"],
                        "averageRssKb": trial["averageRssKb"], "peakRssKb": trial["peakRssKb"],
                        "inputCount": trial["inputCount"], "cadenceSeconds": trial["cadenceSeconds"],
                        "inputOffsetsSeconds": trial["inputOffsetsSeconds"],
                        "source": trial["source"]} for trial in trials],
            "protocol": data["protocol"], "host": data["host"], "raw": f"raw/{filename}",
        }
        entries.append(entry)
        values = "<br>".join(f"{trial['utilizationPercent']:.2f}%" for trial in trials)
        memory_values = "<br>".join(
            f"{trial['averageRssKb'] / 1024:.1f} / {trial['peakRssKb'] / 1024:.1f} MiB"
            for trial in trials
        )
        input_counts = "<br>".join(str(trial["inputCount"]) for trial in trials)
        sources = ", ".join(sorted({html.escape(trial["source"]) for trial in trials}))
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(editor['name'])}</th>"
            f"<td>{html.escape(editor['version'])}</td>"
            f"<td>{median:.2f}%</td><td>{values}</td>"
            f"<td>{median_average_rss / 1024:.1f} / {median_peak_rss / 1024:.1f} MiB</td>"
            f"<td>{memory_values}</td><td>{input_counts}</td><td>{sources}</td>"
            f"<td><a href=\"raw/{html.escape(filename)}\" download>Download JSON</a></td></tr>"
        )
    report = {"complete": True, "runUrl": run_url, "commit": commit, "editors": entries}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    chart = build_median_chart(entries)
    safe_run_url = html.escape(run_url, quote=True)
    safe_commit = html.escape(commit)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LVCE typing benchmark</title><style>
:root{{font:16px/1.5 system-ui,sans-serif;color:#17212b;background:#f4f7fa}}body{{max-width:1100px;margin:3rem auto;padding:0 1rem}}
h1{{line-height:1.15}}.note{{padding:1rem;background:#e8f1fa;border-radius:.5rem}}.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;background:white;margin:1.5rem 0}}th,td{{padding:.7rem;border-bottom:1px solid #d7e0e8;text-align:left;vertical-align:top}}
th{{background:#e8f1fa}}code{{overflow-wrap:anywhere}}a{{color:#0759a5}}
.chart-scroll{{overflow-x:auto;background:white;border:1px solid #d7e0e8;border-radius:.5rem;margin:1.5rem 0}}
.chart{{display:block;width:100%;min-width:760px;height:auto}}.grid{{stroke:#d7e0e8;stroke-width:1}}
.tick,.axis-label,.editor,.value{{font:12px system-ui,sans-serif;fill:#17212b}}.axis-label{{font-size:13px;font-weight:600}}
.stem{{stroke:#4780ad;stroke-width:2}}.marker{{fill:#0759a5;stroke:white;stroke-width:2}}.value{{font-weight:600}}
</style></head><body><main><h1>LVCE desktop typing benchmark</h1>
<p>Latest complete run: <a href="{safe_run_url}">GitHub Actions run</a> · commit <code>{safe_commit}</code></p>
<p class="note">CPU utilization and resident memory are measured across each editor process tree while typing. 100% CPU means one fully busy logical CPU; values above 100% are valid. Each value below is a measured trial, and summaries are medians. Average and peak resident memory are shown in MiB. Measurement sources and full host, input timing, and protocol metadata are available in each downloadable JSON file.</p>
<p>All {len(entries)} editors completed with valid measurements.</p>
<section aria-labelledby="chart-heading"><h2 id="chart-heading">Median CPU utilization while typing by editor</h2>
<p>Each marker uses the same vertical scale; lower values appear closer to zero at the bottom. Scroll the chart horizontally on narrow screens.</p>
<div class="chart-scroll">{chart}</div></section>
<section aria-labelledby="memory-chart-heading"><h2 id="memory-chart-heading">Median average resident memory while typing by editor</h2>
<p>The chart uses a shared scale starting at zero. The table also shows median peak resident memory.</p>
<div class="chart-scroll">{build_median_memory_chart(entries)}</div></section>
<div class="scroll"><table><thead><tr><th>Editor</th><th>Version</th><th>Median CPU</th><th>CPU trials</th><th>Median average / peak memory</th><th>Average / peak memory trials</th><th>Typed characters</th><th>Measurement source</th><th>Raw data</th></tr></thead><tbody>
{''.join(rows)}</tbody></table></div><p><a href="report.json">Download complete report JSON</a></p></main></body></html>
"""
    (output / "index.html").write_text(page)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results/editors")
    parser.add_argument("--output", type=Path, default=ROOT / ".tmp/pages")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--commit", default="")
    args = parser.parse_args()
    results = load_results(args.input)
    build_report(results, args.output, args.run_url, args.commit)
    print(f"Built a complete report for {len(results)} editors at {args.output}")


if __name__ == "__main__":
    main()
