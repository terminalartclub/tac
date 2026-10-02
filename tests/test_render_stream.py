"""The preview renderer streams: bounded memory, no frame retention, byte-identical output."""

import asyncio
import gc
import json
import subprocess
import sys
import weakref
from pathlib import Path

from PIL import Image

import render_piece as rp
import vscreen
from conftest import FIXTURES, ROOT


def _pillow_reference(frames, durs, path: Path) -> None:
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=rp._ms_durations(durs),
                   loop=0, quality=rp.WEBP_QUALITY, method=4)


def test_webp_stream_is_byte_identical_to_pillow(tmp_path: Path) -> None:
    frames = [Image.new("RGB", (54, 96), (i * 20, 10, 200 - i * 9)) for i in range(7)]
    durs = [0.1, 0.1, 0.25, 0.1, 1 / 30, 0.1, 0.07]
    _pillow_reference(frames, durs, tmp_path / "a.webp")
    w = rp.WebPStream(tmp_path / "b.webp")
    for f, d in zip(frames, durs):
        w.add(f, d)
    w.close()
    assert (tmp_path / "a.webp").read_bytes() == (tmp_path / "b.webp").read_bytes()


def test_webp_stream_single_frame_matches_pillow(tmp_path: Path) -> None:
    f = Image.new("RGB", (54, 96), (40, 20, 15))
    _pillow_reference([f], [2.0], tmp_path / "a.webp")
    w = rp.WebPStream(tmp_path / "b.webp")
    w.add(f, 2.0)
    w.close()
    assert (tmp_path / "a.webp").read_bytes() == (tmp_path / "b.webp").read_bytes()


def test_webp_stream_accepts_a_generator_and_drops_frames(tmp_path: Path) -> None:
    refs: list[weakref.ref] = []
    alive_at_yield: list[int] = []

    def frames():
        for i in range(40):
            gc.collect()
            alive_at_yield.append(sum(r() is not None for r in refs))
            im = Image.new("RGB", (540, 960), ((i * 61) % 256, (i * 37) % 256, (i * 91) % 256))
            refs.append(weakref.ref(im))
            yield im

    w = rp.WebPStream(tmp_path / "g.webp")
    for im in frames():
        w.add(im, 0.1)
        del im
    w.close()
    assert Image.open(tmp_path / "g.webp").n_frames == 40
    assert max(alive_at_yield) <= 2  # never more than the held first frame + the one in flight


def test_stream_stats_equal_batch_stats() -> None:
    code = (FIXTURES / "good" / "piece.py").read_text()
    cap = asyncio.run(vscreen.capture(code, 80, 66))
    grids = [vscreen.to_cells(b, 80, 66) for _, b in cap.samples]
    window = max(1, round(0.5 * cap.steps / cap.loop_s))
    st = rp.StreamStats(window, len(grids))
    for g in grids:
        st.add(g)
    assert st.result() == vscreen.stats(grids, window=window)


def test_render_peak_memory_is_bounded(tmp_path: Path) -> None:
    """300 frames at 540x960 held in memory would be ~470 MB of RGB alone; streaming stays far below."""
    d = tmp_path / "long"
    d.mkdir()
    (d / "piece.py").write_text((FIXTURES / "good" / "piece.py").read_text().replace("N, dt = 20,", "N, dt = 300,"))
    (d / "meta.yaml").write_text((FIXTURES / "good" / "meta.yaml").read_text())
    probe = (
        "import resource, subprocess, sys\n"
        "r = subprocess.run(sys.argv[1:])\n"
        "kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss\n"
        "print(kb // 1024 if sys.platform == 'darwin' else kb)\n"  # macOS: bytes, Linux: KB
        "sys.exit(r.returncode)\n"
    )
    out = subprocess.run([sys.executable, "-c", probe, sys.executable, str(ROOT / "tools" / "render_piece.py"),
                          str(d), "--out", str(tmp_path / "o")], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    peak_mb = int(out.stdout.strip().splitlines()[-1]) / 1024
    assert json.loads((tmp_path / "o" / "stats.json").read_text())["frames"] == 300
    assert peak_mb < 300, f"peak RSS {peak_mb:.0f} MB"
