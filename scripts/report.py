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
    for index, trial in enumerate(trials, 1):
        if not isinstance(trial, dict) or trial.get("valid") is not True:
            raise ValueError(f"{editor_id}: trial {index} is invalid")
        for key in ("cpuUsec", "elapsedSeconds", "utilizationPercent"):
            value = trial.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{editor_id}: trial {index} has invalid {key}")
        if trial["cpuUsec"] < 0 or trial["elapsedSeconds"] <= 0 or trial["utilizationPercent"] < 0:
            raise ValueError(f"{editor_id}: trial {index} has out-of-range measurements")
        if not isinstance(trial.get("source"), str) or not trial["source"]:
            raise ValueError(f"{editor_id}: trial {index} is missing its measurement source")
    if not isinstance(data.get("protocol"), dict) or not isinstance(data.get("host"), dict):
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
    width, height = 1000, 470
    left, right, top, bottom = 64, 24, 38, 326
    plot_width, plot_height = width - left - right, bottom - top
    maximum = max((entry["medianUtilizationPercent"] for entry in entries), default=0)
    # Leave headroom above the largest observation and keep the all-zero chart
    # useful, while never clipping measurements above 100%.
    axis_max = max(1, math.ceil(maximum * 1.1))
    tick_count = 4
    x_step = plot_width / max(1, len(entries))

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="chart-title chart-description" xmlns="http://www.w3.org/2000/svg">',
        '<title id="chart-title">Median idle CPU utilization by editor</title>',
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

    for index, entry in enumerate(entries):
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
        filename = f"{editor['id']}.json"
        (raw_dir / filename).write_text(json.dumps(data, indent=2) + "\n")
        entry = {
            "id": editor["id"], "name": editor["name"], "version": editor["version"],
            "medianUtilizationPercent": median,
            "trials": [{"repeat": trial.get("repeat"), "utilizationPercent": trial["utilizationPercent"],
                        "cpuUsec": trial["cpuUsec"], "elapsedSeconds": trial["elapsedSeconds"],
                        "source": trial["source"]} for trial in trials],
            "protocol": data["protocol"], "host": data["host"], "raw": f"raw/{filename}",
        }
        entries.append(entry)
        values = "<br>".join(f"{trial['utilizationPercent']:.2f}%" for trial in trials)
        sources = ", ".join(sorted({html.escape(trial["source"]) for trial in trials}))
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(editor['name'])}</th>"
            f"<td>{html.escape(editor['version'])}</td>"
            f"<td>{median:.2f}%</td><td>{values}</td><td>{sources}</td>"
            f"<td><a href=\"raw/{html.escape(filename)}\" download>Download JSON</a></td></tr>"
        )
    report = {"complete": True, "runUrl": run_url, "commit": commit, "editors": entries}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    chart = build_median_chart(entries)
    safe_run_url = html.escape(run_url, quote=True)
    safe_commit = html.escape(commit)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LVCE idle CPU benchmark</title><style>
:root{{font:16px/1.5 system-ui,sans-serif;color:#17212b;background:#f4f7fa}}body{{max-width:1100px;margin:3rem auto;padding:0 1rem}}
h1{{line-height:1.15}}.note{{padding:1rem;background:#e8f1fa;border-radius:.5rem}}.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;background:white;margin:1.5rem 0}}th,td{{padding:.7rem;border-bottom:1px solid #d7e0e8;text-align:left;vertical-align:top}}
th{{background:#e8f1fa}}code{{overflow-wrap:anywhere}}a{{color:#0759a5}}
.chart-scroll{{overflow-x:auto;background:white;border:1px solid #d7e0e8;border-radius:.5rem;margin:1.5rem 0}}
.chart{{display:block;width:100%;min-width:760px;height:auto}}.grid{{stroke:#d7e0e8;stroke-width:1}}
.tick,.axis-label,.editor,.value{{font:12px system-ui,sans-serif;fill:#17212b}}.axis-label{{font-size:13px;font-weight:600}}
.stem{{stroke:#4780ad;stroke-width:2}}.marker{{fill:#0759a5;stroke:white;stroke-width:2}}.value{{font-weight:600}}
</style></head><body><main><h1>LVCE idle CPU benchmark</h1>
<p>Latest complete run: <a href="{safe_run_url}">GitHub Actions run</a> · commit <code>{safe_commit}</code></p>
<p class="note">CPU utilization is measured across each editor process tree. 100% means one fully busy logical CPU; values above 100% are valid. Each value below is a measured trial, and the summary is its median. Measurement sources and full host and protocol metadata are available in each downloadable JSON file.</p>
<p>All {len(entries)} editors completed with valid measurements.</p>
<section aria-labelledby="chart-heading"><h2 id="chart-heading">Median idle CPU utilization by editor</h2>
<p>Each marker uses the same vertical scale; lower values appear closer to zero at the bottom. Scroll the chart horizontally on narrow screens.</p>
<div class="chart-scroll">{chart}</div></section>
<div class="scroll"><table><thead><tr><th>Editor</th><th>Version</th><th>Median CPU</th><th>Trials</th><th>Measurement source</th><th>Raw data</th></tr></thead><tbody>
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
