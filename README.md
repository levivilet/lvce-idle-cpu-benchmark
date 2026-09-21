# LVCE idle CPU benchmark

Reproducible idle CPU measurements for LVCE Editor and a small comparison set of
desktop editors. The benchmark downloads exact, checksum-pinned releases and
runs one editor per GitHub Actions runner.

This is an observation tool, not a ranking. Hosted runners are shared machines,
so results from different runs and editors are only comparable when the runner
image, protocol, and capture date are considered together.

## Measurement protocol

- Linux x86-64, Ubuntu 24.04, X11/Xvfb, one editor per runner.
- Downloads are described by `config/editors.lock.json`; every archive is
  verified before extraction. Profiles and XDG directories are temporary.
- Startup and settling happen before the idle interval. No keyboard, mouse, or
  file workload is sent during the interval.
- CPU time includes the editor's descendants. When a delegated cgroup is
  supplied through `CPU_CGROUP_PATH`, the benchmark reads its `cpu.stat` usage
  counter. GitHub-hosted runners normally do not provide a writable delegated
  cgroup, so CI uses a descendant-aware `/proc` CPU-tick sampler instead.
- 100% means one fully busy logical CPU for the whole interval. For example,
  250 ms of CPU time over a one-second interval is 25%. Values above 100% are
  valid when an application uses more than one logical CPU.
- An invalid counter, a backwards counter, an impossible interval, or an editor
  that exits during capture is recorded as an invalid trial; it is never
  presented as zero CPU usage. Raw counters, elapsed time, process source, and
  host metadata are retained in the JSON artifact.

The `/proc` fallback samples the complete process tree repeatedly and carries
forward the last observed CPU total when a short-lived child exits between
samples. This is less authoritative than cgroup accounting and is documented
as such in every result.

## Run locally

```sh
sudo apt-get update
sudo apt-get install -y python3 curl xz-utils xvfb xauth openbox \
  libgtk-3-0 libnss3 libgbm1 libxss1 libxtst6 libxkbcommon-x11-0 \
  mesa-utils mesa-vulkan-drivers libvulkan1 libasound2t64 openjdk-21-jre
python3 scripts/install.py
bash scripts/run.sh --editor lvce --repeats 3
python3 -m unittest discover -s tests
```

Use `python3 scripts/benchmark.py --help` for interval and editor options.
`CPU_CGROUP_PATH` can point at a readable cgroup directory containing
`cpu.stat`; otherwise the process-tree sampler is selected automatically.

## CI

Pull requests download and smoke-run every locked editor in a matrix, then run
the accounting tests. Pushes and scheduled runs use three fresh trials per
editor and upload raw JSON artifacts. The workflow intentionally does not claim
that a benchmark is complete when a trial is invalid.
