"""tacctl: the TAC community client behind /tac:login, /tac:submit, /tac:play.

    tacctl login --start | --wait      device-code login to the TAC platform (not Anthropic)
    tacctl logout | whoami
    tacctl prepare <name> --model M    tac-work/<name>/ → tac-work/<name>/submission/ + local check
    tacctl submit <name> [--model M]   prepare, check, upload, poll status
    tacctl status <id>
    tacctl mine                        your pieces: status, views, 7d, 28-day sparkline (private)
    tacctl fit [--sketch]              does a run fit the spare weekly window? (exit 3 = no)
    tacctl start <name> [--sketch]     create the work dir, record size sketch|full
    tacctl style [--print] [--log N]   open/create ~/.config/tac/style.md; --print / --log for the skill
    tacctl direct <name> seed|pick|note "<text>" [--iter K]   log the human's steering in notes.md
    tacctl play <name>                 print the live `tac play` command, build + open the review page
    tacctl gallery                     community pieces as validated handle/slug lines (no free text)

Env: TAC_API (default http://127.0.0.1:8790), TAC_WORK (default ./tac-work), TAC_SITE_URL (optional:
the gallery site's origin, for trusting piece links; default = the API host's last two labels).
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

LIB = Path(__file__).resolve().parent
PLUGIN = LIB.parent
sys.path.insert(0, str(LIB))
import check_piece  # noqa: E402
import meta as metamod  # noqa: E402
import notes as notesmod  # noqa: E402

DEFAULT_API = "http://127.0.0.1:8790"
PROCESS_W = 540
# Same patterns as platform/src/tac_platform/models.py (HANDLE_RE, SLUG_RE); tests pin them equal.
HANDLE_RE = re.compile(r"^[a-z0-9-]{2,24}$")
SLUG_RE = re.compile(r"^(?=.{1,48}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
TERMINAL = {"rejected", "in_review", "published"}


# C0 (incl. ESC), DEL and C1: a server string printed raw could move the cursor, rewrite earlier
# lines or set the window title. Tabs/newlines become spaces so one field can't fake another line.
# Also zero-width (U+200B-U+200D) and direction marks/overrides/isolates (U+200E-U+200F, U+202A-U+202E,
# U+2066-U+2069): they reorder or hide text, so what the user reads isn't what Claude or a terminal gets.
_INVISIBLE = "\u200b-\u200f\u202a-\u202e\u2066-\u2069"
_CTRL = re.compile(f"[\\x00-\\x1f\\x7f-\\x9f{_INVISIBLE}]")


def safe(value: Any) -> str:
    """Printable form of a string that came from the server (or a file it wrote)."""
    return _CTRL.sub(lambda m: " " if m.group() in "\t\n\r" else "", str(value))


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback  # 127.0.0.0/8, ::1
    except ValueError:
        return False


def checked_base(url: str) -> str:
    """An API base: https anywhere, plain http only on loopback (the token rides on it)."""
    try:
        u = urlsplit(url)
        host = u.hostname
    except ValueError:
        host = None
    if host and "@" not in u.netloc and (u.scheme == "https" or (u.scheme == "http" and is_loopback(host))):
        return url.rstrip("/")
    raise ApiError(0, f"refusing API base {safe(url)!r}: use https (plain http only for localhost)")


def api_base() -> str:
    return checked_base(os.environ.get("TAC_API", DEFAULT_API))


def session_base(creds: dict[str, Any]) -> str:
    """TAC_API wins; else the platform you logged in to."""
    return api_base() if os.environ.get("TAC_API") else checked_base(str(creds.get("api") or DEFAULT_API))


def work_root() -> Path:
    return Path(os.environ.get("TAC_WORK", "tac-work"))


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tac"


def cred_path() -> Path:
    return config_dir() / "credentials.json"


def die(msg: str, code: int = 1) -> int:
    print(f"error: {safe(msg)}", file=sys.stderr)
    return code


# ── private files (mode 600, atomic) ───────────────────────────────────────


def write_private(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)  # atomic: concurrent sessions never see a half-written file


def load_creds() -> dict[str, Any] | None:
    try:
        return json.loads(cred_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None


# ── HTTP ───────────────────────────────────────────────────────────────────


class ApiError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status, self.body = status, body


def http(method: str, url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
         timeout: float = 30) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    except urllib.error.URLError as e:
        raise ApiError(0, f"cannot reach {url}: {e.reason}") from None
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = raw
    return status, body


def post_json(url: str, payload: dict[str, Any], token: str | None = None) -> tuple[int, Any]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return http("POST", url, data=json.dumps(payload).encode(), headers=h)


def multipart(fields: list[tuple[str, str]], files: list[tuple[str, Path, str]]) -> tuple[bytes, str]:
    boundary = f"tac-{uuid.uuid4().hex}"
    out = bytearray()
    for name, value in fields:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n").encode()
        out += value.encode("utf-8") + b"\r\n"
    for name, path, ctype in files:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


# ── login ──────────────────────────────────────────────────────────────────


def pending_path() -> Path:
    return config_dir() / "login-pending.json"


_URL_JUNK = re.compile(f"[\\\\\\s\\x00-\\x1f\\x7f-\\x9f{_INVISIBLE}]")  # backslash, whitespace, C0/DEL/C1, bidi
_DEFAULT_PORT = {"https": 443, "http": 80}


def _origin(u: Any) -> tuple[str, str, int]:
    """(scheme, host, port) with the default port filled in; ValueError on a bad port."""
    return u.scheme, (u.hostname or "").lower(), u.port or _DEFAULT_PORT.get(u.scheme, -1)


def browser_target(url: str) -> str | None:
    """The URL to hand to `open`/`xdg-open` for a server-sent link, or None to refuse.

    Never the server's string itself: it is parsed, refused on a backslash, whitespace, control
    characters or userinfo (`https://evil.com\\@tac.example/` opens evil.com in browsers), and must
    match the API's scheme, host and port (default ports normalised). What gets opened is then
    rebuilt as the API origin + the parsed path + ?query, percent-encoded, so it is always ours.
    A file://, smb:// or custom-scheme URL never matches the API's origin.
    """
    if not isinstance(url, str) or _URL_JUNK.search(url):
        return None
    try:
        u, api = urlsplit(url), urlsplit(api_base())
        if u.username is not None or u.password is not None or "@" in u.netloc:
            return None
        if not u.hostname or _origin(u) != _origin(api):
            return None
    except (ValueError, ApiError):
        return None
    if u.path and not u.path.startswith("/"):
        return None
    path = quote(u.path or "/", safe="/%-._~!$&'()*+,;=:@")
    query = quote(u.query, safe="/%-._~!$&'()*+,;=:@?")
    return f"{api.scheme}://{api.netloc}{path}" + (f"?{query}" if query else "")


def _launch(target: str) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open" if shutil.which("xdg-open") else None
    if opener:
        subprocess.run([opener, target], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(target)


def open_browser(url: str) -> bool:
    """Open a server-supplied URL in the browser; refuse (and just print it) unless browser_target
    accepts it. The opened URL is the rebuilt one, never the server's string."""
    target = browser_target(url)
    if target is None:
        print(f"not opening {safe(url)!r}: only https pages on the TAC API host are opened")
        return False
    _launch(target)
    return True


def open_local(path: Path) -> None:
    """Open a file this tool wrote itself (review page, style file)."""
    _launch(str(path.resolve()))


def cmd_login(a: argparse.Namespace) -> int:
    base = api_base()
    if not a.wait:  # --start (default): get a code, open the browser, return immediately
        status, body = post_json(f"{base}/v1/auth/device", {})
        if status != 200 or not isinstance(body, dict) or "device_code" not in body:
            return die(f"device login failed: HTTP {status} {body}")
        write_private(pending_path(), {"api": base, "device_code": body["device_code"],
                                       "interval": body.get("interval", 5),
                                       "expires_at": time.time() + float(body.get("expires_in", 600))})
        uri = str(body.get("verification_uri_complete") or body.get("verification_uri") or "")
        print(f"code: {safe(body.get('user_code'))}")
        print(f"approve at: {safe(body.get('verification_uri'))}")
        if not a.no_browser and open_browser(uri):
            print("(opened in your browser)")
        print("then run: tacctl login --wait")
        return 0
    try:
        p = json.loads(pending_path().read_text())
    except (OSError, json.JSONDecodeError):
        return die("no login in progress — run `tacctl login --start` first")
    interval = max(1.0, float(p.get("interval", 5)))
    p["api"] = checked_base(str(p.get("api") or DEFAULT_API))
    while time.time() < p["expires_at"]:
        status, body = post_json(f"{p['api']}/v1/auth/token", {"device_code": p["device_code"]})
        if status == 200 and isinstance(body, dict) and body.get("access_token"):
            write_private(cred_path(), {"api": p["api"], "access_token": body["access_token"],
                                        "handle": body.get("handle"),
                                        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            pending_path().unlink(missing_ok=True)
            print(f"logged in as {safe(body.get('handle'))} → {cred_path()} (mode 600)")
            return 0
        if status == 428:
            time.sleep(interval)
            continue
        if status == 429:
            interval += 5
            time.sleep(interval)
            continue
        pending_path().unlink(missing_ok=True)
        return die(f"login failed: HTTP {status} {body}")
    pending_path().unlink(missing_ok=True)
    return die("login code expired — run `tacctl login --start` again")


def cmd_logout(a: argparse.Namespace) -> int:
    existed = cred_path().exists()
    cred_path().unlink(missing_ok=True)
    pending_path().unlink(missing_ok=True)
    print(f"removed {cred_path()}" if existed else "not logged in")
    return 0


def cmd_whoami(a: argparse.Namespace) -> int:
    c = load_creds()
    print(f"{safe(c.get('handle'))} @ {safe(c.get('api'))}" if c else "not logged in — run /tac:login")
    return 0 if c else 1


# ── prepare: tac-work/<name>/ → submission/ ────────────────────────────────


def piece_source(wd: Path, name: str) -> Path | None:
    for p in (wd / f"{name}.py", wd / "piece.py"):
        if p.is_file():
            return p
    iters = sorted(wd.glob("iter-*.py"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    return iters[-1] if iters else None


def count_iterations(wd: Path) -> int:
    return len({int(m.group(1)) for p in wd.glob("iter-*.py") if (m := re.match(r"iter-(\d+)$", p.stem))})


def loop_seconds(code: str) -> float | None:
    try:
        import asyncio

        import vscreen
        cap = asyncio.run(vscreen.capture(code, 80, 66, on_sample=lambda t, b: None))  # one pass, nothing kept
        return round(cap.loop_s, 3) if cap.loop_s else None
    except Exception as e:  # noqa: BLE001
        print(f"warn: could not measure loop length: {e}", file=sys.stderr)
        return None


def first_frame_png(src: Path, dst: Path, t: float = 0.0) -> bool:
    """Render one frame with the bundled renderer, downscaled for process/ (≤600 KB)."""
    try:
        import asyncio

        import vscreen
        code = src.read_text(encoding="utf-8")
        cap = asyncio.run(vscreen.capture(code, 80, 66, [t]))
        img = vscreen.Rasterizer().frame(vscreen.to_cells(cap.samples[0][1], 80, 66))
    except Exception as e:  # noqa: BLE001
        print(f"warn: could not render {src.name}: {e}", file=sys.stderr)
        return False
    return save_process_png(img, dst)


def save_process_png(img: Any, dst: Path) -> bool:
    from PIL import Image

    if img.width > PROCESS_W:
        img = img.resize((PROCESS_W, round(img.height * PROCESS_W / img.width)), Image.Resampling.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, optimize=True)
    if dst.stat().st_size > check_piece.MAX_PROCESS_BYTES:
        img.quantize(256).save(dst, optimize=True)
    return dst.stat().st_size <= check_piece.MAX_PROCESS_BYTES


def pick_process(wd: Path, src: Path, n_iter: int) -> list[tuple[str, Path]]:
    """iteration 1 → (one or two middle iterations) → final, as (label, script) pairs."""
    iters = {int(m.group(1)): p for p in wd.glob("iter-*.py") if (m := re.match(r"iter-(\d+)$", p.stem))}
    picks: list[tuple[str, Path]] = []
    if 1 in iters:
        picks.append(("iteration 1", iters[1]))
    mids = sorted(k for k in iters if 1 < k < n_iter)
    for k in ([mids[len(mids) // 3], mids[2 * len(mids) // 3]] if len(mids) >= 4 else mids[-1:]):
        if ("iteration %d" % k, iters[k]) not in picks:
            picks.append((f"iteration {k}", iters[k]))
    picks.append(("final", src))
    return picks[:4]


def tokens_from_transcripts(wd: Path) -> int | None:
    """Estimate: input + cache-write + output tokens (cache reads excluded) of every Claude Code
    transcript entry for this project since the work dir was started. An estimate, not billing."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
    proj = root / slug
    files = [p for p in wd.rglob("*") if p.is_file()]
    if not proj.is_dir() or not files:
        return None
    since = min(p.stat().st_mtime for p in files) - 600
    seen: dict[str, int] = {}
    for f in proj.rglob("*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        for line in f.open(encoding="utf-8", errors="replace"):
            if '"usage"' not in line:
                continue
            try:
                e = json.loads(line)
                msg = e.get("message") or {}
                u = msg.get("usage") or {}
                ts = dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
            except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
                continue
            if ts < since:
                continue
            n = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens"))
            key = msg.get("id") or e.get("uuid") or str(len(seen))
            seen[key] = max(seen.get(key, 0), n)  # streamed entries repeat a message id
    return sum(seen.values()) or None


def prepare(name: str, *, model: str | None, handle: str | None, tokens: int | None,
            estimate_tokens: bool) -> tuple[Path, dict[str, Any], list[str]]:
    wd = work_root() / name
    if not wd.is_dir():
        raise SystemExit(f"error: {wd}/ not found — make the piece with /tac:create first")
    src = piece_source(wd, name)
    if src is None:
        raise SystemExit(f"error: no {name}.py (or iter-*.py) in {wd}/")
    sub = wd / "submission"
    if sub.exists():
        shutil.rmtree(sub)
    (sub / "process").mkdir(parents=True)
    shutil.copyfile(src, sub / "piece.py")
    notes_text = (wd / "notes.md").read_text(encoding="utf-8") if (wd / "notes.md").exists() else ""
    if notes_text:
        shutil.copyfile(wd / "notes.md", sub / "notes.md")

    # meta: tac-work/<name>/meta.yaml is the user's copy; fill only what is missing.
    user_meta_path = wd / "meta.yaml"
    m: dict[str, Any] = metamod.loads_yaml(user_meta_path.read_text()) if user_meta_path.exists() else {}
    creds = load_creds() or {}
    n_iter = count_iterations(wd)
    m.setdefault("title", name)
    m.setdefault("description", notesmod.catalog_description(notes_text)[:400])
    if handle or creds.get("handle"):
        m.setdefault("handle", handle or creds.get("handle"))
    if model:
        m["model"] = model
    if tokens is not None:
        m["tokens"], m["tokens_source"] = tokens, "user"
    elif "tokens" not in m:
        est = tokens_from_transcripts(wd) if estimate_tokens else None
        m["tokens"] = est
        m["tokens_source"] = "transcript-estimate" if est else "unknown"
    m.setdefault("iterations", n_iter or None)
    m.setdefault("size", "full")
    m.setdefault("loop_s", loop_seconds(src.read_text(encoding="utf-8")))
    m.setdefault("license", metamod.DEFAULT_LICENSE)
    m.setdefault("created", dt.date.today().isoformat())
    m["human_role"] = notesmod.human_role(notes_text)  # computed from the direction log, never declared
    user_meta_path.write_text(metamod.dumps_yaml(m), encoding="utf-8")

    labels, captions = [], []
    for i, (label, script) in enumerate(pick_process(wd, src, n_iter), 1):
        if first_frame_png(script, sub / "process" / f"{i:02d}-{label.replace(' ', '-')}.png"):
            k = re.search(r"\d+", label)
            k_iter = int(k.group()) if k else notesmod.last_iter(notes_text)
            prefer = ("works", "biggest problem") if label == "final" else ("biggest problem", "works")
            captions.append(notesmod.critique(notes_text, k_iter, prefer))
            labels.append(label)
    if any(captions):
        m.setdefault("process_notes", captions)
    (sub / "meta.yaml").write_text(metamod.dumps_yaml(m), encoding="utf-8")
    reasons = check_piece.check_dir(sub)
    return sub, m, reasons


def cmd_prepare(a: argparse.Namespace) -> int:
    sub, m, reasons = prepare(a.name, model=a.model, handle=a.handle, tokens=a.tokens,
                              estimate_tokens=a.estimate_tokens)
    print(json.dumps({"dir": str(sub), "ok": not reasons, "reasons": reasons, "meta": m}, indent=2,
                     ensure_ascii=False))
    return 0 if not reasons else 1


# ── fit + start: does a run fit the spare weekly window? ───────────────────


def cmd_fit(a: argparse.Namespace) -> int:
    """exit 0 = fits (or unknown: no fresh usage cache), 3 = won't fit."""
    sys.path.insert(0, str(PLUGIN / "scripts"))
    import nudge

    w = nudge.window(nudge.load_cache(), time.time())
    pct = nudge.piece_pct()
    need = pct * (nudge.SKETCH_SHARE if a.sketch else 1.0)
    kind = "sketch" if a.sketch else "full piece"
    if w is None:
        print(f"fit: unknown (no fresh usage cache; the statusline helper is opt-in) — go ahead with the {kind}")
        return 0
    used, left = w
    spare = max(0.0, 100.0 - used)
    full, sketch = nudge.fit(used, pct)
    head = f"weekly window ~{used:.0f}% used · resets in {left / 3600:.0f}h · a {kind} needs ~{need:.0f}%"
    if spare >= need:
        print(f"fit: yes — {head} (~{spare:.0f}% left)")
        return 0
    alt = " · a sketch fits: /tac:create --sketch" if not a.sketch and sketch else ""
    print(f"fit: NO — {head}, ~{spare:.0f}% left{alt}")
    return 3


def cmd_start(a: argparse.Namespace) -> int:
    wd = work_root() / a.name
    wd.mkdir(parents=True, exist_ok=True)
    path = wd / "meta.yaml"
    m = metamod.loads_yaml(path.read_text()) if path.exists() else {}
    m["size"] = "sketch" if a.sketch else "full"
    path.write_text(metamod.dumps_yaml(m), encoding="utf-8")
    print(f"{wd}/ · size {m['size']}" + (" · max 3 iterations" if a.sketch else ""))
    return 0


# ── direction: the human's optional steering, logged in notes.md ───────────


def append_direction(name: str, line: str) -> str:
    """Append one line to tac-work/<name>/notes.md under `## direction`; returns the new text."""
    wd = work_root() / name
    wd.mkdir(parents=True, exist_ok=True)
    path = wd / "notes.md"
    text = path.read_text(encoding="utf-8") if path.exists() else f"# {name} — notes\n"
    sec = notesmod.direction_section(text)
    if not sec and not re.search(r"^##\s+direction\s*$", text, re.M | re.I):
        text = text.rstrip("\n") + "\n\n## direction\n\n" + line + "\n"
    else:
        m = re.search(r"^##\s+direction\s*$", text, re.M | re.I)
        end = m.end() + len(sec)
        text = text[:end].rstrip("\n") + "\n" + line + "\n" + ("\n" + text[end:].lstrip("\n") if text[end:].strip() else "")
    path.write_text(text, encoding="utf-8")
    return text


def cmd_direct(a: argparse.Namespace) -> int:
    value = " ".join(a.text.split())
    if not value:
        return die("empty direction")
    line = {"seed": f"- seed: {value}", "pick": f"- pick: {value}",
            "note": f"- note (iter-{a.iter}): {value}" if a.iter else f"- note: {value}"}[a.kind]
    text = append_direction(a.name, line)
    print(f"{line}  → human_role {notesmod.human_role(text)}")
    return 0


# ── style: the person's standing taste (~/.config/tac/style.md) ────────────

STYLE_TEMPLATE = """<!--
Your standing taste for /tac:create. Claude reads this at the start of every run.
A per-run idea or note wins when they conflict. TAC's DNA rules (no franchise IP,
real-world scale, near-black ground, seamless loop...) always win.
Write plainly; delete these comments. The first line is quoted in each piece's notes.
-->
I like: 
Palette: 
Subjects I keep coming back to: 
Avoid: 
"""


def style_path() -> Path:
    return config_dir() / "style.md"


def style_text() -> str:
    """The style file without HTML comments, or '' when absent/empty."""
    try:
        raw = style_path().read_text(encoding="utf-8")
    except OSError:
        return ""
    return re.sub(r"<!--.*?-->", "", raw, flags=re.S).strip()


def style_first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:120]


def cmd_style(a: argparse.Namespace) -> int:
    if a.print or a.log:  # skill entry points; silent no-ops when the file is absent or empty
        text = style_text()
        if text and a.log:
            append_direction(a.log, f"- style file used ({style_path()}): {style_first_line(text)}")
        if text and a.print:
            print(text)
        return 0
    path = style_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(STYLE_TEMPLATE, encoding="utf-8")
        print(f"created {path}")
    else:
        print(path)
    if not a.no_open:
        if sys.platform == "darwin":
            subprocess.run(["open", "-t", str(path)], check=False)
        else:
            open_local(path)
    return 0


# ── submit ─────────────────────────────────────────────────────────────────


def print_status(s: dict[str, Any]) -> None:
    print(f"status: {safe(s.get('status'))}")
    for r in s.get("reasons") or []:
        print(f"  reason: {safe(r)}")
    if s.get("critique"):
        print(f"critique: {safe(s['critique'])}")
    for k in ("preview_url", "url"):
        if s.get(k):
            print(f"{k.replace('_', ' ')}: {safe(s[k])}")


def poll(base: str, sid: str, token: str, wait_s: float) -> dict[str, Any]:
    deadline, last = time.time() + wait_s, None
    while True:
        status, body = http("GET", f"{base}/v1/submissions/{sid}",
                            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if status != 200 or not isinstance(body, dict):
            raise ApiError(status, str(body))
        if body.get("status") != last:
            print(f"  … {safe(body.get('status'))}", flush=True)
            last = body.get("status")
        if body.get("status") in TERMINAL or time.time() >= deadline:
            return body
        time.sleep(3)


def cmd_submit(a: argparse.Namespace) -> int:
    sub, m, reasons = prepare(a.name, model=a.model, handle=a.handle, tokens=a.tokens,
                              estimate_tokens=a.estimate_tokens)
    if reasons:
        print(f"local check rejected {sub} — nothing was uploaded:")
        for r in reasons:
            print(f"  - {r}")
        return 1
    print(f"local check ok: {sub}")
    if a.dry_run:
        print(metamod.dumps_yaml(m), end="")
        return 0
    creds = load_creds()
    if not creds:
        return die("not logged in — run /tac:login first")
    base = session_base(creds)
    send = {k: v for k, v in m.items() if k != "handle"}  # the platform takes the handle from the token
    files = [("piece", sub / "piece.py", "text/x-python")]
    if (sub / "notes.md").exists():
        files.append(("notes", sub / "notes.md", "text/markdown"))
    files += [("process", p, "image/png") for p in sorted((sub / "process").glob("*.png"))]
    body, ctype = multipart([("meta", json.dumps(send, ensure_ascii=False))], files)
    status, resp = http("POST", f"{base}/v1/submissions", data=body, timeout=120,
                        headers={"Content-Type": ctype, "Authorization": f"Bearer {creds['access_token']}",
                                 "Accept": "application/json"})
    if status == 401:
        return die("the platform rejected your login (401) — run /tac:login again")
    if status not in (200, 201, 202) or not isinstance(resp, dict) or "id" not in resp:
        return die(f"upload failed: HTTP {status} {resp}")
    print(f"uploaded: submission {safe(resp['id'])} ({safe(resp.get('status'))})")
    record = {"id": resp["id"], "api": base, "url": resp.get("url"), "piece_url": resp.get("piece_url"),
              "submitted": time.time()}
    (work_root() / a.name / ".submission.json").write_text(json.dumps(record, indent=2))
    share = share_url(resp, base)
    if a.no_wait:
        print_status(resp)
        print(submitted_line(share))
        return 0
    try:
        final = poll(base, resp["id"], creds["access_token"], a.wait)
    except ApiError as e:
        return die(f"status poll failed: {e}")
    final.setdefault("url", resp.get("url"))
    print_status(final)
    if final.get("status") == "rejected":
        return 1
    print(submitted_line(share))
    return 0


def site_hosts(base: str) -> set[str]:
    """Hosts a piece link may point at: the API host, plus the site host. That is TAC_SITE_URL's host
    when set, else the API host's last two labels (api.terminalart.club -> terminalart.club). No public
    suffix list: under a multi-label suffix such as co.uk, set TAC_SITE_URL."""
    api_host = (urlsplit(base).hostname or "").lower()
    hosts = {api_host}
    site = os.environ.get("TAC_SITE_URL")
    if site:
        try:
            hosts.add((urlsplit(site).hostname or "").lower())
        except ValueError:
            pass
    elif not is_loopback(api_host) and api_host.count(".") >= 1:
        try:
            ipaddress.ip_address(api_host)
        except ValueError:
            hosts.add(".".join(api_host.split(".")[-2:]))
    hosts.discard("")
    return hosts


def share_url(resp: dict[str, Any], base: str) -> Any:
    """piece_url only if it is https, clean (no whitespace, control chars, backslash or userinfo) and on
    the site or API host; it is printed into Claude's context. Otherwise the status URL."""
    pu = resp.get("piece_url")
    if isinstance(pu, str) and not _URL_JUNK.search(pu):
        try:
            u = urlsplit(pu)
            if u.scheme == "https" and "@" not in u.netloc and (u.hostname or "").lower() in site_hosts(base) \
                    and u.port in (None, 443):
                return pu
        except ValueError:
            pass
    return resp.get("url")


def submitted_line(url: Any) -> str:
    return f"Submitted. Once it passes review it's on the wall: {safe(url)}. Share the link. /tac:mine shows who's watching."


def cmd_status(a: argparse.Namespace) -> int:
    creds = load_creds()
    if not creds:
        return die("not logged in — run /tac:login")
    base = session_base(creds)
    status, body = http("GET", f"{base}/v1/submissions/{a.id}",
                        headers={"Authorization": f"Bearer {creds['access_token']}"})
    if status != 200 or not isinstance(body, dict):
        return die(f"HTTP {status} {body}")
    print_status(body)
    return 0


# ── mine: your own pieces and their (private) view counts ──────────────────

SPARK = "▁▂▃▄▅▆▇█"


def sparkline(series: list[Any]) -> str:
    vals = [int((v.get("views") if isinstance(v, dict) else v) or 0) for v in series]
    if not vals:
        return ""
    top = max(vals)
    if top == 0:
        return SPARK[0] * len(vals)
    return "".join(SPARK[min(len(SPARK) - 1, round(v / top * (len(SPARK) - 1)))] for v in vals)


def cmd_mine(a: argparse.Namespace) -> int:
    creds = load_creds()
    if not creds:
        print("not logged in — run /tac:login (or `tacctl login --start`) first")
        return 1
    base = session_base(creds)
    status, body = http("GET", f"{base}/v1/me/pieces",
                        headers={"Authorization": f"Bearer {creds['access_token']}", "Accept": "application/json"})
    if status == 401:
        print("your login expired or was revoked — run /tac:login again")
        return 1
    if status != 200:
        return die(f"HTTP {status} {body}")
    pieces = body.get("pieces", []) if isinstance(body, dict) else body if isinstance(body, list) else []
    print(f"@{safe(creds.get('handle'))} · your pieces")
    if not pieces:
        print("  none yet — /tac:create, then /tac:submit")
        return 0
    rows, extra = [], []
    for p in pieces:
        notes_ = []
        if p.get("critique"):
            notes_.append(("critique", str(p["critique"])))
        if p.get("status") == "rejected":
            notes_ += [("reason", str(r)) for r in (p.get("reasons") or [])]
        extra.append(notes_)
        series = next((p[k] for k in ("views_28d", "series_28d", "series") if isinstance(p.get(k), list)), [])
        rows.append((safe(p.get("title") or p.get("slug") or p.get("id")), safe(p.get("status") or "?"),
                     safe(p.get("views_total") if p.get("views_total") is not None else "–"),
                     safe(p.get("views_7d") if p.get("views_7d") is not None else "–"), sparkline(series)))
    w = [max(len(r[i]) for r in rows) for i in range(4)]
    # views lead each line: total · last 7 days, then piece, status, the 28-day sparkline
    fmt = f"  views: {{2:>{w[2]}}} · {{3:>{w[3]}}} last 7 days  {{0:<{w[0]}}}  {{1:<{w[1]}}}  {{4}}"
    width = max(40, shutil.get_terminal_size((100, 24)).columns)
    dim = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    for r, notes_ in zip(rows, extra):
        print(fmt.format(*r))
        for kind, text in notes_:
            first, rest = ("    ", "    ") if kind == "critique" else ("    ✗ ", "      ")
            line = textwrap.fill(" ".join(safe(text).split()), width=width, initial_indent=first, subsequent_indent=rest)
            print(f"\x1b[2m{line}\x1b[0m" if dim else line)
    print("manage or unpublish at terminalart.club/me")
    return 0


# ── gallery: what the community already made, as ids only ───────────────


def gallery_ids(doc: Any) -> tuple[list[str], int]:
    """(valid "handle/slug" ids in feed order, count dropped). Titles, descriptions and bios are
    community-written text; they never leave this function, so they never reach Claude's context."""
    ids: list[str] = []
    dropped = 0
    pieces = doc.get("pieces") if isinstance(doc, dict) else None
    for p in pieces if isinstance(pieces, list) else []:
        h, s = (p.get("handle"), p.get("slug")) if isinstance(p, dict) else (None, None)
        if isinstance(h, str) and isinstance(s, str) and HANDLE_RE.match(h) and SLUG_RE.match(s):
            if f"{h}/{s}" not in ids:
                ids.append(f"{h}/{s}")
        else:
            dropped += 1
    return ids, dropped


def cmd_gallery(a: argparse.Namespace) -> int:
    creds = load_creds()
    base = session_base(creds) if creds else api_base()
    status, body = http("GET", f"{base}/v1/community.json", headers={"Accept": "application/json"})
    if status != 200:
        return die(f"gallery unavailable: HTTP {status}")  # body not echoed: it is community-writable
    ids, dropped = gallery_ids(body)
    for i in ids:
        print(i)
    if dropped:
        print(f"({dropped} entries with an invalid handle/slug skipped)", file=sys.stderr)
    return 0


# ── play / review ──────────────────────────────────────────────────────────


def cmd_play(a: argparse.Namespace) -> int:
    import review

    wd = work_root() / a.name
    src = piece_source(wd, a.name) if wd.is_dir() else None
    if src is None:
        return die(f"no piece in {wd}/")
    tac = PLUGIN / "bin" / "tac"
    print("watch it live — paste into a terminal (Ctrl-C quits):")
    print(f"  {shlex.quote(str(tac))} play {shlex.quote(str(src.resolve()))}")
    if a.no_page:
        return 0
    page = review.build(work_root(), render=not a.no_render, only=a.name)
    print(f"review page: file://{page.resolve()}")
    if not a.no_browser:
        open_local(page)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tacctl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    lg = sp.add_parser("login")
    g = lg.add_mutually_exclusive_group()
    g.add_argument("--start", action="store_true", help="get a code and open the browser (default)")
    g.add_argument("--wait", action="store_true", help="poll until the code is approved")
    lg.add_argument("--no-browser", action="store_true")
    sp.add_parser("logout")
    sp.add_parser("whoami")
    for name in ("prepare", "submit"):
        p = sp.add_parser(name)
        p.add_argument("name")
        p.add_argument("--model", help="model id that made the piece, e.g. claude-opus-5-5")
        p.add_argument("--handle")
        p.add_argument("--tokens", type=int, help="your own token count for this piece")
        p.add_argument("--estimate-tokens", action="store_true",
                       help="estimate tokens from this project's Claude Code transcripts")
        if name == "submit":
            p.add_argument("--dry-run", action="store_true", help="prepare + check only")
            p.add_argument("--no-wait", action="store_true")
            p.add_argument("--wait", type=float, default=300, help="seconds to poll status (default 300)")
    ft = sp.add_parser("fit", help="does a run fit the spare weekly window? exit 3 = no")
    ft.add_argument("--sketch", action="store_true")
    sa = sp.add_parser("start", help="create tac-work/<name>/ and record the run size")
    sa.add_argument("name")
    sa.add_argument("--sketch", action="store_true")
    sp.add_parser("mine", help="your pieces on the platform: status + private view counts")
    sy = sp.add_parser("style", help="open/create ~/.config/tac/style.md (your standing taste)")
    sy.add_argument("--print", action="store_true", help="print the style (nothing if absent)")
    sy.add_argument("--log", metavar="NAME", help="log its use under ## direction in NAME's notes.md")
    sy.add_argument("--no-open", action="store_true")
    dr = sp.add_parser("direct", help="log the human's seed / concept pick / iteration note in notes.md")
    dr.add_argument("name")
    dr.add_argument("kind", choices=("seed", "pick", "note"))
    dr.add_argument("text")
    dr.add_argument("--iter", type=int)
    st = sp.add_parser("status")
    st.add_argument("id")
    sp.add_parser("gallery", help="community pieces as handle/slug lines, nothing else")
    pl = sp.add_parser("play")
    pl.add_argument("name")
    pl.add_argument("--no-page", action="store_true")
    pl.add_argument("--no-render", action="store_true", help="don't render missing previews")
    pl.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    try:
        return {"login": cmd_login, "logout": cmd_logout, "whoami": cmd_whoami, "prepare": cmd_prepare,
                "submit": cmd_submit, "status": cmd_status, "play": cmd_play, "direct": cmd_direct, "style": cmd_style, "mine": cmd_mine,
                "fit": cmd_fit, "start": cmd_start, "gallery": cmd_gallery}[a.cmd](a)
    except ApiError as e:
        return die(str(e))


if __name__ == "__main__":
    sys.exit(main())
