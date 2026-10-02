import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import nudge
from conftest import ROOT

SCRIPTS = ROOT / "plugins" / "tac-studio" / "scripts"
NOW = 1_800_000_000.0


def cache(pct: float, reset_in: float, age: float = 60) -> dict:
    return {"seven_day": {"used_percentage": pct, "resets_at": NOW + reset_in}, "updated_at": NOW - age}


@pytest.mark.parametrize("data,pct,expected", [
    (cache(58, 11 * 3600 + 120), 15, "weekly window ~58% used · resets in 11h · fits ~2 full pieces → /tac:create"),
    (cache(58, 11 * 3600 + 120), 40, "weekly window ~58% used · resets in 11h · fits ~1 full piece → /tac:create"),
    (cache(70, 30 * 60), 50, "weekly window ~70% used · resets in 30m · fits a sketch → /tac:create --sketch"),
    (cache(70, 30 * 60), 90, None),          # not even a sketch fits
    (cache(80, 3600), 15, None),             # used ≥ 80%
    (cache(20, 25 * 3600), 15, None),        # reset > 24h away
    (cache(20, -60), 15, None),              # already reset
    (cache(20, 3600, age=8 * 86400), 15, None),  # stale cache
    ({}, 15, None),
])
def test_message(data: dict, pct: float, expected: str | None) -> None:
    assert nudge.message(data, NOW, pct) == expected


def test_piece_pct_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAC_PIECE_PCT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert nudge.piece_pct() == nudge.DEFAULT_PIECE_PCT
    (tmp_path / "tac").mkdir()
    (tmp_path / "tac" / "config.json").write_text('{"piece_pct": 6}')
    assert nudge.piece_pct() == 6.0
    monkeypatch.setenv("TAC_PIECE_PCT", "9.5")
    assert nudge.piece_pct() == 9.5


def run_hook(tmp_path: Path, data: dict | None) -> str:
    env = {"XDG_CACHE_HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "HOME": str(tmp_path),
           "PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")}
    if data is not None:
        (tmp_path / "tac").mkdir(exist_ok=True)
        (tmp_path / "tac" / "usage.json").write_text(json.dumps(data))
    r = subprocess.run([sys.executable, str(SCRIPTS / "nudge.py")], input="{}", capture_output=True, text=True, env=env)
    assert r.returncode == 0
    return r.stdout


def test_hook_silent_without_cache(tmp_path: Path) -> None:
    assert run_hook(tmp_path, None) == ""
    assert (tmp_path / "data" / "statusline_cache.py").exists()  # helper synced to a stable path


def test_hook_prints_system_message(tmp_path: Path) -> None:
    now = time.time()
    out = run_hook(tmp_path, {"seven_day": {"used_percentage": 42.4, "resets_at": now + 5 * 3600 + 60},
                              "updated_at": now})
    assert json.loads(out) == {"systemMessage": "weekly window ~42% used · resets in 5h · fits ~3 full pieces → /tac:create"}


def test_statusline_helper_caches_and_passes_through(tmp_path: Path) -> None:
    payload = {"model": {"display_name": "Opus"},
               "rate_limits": {"seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}}}
    env = {"XDG_CACHE_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(SCRIPTS / "statusline_cache.py"), "--", "cat"],
                       input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert r.returncode == 0 and json.loads(r.stdout) == payload  # the user's command saw the same stdin
    cached = json.loads((tmp_path / "tac" / "usage.json").read_text())
    assert cached["seven_day"] == {"used_percentage": 41.2, "resets_at": 1738857600}


def test_statusline_helper_without_rate_limits_writes_nothing(tmp_path: Path) -> None:
    env = {"XDG_CACHE_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(SCRIPTS / "statusline_cache.py")], input="{}",
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout == ""
    assert not (tmp_path / "tac" / "usage.json").exists()
