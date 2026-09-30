# LVCE desktop typing benchmark

Reproducible CPU and resident-memory measurements while typing in LVCE Editor
and a comparison set of desktop editors. The benchmark downloads exact,
checksum-pinned releases and runs one editor per GitHub Actions runner.

This is an observation tool, not a ranking. Hosted runners are shared machines,
so results from different runs and editors are only comparable when the runner
image, protocol, and capture date are considered together.

The latest complete benchmark run is published at
[GitHub Pages](https://levivilet.github.io/lvce-idle-cpu-benchmark/). The report
compares median CPU utilization and resident memory while typing, and includes
each trial, input timing, measurement source, run provenance, and downloadable
raw JSON for every editor. An incomplete or invalid matrix is not published.

## Measurement protocol

- Linux x86-64, Ubuntu 26.04, X11/Xvfb, one editor per runner. Each trial types
  a deterministic ASCII character every second for 180 seconds.
- Downloads are described by `config/editors.lock.json`; every archive is
  verified before extraction. Profiles and XDG directories are temporary.
- Cursor is pinned to 3.22.12. Each trial initializes a fresh profile, seeds the
  pinned version's welcome-state keys in its SQLite storage, then opens the
  benchmark fixture. The trial starts settling only after a visible Cursor
  window title contains the fixture name.
- Startup and settling happen before typing. The default X11 driver focuses the
  visible window whose title contains the fixture name and verifies focus
  before each input. Trials invalidate on focus loss, and the saved file must
  match every scheduled character. `--input-driver ydotool` is available for
  Wayland sessions with a running `ydotoold`.
- CPU time includes the editor's descendants and is sampled from `/proc` CPU
  ticks during typing. 100% means one fully busy logical CPU; values above
  100% are valid when an application uses more than one logical CPU.
- Resident memory is the sum of descendant process RSS, sampled during typing.
  Reports compare median average RSS and median peak RSS in MiB. RSS is process
  memory and does not include every shared-system-memory cost.
- 250 ms of CPU time over a one-second interval is 25%. Values above 100% are
  valid when an application uses more than one logical CPU.
- An invalid counter, backwards counter, missed cadence, focus loss, incomplete
  input, editor exit, or mismatched saved document is an invalid trial, never
  zero CPU or memory. Raw CPU time, RSS measurements, actual input count and
  offsets, elapsed time, and host metadata are retained in each JSON artifact.

The `/proc` sampler observes the complete process tree repeatedly, including
short-lived children that remain visible between samples.

## Run locally

```sh
sudo apt-get update
sudo apt-get install -y python3 curl xz-utils xvfb xauth openbox \
  libgtk-3-0 libnss3 libgbm1 libxss1 libxtst6 libxkbcommon-x11-0 \
  mesa-utils mesa-vulkan-drivers libvulkan1 libasound2t64 openjdk-21-jre
python3 scripts/install.py
bash scripts/run.sh --editor lvce --repeats 1 --sample-seconds 180 --cadence-seconds 1
python3 -m unittest discover -s tests
```

Local trials type for 180 seconds by default. Pass `--sample-seconds` and
`--cadence-seconds` to configure duration and input interval.

Use `python3 scripts/benchmark.py --help` for interval and editor options.
Use `--input-driver ydotool` with `YDOTOOL_SOCKET` set in a Wayland session.
X11/Xvfb remains the GitHub Actions mode because that is the validated setup
for the supported editor launch configurations. A local Weston headless plus
ydotool probe could start the compositor and input daemon, but Weston exposed
no keyboard seat, so the fixture-content acceptance check failed. Use a
compositor that accepts the ydotool uinput keyboard before selecting Wayland.

## CI

Every CI trial types for 180 seconds. Pull requests run one trial per
locked editor after 5 seconds of settling, then run the accounting tests. Pushes,
scheduled runs, and manual runs use three fresh trials per editor, each after
10 seconds of settling, and upload raw JSON artifacts. The workflow intentionally does not claim
that a benchmark is complete when a trial is invalid.
