import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from conftest import ROOT

RENDER = ROOT / "tools" / "render_piece.py"
BUILD = ROOT / "tools" / "build_site.py"


def test_render_contract(good: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(RENDER), str(good), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    webp = Image.open(out / "preview.webp")
    assert webp.size == (540, 960) and webp.n_frames == 20
    assert Image.open(out / "og.jpg").size == (540, 960)
    stats = json.loads((out / "stats.json").read_text())
    assert {"motion_median", "seam", "void"} <= stats.keys()
    assert stats["seam"] in ("CLEAN", "CHECK", "JUMP") and stats["fps"] == 10.0 and stats["loop_s"] == 2.0
    assert (out / "process" / "01.webp").exists()


def test_render_timeout_kills_runaway(good: Path, tmp_path: Path) -> None:
    (good / "piece.py").write_text("x = 0\nfor i in range(10 ** 12):\n    x += i\n" +
                                   (good / "piece.py").read_text())
    r = subprocess.run([sys.executable, str(RENDER), str(good), "--out", str(tmp_path / "o"), "--timeout", "3"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 124 and "wall clock" in r.stderr


def test_render_missing_piece(tmp_path: Path) -> None:
    r = subprocess.run([sys.executable, str(RENDER), str(tmp_path), "--out", str(tmp_path / "o")],
                       capture_output=True, text=True)
    assert r.returncode != 0


def test_build_site_contract(good: Path, tmp_path: Path) -> None:
    pieces = tmp_path / "pieces"
    d = pieces / "alex" / "ember"
    d.parent.mkdir(parents=True)
    shutil.copytree(good, d)
    (d / "meta.yaml").write_text((d / "meta.yaml").read_text() + 'handle: "alex"\ntokens_source: "user"\n')
    (d / "notes.md").write_text("# ember\n\n## direction\n\n- seed: embers\n")
    cur = tmp_path / "curation.json"
    cur.write_text(json.dumps({"week": "2026-W40", "theme": {"title": "first light", "blurb": "b"},
                               "picks": ["alex/ember"]}))
    build = tmp_path / "build"
    r = subprocess.run([sys.executable, str(BUILD), "--render", "--build", str(build), "--pieces", str(pieces),
                        "--curation", str(cur)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads((build / "community.json").read_text())
    assert doc["week"] == {"label": "2026-W40", "views": 0} and doc["theme"]["title"] == "first light"
    assert doc["totals"] == {"pieces": 1, "artists": 1, "tokens": 0}
    p = doc["pieces"][0]
    assert p["created"] is None or (len(p["created"]) == 20 and p["created"].endswith("Z") and "T" in p["created"])
    assert set(p) == {"id", "handle", "slug", "title", "description", "model", "model_label", "house_artist",
                      "human_role", "tokens", "iterations", "size", "loop_s", "license", "created", "pick", "preview",
                      "og", "source", "process", "stats"}
    assert p["id"] == "alex/ember" and p["pick"] is True and p["model_label"] == "Claude Opus 5.5"
    assert p["human_role"] == "seeded" and p["house_artist"] is False and p["tokens"] is None
    assert p["process"] == [{"label": "iteration 1", "image": "alex/ember/process/01.webp", "note": ""}]
    for key in ("preview", "og", "source"):
        assert (build / p[key]).exists()
    assert set(p["stats"]) == {"motion_median", "seam", "void"}


def test_build_site_rejects_bad_piece(good: Path, tmp_path: Path) -> None:
    d = tmp_path / "pieces" / "alex" / "ember"
    d.parent.mkdir(parents=True)
    shutil.copytree(good, d)  # no handle in meta → layout check fails
    r = subprocess.run([sys.executable, str(BUILD), "--build", str(tmp_path / "b"), "--pieces",
                        str(tmp_path / "pieces")], capture_output=True, text=True)
    assert r.returncode == 1 and "handle" in r.stderr


def test_build_site_week_label_accepts_both_curation_shapes() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_site", BUILD)
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    assert bs.week_label("2026-W40") == "2026-W40"
    assert bs.week_label({"label": "2026-W41", "views": 9}) == "2026-W41"
    assert bs.week_label(None) == "" and bs.week_label({"views": 1}) == "" and bs.week_label(40) == ""



def test_build_site_created_is_full_utc_timestamp() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_site", BUILD)
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    assert bs.created_utc("2026-10-02") == "2026-10-02T00:00:00Z"  # house meta has a date only: midnight UTC
    assert bs.created_utc("2026-10-02T23:14:03+02:00") == "2026-10-02T21:14:03Z"
    assert bs.created_utc(None) is None and bs.created_utc("") is None and bs.created_utc("Oct 2") is None
