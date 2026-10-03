"""Startup gates: unknown env values, dev login off loopback, prod site origins."""

import pytest

from tac_platform.app import create_app
from tac_platform.config import Settings

PROD = dict(env="prod", auth_mode="github", renderer="docker", site_origins=("https://terminalart.club",))


@pytest.mark.parametrize("value", ["production", "staging", "", " ", "prd", "development"])
def test_unknown_env_refuses_to_start(value, tmp_path, monkeypatch):
    monkeypatch.setenv("TAC_ENV", value)
    with pytest.raises(RuntimeError, match="TAC_ENV="):
        Settings.from_env(data_dir=tmp_path)
    with pytest.raises(RuntimeError, match="TAC_ENV="):
        Settings.from_env(data_dir=tmp_path, env=value)


@pytest.mark.parametrize("value", ["Prod", " PROD ", "prod\n"])
def test_prod_spellings_get_prod_gates(value, tmp_path, monkeypatch):
    monkeypatch.setenv("TAC_ENV", value)
    s = Settings.from_env(data_dir=tmp_path, auth_mode="dev", renderer="docker")
    assert s.env == "prod"
    with pytest.raises(RuntimeError, match="TAC_AUTH=github"):  # dev web login never live under any prod spelling
        create_app(s)
    with pytest.raises(RuntimeError, match="TAC_RENDERER='local'"):  # renderer gate sees the normalized env too
        create_app(Settings.from_env(data_dir=tmp_path, auth_mode="github", renderer="local"))
    create_app(Settings.from_env(data_dir=tmp_path, **{**PROD, "env": value}))


def test_unknown_auth_mode_refuses_to_start(tmp_path):
    with pytest.raises(RuntimeError, match="TAC_AUTH="):
        Settings.from_env(data_dir=tmp_path, auth_mode="oauth")
    assert Settings.from_env(data_dir=tmp_path, auth_mode=" GitHub ").auth_mode == "github"


@pytest.mark.parametrize(("base", "site_url"), [
    ("https://api.terminalart.club", ""),
    ("http://0.0.0.0:8790", ""),
    ("http://192.168.1.5:8790", ""),
    ("http://localhost.evil.example:8790", ""),
    ("http://127.0.0.1:8790", "https://terminalart.club"),
])
def test_dev_auth_refuses_non_loopback(base, site_url, tmp_path):
    s = Settings.from_env(data_dir=tmp_path, env="dev", auth_mode="dev", public_base_url=base, site_url=site_url)
    with pytest.raises(RuntimeError, match="TAC_AUTH=dev"):
        create_app(s)
    create_app(Settings.from_env(data_dir=tmp_path, env="dev", auth_mode="github", public_base_url=base, site_url=site_url))


@pytest.mark.parametrize(("base", "site_url"), [
    ("http://127.0.0.1:8790", ""), ("http://localhost:8790", "http://localhost:5181"), ("http://[::1]:8790", ""),
])
def test_dev_auth_on_loopback_starts(base, site_url, tmp_path):
    create_app(Settings.from_env(data_dir=tmp_path, env="dev", auth_mode="dev", public_base_url=base, site_url=site_url))


def test_prod_site_origins_explicit_and_https(tmp_path, monkeypatch):
    monkeypatch.delenv("TAC_SITE_ORIGINS", raising=False)
    with pytest.raises(RuntimeError, match="requires TAC_SITE_ORIGINS"):
        create_app(Settings.from_env(data_dir=tmp_path, **{**PROD, "site_origins": ()}))
    for origins in ("http://localhost:5181,https://terminalart.club", "http://terminalart.club"):
        monkeypatch.setenv("TAC_SITE_ORIGINS", origins)
        with pytest.raises(RuntimeError, match="non-https TAC_SITE_ORIGINS"):
            create_app(Settings.from_env(data_dir=tmp_path, env="prod", auth_mode="github", renderer="docker"))
    monkeypatch.setenv("TAC_SITE_ORIGINS", "https://terminalart.club/")
    s = Settings.from_env(data_dir=tmp_path, env="prod", auth_mode="github", renderer="docker")
    assert s.site_origins == ("https://terminalart.club",)
    create_app(s)
    # dev default: the local site only
    monkeypatch.delenv("TAC_SITE_ORIGINS")
    assert Settings.from_env(data_dir=tmp_path, env="dev").site_origins == ("http://localhost:5181",)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "192.168.1.5", "10.0.0.7", "fd00::1", "", "example.com"])
def test_dev_auth_refuses_non_loopback_bind(host, tmp_path, monkeypatch):
    monkeypatch.setenv("TAC_HOST", host)  # e.g. a Fly deploy that set only TAC_HOST=0.0.0.0
    for env in ("TAC_ENV", "TAC_AUTH", "TAC_PUBLIC_BASE_URL", "TAC_SITE_URL"):
        monkeypatch.delenv(env, raising=False)
    s = Settings.from_env(data_dir=tmp_path)
    assert (s.env, s.auth_mode, s.host) == ("dev", "dev", host)
    with pytest.raises(RuntimeError, match="loopback TAC_HOST"):
        create_app(s)
    create_app(Settings.from_env(data_dir=tmp_path, auth_mode="github"))  # the bind host only gates dev login


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "127.0.0.2"])
def test_dev_auth_on_loopback_bind_starts(host, tmp_path, monkeypatch):
    monkeypatch.setenv("TAC_HOST", host)
    create_app(Settings.from_env(data_dir=tmp_path, auth_mode="dev"))
