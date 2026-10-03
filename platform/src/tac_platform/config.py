"""Settings, read once from the environment. Every knob is listed in README.md."""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PLATFORM_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = PLATFORM_DIR.parent
# IMPLEMENTED render backends that isolate untrusted code (no secrets, no network, own container,
# machine or namespace). Add a name here only together with its implementation (renderer.py).
ISOLATED_RENDERERS: frozenset[str] = frozenset({"docker", "fly-machine"})


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = PLATFORM_DIR / "data"
    db_path: Path | None = None  # default: data_dir / "tac.sqlite3"
    public_base_url: str = "http://127.0.0.1:8790"
    host: str = "127.0.0.1"
    port: int = 8790

    env: str = "dev"  # "dev" | "prod"
    renderer: str = "local"  # "local" (run_limited subprocess, NOT isolated) | "docker" (isolated)
    render_image: str = "tac-render:local"
    fly_render_app: str = "tac-render"
    fly_render_image: str = ""  # registry.fly.io/tac-render:<tag>
    fly_render_region: str = ""
    auth_mode: str = "dev"  # "dev" | "github"
    github_client_id: str = ""
    github_client_secret: str = ""
    admin_token: str = ""

    tools_dir: Path = REPO_DIR / "tools"
    tools_python: str = sys.executable
    render_timeout_s: float = 240.0
    check_timeout_s: float = 60.0
    render_concurrency: int = 1
    themes_file: Path = PLATFORM_DIR / "themes.json"

    automod_model: str = "claude-opus-5-5"
    automod_effort: str = "low"
    anthropic_api_key_present: bool = False

    site_origins: tuple[str, ...] = ("http://localhost:5181", "https://terminalart.club")
    site_url: str = ""  # web login redirects to site_url + return path; "" = relative (dev proxy)
    cookie_domain: str = ""  # prod: .terminalart.club
    trust_proxy: bool = False  # honour Fly-Client-IP / X-Forwarded-For
    worker_enabled: bool = True
    submissions_per_day: int = 3
    reports_per_hour: int = 5
    reports_to_hide: int = 3
    extra: dict = field(default_factory=dict)

    def check_prod_safety(self) -> None:
        """Refuse to run in prod while renders would execute untrusted code unisolated on the API host."""
        if self.env == "prod" and self.auth_mode != "github":
            raise RuntimeError("TAC_ENV=prod requires TAC_AUTH=github: dev login lets anyone claim any handle")
        if self.env == "prod" and self.renderer not in ISOLATED_RENDERERS:
            raise RuntimeError(
                f"TAC_ENV=prod refuses TAC_RENDERER={self.renderer!r}: the local subprocess renderer is not a "
                f"sandbox. Isolated backends implemented: {', '.join(sorted(ISOLATED_RENDERERS)) or 'none yet'}; see DEPLOY.md."
            )

    @property
    def sqlite_path(self) -> Path:
        return self.db_path or self.data_dir / "tac.sqlite3"

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        values = dict(
            data_dir=Path(_env("TAC_DATA_DIR", str(PLATFORM_DIR / "data"))),
            db_path=Path(os.environ["TAC_DB_PATH"]) if os.environ.get("TAC_DB_PATH") else None,
            public_base_url=_env("TAC_PUBLIC_BASE_URL", "http://127.0.0.1:8790").rstrip("/"),
            host=_env("TAC_HOST", "127.0.0.1"),
            port=int(_env("TAC_PORT", "8790")),
            env=_env("TAC_ENV", "dev"),
            renderer=_env("TAC_RENDERER", "local"),
            render_image=_env("TAC_RENDER_IMAGE", "tac-render:local"),
            fly_render_app=_env("TAC_FLY_RENDER_APP", "tac-render"),
            fly_render_image=_env("TAC_FLY_RENDER_IMAGE", ""),
            fly_render_region=_env("TAC_FLY_RENDER_REGION", ""),
            auth_mode=_env("TAC_AUTH", "dev"),
            github_client_id=_env("TAC_GITHUB_CLIENT_ID", ""),
            github_client_secret=_env("TAC_GITHUB_CLIENT_SECRET", ""),
            admin_token=_env("TAC_ADMIN_TOKEN", ""),
            tools_dir=Path(_env("TAC_TOOLS_DIR", str(REPO_DIR / "tools"))),
            tools_python=_env("TAC_TOOLS_PYTHON", sys.executable),
            render_timeout_s=float(_env("TAC_RENDER_TIMEOUT_S", "240")),
            render_concurrency=int(_env("TAC_RENDER_CONCURRENCY", "1")),
            themes_file=Path(_env("TAC_THEMES_FILE", str(PLATFORM_DIR / "themes.json"))),
            automod_model=_env("TAC_AUTOMOD_MODEL", "claude-opus-5-5"),
            automod_effort=_env("TAC_AUTOMOD_EFFORT", "low"),
            anthropic_api_key_present=bool(os.environ.get("ANTHROPIC_API_KEY")),
            site_origins=tuple(
                o.strip().rstrip("/")
                for o in _env("TAC_SITE_ORIGINS", "http://localhost:5181,https://terminalart.club").split(",")
                if o.strip()
            ),
            site_url=_env("TAC_SITE_URL", "").rstrip("/"),
            cookie_domain=_env("TAC_COOKIE_DOMAIN", ""),
            trust_proxy=_env("TAC_TRUST_PROXY", "0") == "1",
            worker_enabled=_env("TAC_WORKER", "1") == "1",
        )
        values.update(overrides)
        return cls(**values)
