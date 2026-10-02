"""Client flows against a fake TAC platform: device login, prepare, upload, status polling."""

import json
import shutil
import stat
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import notes
import tacctl
from conftest import FIXTURES


class Platform:
    def __init__(self) -> None:
        self.token_polls = 0
        self.status_polls = 0
        self.upload: dict = {}
        self.auth_headers: list[str] = []


@pytest.fixture
def platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    state = Platform()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def reply(self, code: int, body: dict | None = None) -> None:
            data = json.dumps(body or {}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            if self.path == "/v1/auth/device":
                return self.reply(200, {"device_code": "dc-1", "user_code": "WXYZ-1234",
                                        "verification_uri": f"{base}/device", "interval": 1, "expires_in": 60})
            if self.path == "/v1/auth/token":
                assert json.loads(raw) == {"device_code": "dc-1"}
                state.token_polls += 1
                if state.token_polls < 2:
                    return self.reply(428, {"error": "authorization_pending"})
                return self.reply(200, {"access_token": "tok-123", "handle": "alex"})
            if self.path == "/v1/submissions":
                state.auth_headers.append(self.headers.get("Authorization", ""))
                msg = BytesParser(policy=HTTP).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw)
                parts: dict = {}
                for part in msg.iter_parts():
                    parts.setdefault(part.get_param("name", header="content-disposition"), []).append(
                        (part.get_filename(), part.get_payload(decode=True)))
                state.upload = parts
                return self.reply(202, {"id": "sub-1", "status": "queued", "url": f"{base}/v1/submissions/sub-1"})
            self.reply(404)

        def do_GET(self):
            if self.path == "/v1/submissions/sub-1":
                state.status_polls += 1
                seq = ["queued", "rendering", "in_review"]
                st = seq[min(state.status_polls - 1, 2)]
                return self.reply(200, {"id": "sub-1", "status": st, "reasons": [],
                                        "preview_url": f"{base}/p/sub-1.webp" if st == "in_review" else None,
                                        "critique": "calm, readable at t=0" if st == "in_review" else None})
            self.reply(404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{srv.server_port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("TAC_API", base)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("TAC_WORK", str(tmp_path / "tac-work"))
    monkeypatch.setattr(tacctl.time, "sleep", lambda s: None)
    yield state
    srv.shutdown()


@pytest.fixture
def work(tmp_path: Path) -> Path:
    wd = tmp_path / "tac-work" / "ember"
    wd.mkdir(parents=True)
    src = (FIXTURES / "good" / "piece.py").read_text()
    (wd / "iter-1.py").write_text(src.replace("N, dt = 20", "N, dt = 10"))
    (wd / "iter-2.py").write_text(src)
    shutil.copyfile(FIXTURES / "good" / "piece.py", wd / "ember.py")
    (wd / "notes.md").write_text(
        "# ember — notes\n\n## iteration log\n\n### iter-1\n- stats: x\n- biggest problem: **too fast.** More.\n"
        "\n### iter-2 (final → ember.py)\n- works: the wave breathes at 0.5 Hz.\n\n"
        "## catalog description\nA slow amber wave at 4am.\n")
    return wd


def test_login_device_flow_writes_private_credentials(platform: Platform, capsys) -> None:
    assert tacctl.main(["login", "--start", "--no-browser"]) == 0
    assert "WXYZ-1234" in capsys.readouterr().out
    assert tacctl.main(["login", "--wait"]) == 0
    cred = tacctl.cred_path()
    assert json.loads(cred.read_text())["access_token"] == "tok-123"
    assert stat.S_IMODE(cred.stat().st_mode) == 0o600
    assert platform.token_polls == 2
    assert tacctl.main(["whoami"]) == 0
    assert tacctl.main(["logout"]) == 0 and not cred.exists()


def test_wait_without_start_fails(platform: Platform) -> None:
    assert tacctl.main(["login", "--wait"]) == 1


def test_submit_requires_login(platform: Platform, work: Path, capsys) -> None:
    assert tacctl.main(["submit", "ember", "--model", "claude-opus-5-5"]) == 1
    assert "not logged in" in capsys.readouterr().err
    assert platform.upload == {}


def test_submit_uploads_and_polls(platform: Platform, work: Path, capsys) -> None:
    tacctl.write_private(tacctl.cred_path(), {"api": "unused", "access_token": "tok-123", "handle": "alex"})
    assert tacctl.main(["submit", "ember", "--model", "claude-opus-5-5", "--tokens", "1234"]) == 0
    out = capsys.readouterr().out
    assert "status: in_review" in out and "critique: calm" in out and "/v1/submissions/sub-1" in out
    assert platform.auth_headers == ["Bearer tok-123"]
    up = platform.upload
    meta = json.loads(up["meta"][0][1])
    assert meta["model"] == "claude-opus-5-5" and meta["tokens"] == 1234 and meta["tokens_source"] == "user"
    assert meta["iterations"] == 2 and meta["loop_s"] == 2.0 and meta["human_role"] == "none"
    assert meta["description"] == "A slow amber wave at 4am."
    assert "handle" not in meta  # the platform takes it from the token
    assert up["piece"][0][0] == "piece.py" and b"# ember" in up["piece"][0][1]
    assert up["notes"][0][0] == "notes.md"
    pngs = up["process"]
    assert [f for f, _ in pngs] == ["01-iteration-1.png", "02-final.png"]
    assert all(b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) <= 600_000 for _, b in pngs)
    assert meta["process_notes"] == ["too fast.", "the wave breathes at 0.5 Hz."]
    assert json.loads((work / ".submission.json").read_text())["id"] == "sub-1"


def test_local_check_blocks_upload(platform: Platform, work: Path, capsys) -> None:
    tacctl.write_private(tacctl.cred_path(), {"access_token": "tok-123", "handle": "alex"})
    (work / "ember.py").write_text("import os\n" + (work / "ember.py").read_text())
    assert tacctl.main(["submit", "ember", "--model", "claude-opus-5-5"]) == 1
    assert "nothing was uploaded" in capsys.readouterr().out
    assert platform.upload == {}


def test_human_role_is_computed_not_declared(platform: Platform, work: Path) -> None:
    (work / "meta.yaml").write_text('human_role: "directed"\n')  # an attempt to inflate
    sub, m, reasons = tacctl.prepare("ember", model="claude-opus-5-5", handle=None, tokens=None,
                                     estimate_tokens=False)
    assert m["human_role"] == "none" and reasons == []
    assert tacctl.main(["direct", "ember", "seed", "embers at 4am"]) == 0
    _, m, _ = tacctl.prepare("ember", model="claude-opus-5-5", handle=None, tokens=None, estimate_tokens=False)
    assert m["human_role"] == "seeded"
    assert tacctl.main(["direct", "ember", "note", "warmer", "--iter", "2"]) == 0
    _, m, reasons = tacctl.prepare("ember", model="claude-opus-5-5", handle=None, tokens=None,
                                   estimate_tokens=False)
    assert m["human_role"] == "directed" and reasons == []
    text = (work / "notes.md").read_text()
    assert "## direction\n\n- seed: embers at 4am\n- note (iter-2): warmer\n" in text


def test_direction_section_parsing() -> None:
    text = ("# x\n\n## direction\n\n- seed: rain\n- pick: concept B — the window\n\n## concept\n"
            "- note: not in direction\n")
    assert notes.direction(text) == {"seed": ["rain"], "pick": ["concept B — the window"], "note": []}
    assert notes.human_role(text) == "directed"
    assert notes.human_role("# x\n## direction\n- seed: 42\n") == "seeded"
    assert notes.human_role("# x\n") == "none"


def test_pr_fallback_prints_commands(platform: Platform, work: Path, capsys) -> None:
    assert tacctl.main(["submit", "ember", "--model", "claude-opus-5-5", "--handle", "alex", "--pr"]) == 0
    out = capsys.readouterr().out
    assert "gh repo fork" in out and "pieces/alex/ember" in out and "gh pr create" in out
    assert platform.upload == {}


def test_fit_gate_and_size(platform: Platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import time as _t
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("TAC_PIECE_PCT", "30")
    assert tacctl.main(["fit"]) == 0  # no cache → unknown → don't block
    (tmp_path / "cache" / "tac").mkdir(parents=True)
    (tmp_path / "cache" / "tac" / "usage.json").write_text(json.dumps(
        {"seven_day": {"used_percentage": 85, "resets_at": _t.time() + 7200}, "updated_at": _t.time()}))
    assert tacctl.main(["fit"]) == 3  # 15% left < 30% for a full piece
    assert "a sketch fits" in capsys.readouterr().out
    assert tacctl.main(["fit", "--sketch"]) == 0  # 15% ≥ 12%
    assert tacctl.main(["start", "ember", "--sketch"]) == 0
    assert 'size: "sketch"' in (Path(tacctl.work_root()) / "ember" / "meta.yaml").read_text()


def test_sketch_over_three_iterations_rejected(good: Path) -> None:
    from check_piece import check_dir
    (good / "meta.yaml").write_text((good / "meta.yaml").read_text().replace("iterations: 1", "iterations: 5")
                                    + 'size: "sketch"\n')
    assert any("at most 3 iterations" in r for r in check_dir(good))
