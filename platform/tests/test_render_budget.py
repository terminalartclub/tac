"""The platform's render budget reaches render_piece.py's own --timeout on every backend, a margin shorter
than the backend's kill, so render_piece reports the timeout instead of being killed silently."""

import importlib.util
import sys
from pathlib import Path

import pytest

from tac_platform import renderer as renderer_mod
from tac_platform.config import Settings
from tac_platform.fly_machine import FLY_OVERHEAD_S, FlyMachineRenderer
from tac_platform.sandbox import RunResult

BOOTSTRAP = Path(__file__).resolve().parents[1] / "render-image" / "fly_bootstrap.py"
OK = RunResult(returncode=0, stdout="", stderr="", timed_out=False)


def _timeout_arg(argv: list[str]) -> float:
    return float(argv[argv.index("--timeout") + 1])


def test_defaults_and_inner_margin(monkeypatch):
    for v in ("TAC_RENDER_TIMEOUT_S", "TAC_FLY_RENDER_CPU_KIND", "TAC_FLY_RENDER_CPUS", "TAC_FLY_RENDER_MEMORY_MB"):
        monkeypatch.delenv(v, raising=False)
    s = Settings.from_env()
    assert s.render_timeout_s == 300 and s.render_piece_timeout_s == 280
    assert (s.fly_render_cpu_kind, s.fly_render_cpus, s.fly_render_memory_mb) == ("performance", 1, 2048)
    monkeypatch.setenv("TAC_RENDER_TIMEOUT_S", "180")
    monkeypatch.setenv("TAC_FLY_RENDER_CPU_KIND", "Shared")
    monkeypatch.setenv("TAC_FLY_RENDER_CPUS", "2")
    s = Settings.from_env()
    assert (s.render_piece_timeout_s, s.fly_render_cpu_kind, s.fly_render_cpus) == (160, "shared", 2)
    assert Settings.from_env(render_timeout_s=2.0).render_piece_timeout_s == 1.5  # tiny test budgets: a quarter
    for bad in ({"fly_render_cpu_kind": "gpu"}, {"fly_render_cpus": 0}, {"fly_render_memory_mb": 128},
                {"render_timeout_s": 0.0}):
        with pytest.raises(RuntimeError):
            Settings.from_env(**bad)


async def test_local_backend_passes_the_inner_timeout(tmp_path, monkeypatch):
    seen = {}

    async def fake_run_limited(argv, work, timeout):
        seen.update(argv=argv, timeout=timeout)
        return OK

    monkeypatch.setattr(renderer_mod, "run_limited", fake_run_limited)
    s = Settings.from_env(render_timeout_s=300.0)
    await renderer_mod.LocalRenderer(s).render(tmp_path / "p", tmp_path / "o", tmp_path)
    assert seen["argv"][1].endswith("render_piece.py") and _timeout_arg(seen["argv"]) == 280
    assert seen["timeout"] == 300  # the outer kill: later than render_piece's own


async def test_docker_backend_passes_the_inner_timeout(tmp_path, monkeypatch):
    seen = {}

    async def fake_run(self, argv_tail, piece_dir, out_dir, timeout):
        seen.update(argv=argv_tail, timeout=timeout)
        return OK

    monkeypatch.setattr(renderer_mod.DockerRenderer, "_run", fake_run)
    s = Settings.from_env(renderer="docker", render_timeout_s=300.0)
    await renderer_mod.DockerRenderer(s).render(tmp_path / "p", tmp_path / "o", tmp_path)
    assert seen["argv"][:2] == ["python", "/app/render_piece.py"] and _timeout_arg(seen["argv"]) == 280
    assert seen["timeout"] == 300


def test_fly_backend_passes_both_budgets_and_a_wait_longer_than_the_steps():
    s = Settings.from_env(renderer="fly-machine", render_timeout_s=300.0, check_timeout_s=60.0,
                          fly_render_cpu_kind="shared", fly_render_cpus=2, fly_render_memory_mb=4096)
    r = FlyMachineRenderer(s, store=None)
    cfg = r.machine_config("https://in", "https://out", "job1")["config"]
    assert cfg["env"]["TAC_RENDER_TIMEOUT"] == "300" and cfg["env"]["TAC_RENDER_PIECE_TIMEOUT"] == "280"
    assert cfg["guest"] == {"cpu_kind": "shared", "cpus": 2, "memory_mb": 4096}
    assert r.wait_total_s == 60 + 300 + FLY_OVERHEAD_S > 60 + 300  # the VM wait gives up last


def _bootstrap(monkeypatch, env: dict):
    spec = importlib.util.spec_from_file_location("fly_bootstrap_under_test", BOOTSTRAP)
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    calls = []
    monkeypatch.setattr(b, "fetch_input", lambda url: None)
    monkeypatch.setattr(b, "network_is_dropped", lambda: (True, {"ok": True}))
    monkeypatch.setattr(b, "upload", lambda url, data: None)
    monkeypatch.setattr(b, "pack_output", lambda result: b"")
    monkeypatch.setattr(b.os, "chown", lambda *a: None)
    monkeypatch.setattr(b, "OUT", Path(env.pop("_out")))

    def fake_run_isolated(argv, timeout):
        calls.append((argv, timeout))
        return {"returncode": 0}

    monkeypatch.setattr(b, "run_isolated", fake_run_isolated)
    for k in ("TAC_RENDER_TIMEOUT", "TAC_RENDER_PIECE_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {"TAC_IN_URL": "https://in", "TAC_OUT_URL": "https://out", **env}.items():
        monkeypatch.setenv(k, v)
    assert b.main() == 0
    return calls


@pytest.mark.parametrize("env,inner,outer", [
    ({"TAC_RENDER_TIMEOUT": "300", "TAC_RENDER_PIECE_TIMEOUT": "280"}, 280, 300),
    ({"TAC_RENDER_TIMEOUT": "300"}, 280, 300),  # an older API without the inner value: derived the same way
    ({}, 280, 300),  # neither: the image's own default matches the platform default
])
def test_fly_bootstrap_runs_render_piece_with_the_inner_timeout(tmp_path, monkeypatch, env, inner, outer):
    calls = _bootstrap(monkeypatch, {**env, "_out": str(tmp_path / "out")})
    (check_argv, _), (render_argv, render_timeout) = calls
    assert check_argv[1] == "/app/check_piece.py"
    assert render_argv[1] == "/app/render_piece.py" and _timeout_arg(render_argv) == inner
    assert render_timeout == outer


def test_render_piece_reports_its_own_timeout(tmp_path):
    """The contract the margin relies on: render_piece.py exits 124 with a clear message at --timeout."""
    import subprocess

    lib = Path(__file__).resolve().parents[2] / "plugins" / "tac-studio" / "lib"
    piece = tmp_path / "hang"
    piece.mkdir()
    (piece / "piece.py").write_text("import time\nwhile True:\n    time.sleep(1)\n")
    out = subprocess.run([sys.executable, str(lib / "render_piece.py"), str(piece), "--out", str(tmp_path / "o"),
                          "--timeout", "1.5"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 124 and "render exceeded 2s wall clock" in out.stderr
