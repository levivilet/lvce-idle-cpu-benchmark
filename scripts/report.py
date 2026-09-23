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
    paths = list(results_root.glob("idle-cpu-*/results/results.json"))
    found: dict[str, Path] = {}
    for path in paths:
        try:
            data = json.loads(path.read_text())
            editor_id = data["editor"]["id"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"malformed benchmark artifact {path}: {error}") from error
        if path.parent.parent.name != f"idle-cpu-{editor_id}":
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
    safe_run_url = html.escape(run_url, quote=True)
    safe_commit = html.escape(commit)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LVCE idle CPU benchmark</title><style>
:root{{font:16px/1.5 system-ui,sans-serif;color:#17212b;background:#f4f7fa}}body{{max-width:1100px;margin:3rem auto;padding:0 1rem}}
h1{{line-height:1.15}}.note{{padding:1rem;background:#e8f1fa;border-radius:.5rem}}.scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;background:white;margin:1.5rem 0}}th,td{{padding:.7rem;border-bottom:1px solid #d7e0e8;text-align:left;vertical-align:top}}
th{{background:#e8f1fa}}code{{overflow-wrap:anywhere}}a{{color:#0759a5}}
</style></head><body><main><h1>LVCE idle CPU benchmark</h1>
<p>Latest complete run: <a href="{safe_run_url}">GitHub Actions run</a> · commit <code>{safe_commit}</code></p>
<p class="note">CPU utilization is measured across each editor process tree. 100% means one fully busy logical CPU; values above 100% are valid. Each value below is a measured trial, and the summary is its median. Measurement sources and full host and protocol metadata are available in each downloadable JSON file.</p>
<p>All {len(entries)} editors completed with valid measurements.</p><div class="scroll"><table><thead><tr><th>Editor</th><th>Version</th><th>Median CPU</th><th>Trials</th><th>Measurement source</th><th>Raw data</th></tr></thead><tbody>
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
