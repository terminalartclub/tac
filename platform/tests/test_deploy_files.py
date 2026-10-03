"""platform/fly.toml + Dockerfile: the deployed [env] must pass the prod startup gate, and the image must
never ship local state."""

import tomllib
from pathlib import Path

import pytest

from tac_platform.app import create_app
from tac_platform.config import Settings
from tac_platform.fly_machine import FlyMachineRenderer

PLATFORM = Path(__file__).resolve().parents[1]
FLY = tomllib.loads((PLATFORM / "fly.toml").read_text())
ENV = FLY["env"]


def test_fly_oauth_credentials_are_secrets_and_required(tmp_path, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TAC_DATA_DIR", str(tmp_path))
    assert not {"TAC_GITHUB_CLIENT_ID", "TAC_GITHUB_CLIENT_SECRET"} & set(ENV)  # Fly secrets, never [env]
    monkeypatch.delenv("TAC_GITHUB_CLIENT_ID")
    monkeypatch.delenv("TAC_GITHUB_CLIENT_SECRET")
    with pytest.raises(RuntimeError, match=r"fly secrets set TAC_GITHUB_CLIENT_ID=… TAC_GITHUB_CLIENT_SECRET=… -a tac-api"):
        create_app(Settings.from_env())  # the next deploy without them fails with this instruction


def test_fly_env_passes_the_prod_gate(tmp_path, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TAC_DATA_DIR", str(tmp_path))  # /data is the volume; keep the test local
    s = Settings.from_env()
    assert (s.env, s.auth_mode, s.renderer) == ("prod", "github", "fly-machine")
    assert all(o.startswith("https://") for o in s.site_origins)
    assert "https://terminalart.club" in s.site_origins
    assert s.public_base_url.startswith("https://") and s.trust_proxy
    create_app(s)  # check_prod_safety passes


async def test_fly_env_render_backend_needs_only_the_token_secret(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FLY_API_TOKEN", raising=False)
    r = FlyMachineRenderer(Settings.from_env(data_dir=Path("/nonexistent")), store=None)
    assert "FLY_API_TOKEN" in (await r.available())  # prod refuses to start without the secret
    monkeypatch.setenv("FLY_API_TOKEN", "x")
    assert await r.available() is None
    assert ENV["TAC_FLY_RENDER_IMAGE"].startswith("registry.fly.io/tac-render:")


def test_fly_service_shape():
    svc = FLY["http_service"]
    assert svc["internal_port"] == int(ENV["TAC_PORT"])
    assert svc["auto_stop_machines"] == "off" and svc["min_machines_running"] == 1
    assert any(c["path"] == "/healthz" for c in svc["checks"])
    assert FLY["mounts"][0] == {"source": "tac_data", "destination": ENV["TAC_DATA_DIR"]}
    assert ENV["TAC_HOST"] == "0.0.0.0"


@pytest.mark.parametrize("name", ["data", ".venv", ".env", "tests"])
def test_image_never_ships_local_state(name):
    ignored = (PLATFORM / ".dockerignore").read_text().split()
    assert name in ignored
    docker = (PLATFORM / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in docker and 'CMD ["tac-platform"]' in docker
