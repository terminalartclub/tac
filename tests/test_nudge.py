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
    (cache(58, 11 * 3600 + 120), 15, "tac: room for a piece before your weekly reset (~58% used, resets in 11h, fits ~2 full pieces). Make something for the wall: /tac:create"),
    (cache(58, 11 * 3600 + 120), 40, "tac: room for a piece before your weekly reset (~58% used, resets in 11h, fits ~1 full piece). Make something for the wall: /tac:create"),
    (cache(70, 30 * 60), 50, "tac: room for a sketch before your weekly reset (~70% used, resets in 30m, fits a sketch). Make something for the wall: /tac:create --sketch"),
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
    uv_dir = tmp_path / "uvbin"  # uv is installed here (the missing-uv line has its own tests)
    uv_dir.mkdir(exist_ok=True)
    for tool in ("uv", "python3.12"):  # and a Python for it (the first-run line has its own tests)
        (uv_dir / tool).write_text("#!/bin/sh\n")
        (uv_dir / tool).chmod(0o755)
    env = {"XDG_CACHE_HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "HOME": str(tmp_path),
           "PATH": f"{uv_dir}:/usr/bin:/bin", "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")}
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
    assert json.loads(out) == {"systemMessage": "tac: room for a piece before your weekly reset (~42% used, resets in 5h, fits ~3 full pieces). Make something for the wall: /tac:create"}


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


def test_hook_never_overwrites_existing_statusline_helper(tmp_path: Path) -> None:
    helper = tmp_path / "data" / "statusline_cache.py"
    assert run_hook(tmp_path, None) == ""  # missing → copied, silently
    assert helper.read_bytes() == (SCRIPTS / "statusline_cache.py").read_bytes()
    assert run_hook(tmp_path, None) == ""  # identical → nothing to say
    helper.write_text("# the user's reviewed (older) copy\n")
    out = run_hook(tmp_path, None)
    assert helper.read_text() == "# the user's reviewed (older) copy\n"  # left alone
    msg = json.loads(out)["systemMessage"]
    assert "\n" not in msg and "differs from this plugin's version" in msg and str(helper) in msg


@pytest.mark.parametrize("path", [SCRIPTS / "nudge.py", SCRIPTS / "session_env.py",
                                  ROOT / "plugins" / "tac-studio" / "lib" / "uvfind.py"])
def test_hook_makes_no_network_calls_and_runs_nothing(path: Path) -> None:
    """Everything a SessionStart hook loads: no network module, no subprocess, no os.system/exec/spawn."""
    import ast

    tree = ast.parse(path.read_text())
    banned = {"urllib", "http", "socket", "requests", "httpx", "subprocess", "asyncio", "multiprocessing"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned, ast.dump(node)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned, ast.dump(node)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
            assert not (node.attr in {"system", "popen", "fork", "forkpty"} or node.attr.startswith(("exec", "spawn"))), node.attr


@pytest.mark.parametrize("sid,written", [("d4eac668-7812-49a5-876a-0edb5cddbf89", True), ("x; rm -rf ~", False),
                                         ("$(id)", False), (None, False), ("abc\n", False),
                                         ("abc\nexport PATH=/tmp", False)])
def test_session_env_hook_exports_only_a_safe_id(tmp_path: Path, sid, written) -> None:
    env_file = tmp_path / "env"
    env_file.write_text("export OTHER=1\n")
    payload = json.dumps({"session_id": sid, "hook_event_name": "SessionStart"} if sid is not None else {})
    r = subprocess.run([sys.executable, str(SCRIPTS / "session_env.py")], input=payload, capture_output=True,
                       text=True, env={"CLAUDE_ENV_FILE": str(env_file), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout == ""
    lines = env_file.read_text().splitlines()
    assert lines[0] == "export OTHER=1"  # appended, never overwritten
    assert lines[1:] == ([f"export TAC_SESSION_ID={sid}"] if written else [])


def test_session_env_hook_registered_for_every_session_start() -> None:
    hooks = json.loads((ROOT / "plugins" / "tac-studio" / "hooks" / "hooks.json").read_text())["hooks"]["SessionStart"]
    entry = next(h for h in hooks if "session_env.py" in h["hooks"][0]["command"])
    assert "matcher" not in entry  # startup, resume, clear and compact all re-export the id
