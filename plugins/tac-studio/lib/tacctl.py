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
    tacctl gallery                     curated pieces (house artists, club picks) as handle/slug lines

Env: TAC_API (default https://api.terminalart.club; dev: http://127.0.0.1:8790), TAC_WORK (default: ./tac-work if it exists here, else ~/tac-work), TAC_SITE_URL (optional:
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

DEFAULT_API = "https://api.terminalart.club"  # the public platform; TAC_API overrides (dev: http://127.0.0.1:8790)
PROCESS_W = 540
# Same patterns as platform/src/tac_platform/models.py (HANDLE_RE, SLUG_RE); tests pin them equal.
HANDLE_RE = re.compile(r"^[a-z0-9-]{2,24}$")
SLUG_RE = re.compile(r"^(?=.{1,48}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
TERMINAL = {"rejected", "in_review", "published"}


# C0 (incl. ESC), DEL and C1: a server string printed raw could move the cursor, rewrite earlier
# lines or set the window title. Tabs/newlines become spaces so one field can't fake another line.
# Also the invisible/spoofing class, the same set as the platform's me._INVISIBLE: zero-width
# (U+200B-U+200D), direction marks/overrides/isolates (U+200E-U+200F, U+202A-U+202E, U+2066-U+2069),
# line/paragraph separators (U+2028-U+2029), word joiner and invisible operators (U+2060-U+2064), BOM
# (U+FEFF), soft hyphen, Arabic letter mark, Mongolian vowel separator and tag characters. They reorder,
# hide or break text, so what the user reads isn't what Claude or a terminal gets.
_INVISIBLE = ("\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff"
              "\U000e0000-\U000e007f")
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


NAME_RULE = ("a piece name is lowercase letters, digits and single hyphens, e.g. kettle or last-light "
             "(not tac-work or submission)")
RESERVED_NAMES = frozenset({"tac-work", "submission"})  # a root's own name; the folder prepare rebuilds
_BOTH_NOTED: set[str] = set()


class BadName(ValueError):
    pass


class Unsafe(BadName):
    """A piece folder, or a file tacctl writes in one, that resolves outside where it should (a symlink)."""


def valid_name(name: Any) -> bool:
    """fullmatch, never match: SLUG_RE's `$` would accept a trailing newline."""
    return isinstance(name, str) and SLUG_RE.fullmatch(name) is not None and name not in RESERVED_NAMES


def _within(p: Path, base: Path) -> bool:
    try:
        return p.resolve().is_relative_to(base.resolve())
    except (OSError, RuntimeError):  # a symlink loop
        return False


def inside(wd: Path, *parts: str) -> Path:
    """wd/parts, for a file or folder tacctl is about to write: refused (Unsafe) when it is a symlink or
    resolves outside wd. A cloned repo's tac-work/ can hold symlinks; a write must never follow one out."""
    p = wd.joinpath(*parts)
    if p.is_symlink() or not _within(p, wd):
        raise Unsafe(f"{p} is a symlink or points outside {wd}; refusing to write through it")
    return p


def home_root() -> Path:
    """Where new pieces go, always absolute: $TAC_WORK, else ~/tac-work (one home, whatever the cwd)."""
    env = os.environ.get("TAC_WORK")
    return Path(env).expanduser().absolute() if env else Path.home() / "tac-work"


def local_root() -> Path | None:
    """An existing ./tac-work in the current directory (pieces made before 0.1.1, or a project's own), also
    searched by name. None with $TAC_WORK (then only that), when absent, or when it IS ~/tac-work (cwd = ~)."""
    if os.environ.get("TAC_WORK"):
        return None
    local = Path("tac-work").absolute()
    if local.is_symlink() or not local.is_dir():  # a symlinked ./tac-work (e.g. in a cloned repo): not searched
        return None
    if _within(local, home_root()):  # it IS ~/tac-work, or a piece folder inside it (cwd under ~/tac-work)
        return None
    return local


def search_roots() -> list[Path]:
    """Lookup order for an existing piece: ./tac-work (if any), then ~/tac-work."""
    return [r for r in (local_root(), home_root()) if r is not None]


def work_root() -> Path:
    """The root new pieces are created in (and the review page lives in): home_root()."""
    return home_root()


def root_label(root: Path) -> str:
    if root == Path.home() / "tac-work":
        return "~/tac-work"
    if root == local_root():
        return "./tac-work"
    return str(root)


def piece_dir(name: Any) -> Path:
    """The folder for a piece name. The ONLY way a name becomes a path: the name must be a slug (no `..`, `/`,
    absolute, empty, unicode or newline), else BadName. Lookup: $TAC_WORK/<name> when set; else
    ./tac-work/<name> if that folder exists, else ~/tac-work/<name> (existing or not). So a piece that
    doesn't exist yet always lands in ~/tac-work: nothing is created in ./tac-work just because it exists."""
    if not valid_name(name):
        raise BadName(NAME_RULE)
    home = home_root() / name
    local = local_root()
    wd = home
    if local is not None and (local / name).is_dir():
        if home.is_dir() and name not in _BOTH_NOTED:
            _BOTH_NOTED.add(name)
            print(f"note: {name} is in both {local / name} and {home}; using the one in ./tac-work",
                  file=sys.stderr)
        wd = local / name
    if (wd.is_symlink() or wd.exists()) and (not _within(wd, wd.parent) or wd.resolve() == wd.parent.resolve()):
        raise Unsafe(f"{wd} points outside {wd.parent}; not using it (a symlinked piece folder)")
    return wd


def all_piece_dirs() -> list[Path]:
    """Every piece folder across search_roots(), one per name (the one piece_dir() would pick)."""
    seen: dict[str, Path] = {}
    for root in search_roots():
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir() and valid_name(d.name) and d.name not in seen:
                    seen[d.name] = d
    return list(seen.values())


def cmd_root(a: argparse.Namespace) -> int:
    if a.name is not None:
        print(piece_dir(a.name))
        return 0
    print(home_root())
    if (local := local_root()) is not None:
        print(f"also searched by name: {local} (./tac-work in this directory)", file=sys.stderr)
    return 0


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
    if target is None:  # the refused URL is not echoed: it is server text and would reach Claude's context
        print("not opening the browser: the server's link is not an https page on the TAC API host")
        return False
    _launch(target)
    return True


def open_local(path: Path) -> None:
    """Open a file this tool wrote itself (review page, style file)."""
    _launch(str(path.resolve()))


APPROVE_FALLBACK = "approve the code at the terminal art club site"


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
        approve = trusted_link(body.get("verification_uri"), base)
        print(f"approve at: {approve}" if approve else APPROVE_FALLBACK)
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


SESSIONS_FILE = ".sessions"  # tac-work/<name>/.sessions: one JSON line per Claude session that worked on it
SESSION_ID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")  # always fullmatch: "$" alone would accept a trailing newline
WORK_END_SLACK_S = 300  # the turn that wrote the last file finishes a little after the write
WORK_START_SLACK_S = 600  # the turns that planned the piece before its first file or `tacctl start`


def record_session(name: str) -> None:
    """Note the current Claude session (TAC_SESSION_ID, set by the plugin's SessionStart hook) as working on
    <name>. A new line whenever this session's latest sighting (across all pieces) is another piece, so
    A -> B -> back to A records A twice. Silently does nothing outside Claude Code or without the hook."""
    sid = os.environ.get("TAC_SESSION_ID", "")
    if not SESSION_ID_RE.fullmatch(sid):
        return
    path = inside(piece_dir(name), SESSIONS_FILE)
    latest = max(((r["at"], other.name) for other in all_piece_dirs()
                  for r in read_sessions(other) if r["session"] == sid), default=None)
    if latest is not None and latest[1] == name:
        return  # still on this piece in this session
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"session": sid, "at": time.time()}) + "\n")


def read_sessions(wd: Path) -> list[dict[str, Any]]:
    out = []
    try:
        lines = (wd / SESSIONS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and SESSION_ID_RE.fullmatch(str(r.get("session", ""))) \
                and isinstance(r.get("at"), (int, float)) and not isinstance(r.get("at"), bool):
            out.append(r)
    return out


def _work_times(wd: Path) -> list[float]:
    """mtimes of the piece's own files (not the submission build, meta or the session log)."""
    skip = {SESSIONS_FILE, "meta.yaml", ".submission.json"}
    return [p.stat().st_mtime for p in wd.rglob("*")
            if p.is_file() and p.name not in skip and "submission" not in p.relative_to(wd).parts]


def _work_end(wd: Path) -> float | None:
    """When the piece was last worked on: newest mtime of its own files, plus slack."""
    times = _work_times(wd)
    return max(times) + WORK_END_SLACK_S if times else None


def tokens_from_transcripts(wd: Path) -> int | None:
    """Estimate: input + cache-write + output tokens (cache reads excluded) of the Claude Code sessions that
    built this piece, as recorded in tac-work/<name>/.sessions. An estimate, not billing.

    Per recorded session, one window per sighting of this piece: from that sighting (the first one reaches
    back to 10 min before the piece's first file or start, whichever is earlier) until the session's next
    sighting of another piece, and never past the piece's last file edit + 5 min. So earlier unrelated work
    in the session, other pieces, and /tac:play, /tac:submit or /tac:mine turns after the work don't count;
    returning to the piece later (A -> B -> A) does. Other sessions are never read. No session -> None."""
    sessions = read_sessions(wd)
    times = _work_times(wd)
    if not sessions or not times:
        return None
    end_cap = max(times) + WORK_END_SLACK_S
    first_file = min(times)
    # every piece's sightings per session: a session's timeline of which piece it was working on
    timeline: dict[str, list[tuple[float, str]]] = {}
    dirs = {d.name: d for d in all_piece_dirs()}  # a session may have worked on pieces in either root
    for other in dirs.values():
        for r in read_sessions(other):
            timeline.setdefault(r["session"], []).append((r["at"], other.name))
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    seen: dict[str, int] = {}
    for sid in dict.fromkeys(r["session"] for r in sessions):
        ordered = sorted(timeline.get(sid, []))
        windows: list[tuple[float, float]] = []
        for k, (at, piece) in enumerate(ordered):
            if piece != wd.name:
                continue
            prev = max(((t, n) for t, n in ordered[:k] if n != wd.name), default=None)
            # the previous piece in this session owns time up to this sighting or its own last edit + slack
            prev_end = float("-inf") if prev is None else min(at, _work_end(dirs.get(prev[1], wd.parent / prev[1])) or prev[0])
            # the first sighting reaches back to just before the piece's first file (planning turns), but
            # never into the previous piece's window and never to the session's beginning
            lo = at if windows else max(min(at, first_file) - WORK_START_SLACK_S, prev_end)
            hi = min([t for t, n in ordered[k + 1:] if n != wd.name] + [end_cap])
            if lo < hi:
                windows.append((lo, hi))
        if not windows:
            continue
        files = [*root.glob(f"*/{sid}.jsonl"), *root.glob(f"*/{sid}/**/*.jsonl")]  # main + subagent transcripts
        for f in files:
            for line in f.open(encoding="utf-8", errors="replace"):
                if '"usage"' not in line:
                    continue
                try:
                    e = json.loads(line)
                    msg = e.get("message") or {}
                    u = msg.get("usage") or {}
                    ts = dt.datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
                except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError):
                    continue
                if not any(lo <= ts < hi for lo, hi in windows):
                    continue
                n = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "output_tokens"))
                key = msg.get("id") or e.get("uuid") or f"{f}:{len(seen)}"
                seen[key] = max(seen.get(key, 0), n)  # streamed entries repeat a message id
    return sum(seen.values()) or None


def prepare(name: str, *, model: str | None, handle: str | None, tokens: int | None,
            estimate_tokens: bool) -> tuple[Path, dict[str, Any], list[str]]:
    wd = piece_dir(name)
    if not wd.is_dir():
        raise SystemExit(f"error: {wd}/ not found — make the piece with /tac:create first")
    src = piece_source(wd, name)
    if src is None:
        raise SystemExit(f"error: no {name}.py (or iter-*.py) in {wd}/")
    sub = inside(wd, "submission")  # never rmtree through a symlink (shutil would raise; worse, it once didn't)
    if sub.exists():
        shutil.rmtree(sub)
    (sub / "process").mkdir(parents=True)
    shutil.copyfile(src, sub / "piece.py")
    notes_text = (wd / "notes.md").read_text(encoding="utf-8") if (wd / "notes.md").exists() else ""
    if notes_text:
        shutil.copyfile(wd / "notes.md", sub / "notes.md")

    # meta: tac-work/<name>/meta.yaml is the user's copy; fill only what is missing.
    user_meta_path = inside(wd, "meta.yaml")
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
        if est is not None and est > check_piece.MAX_TOKENS:  # never submit an estimate past the platform's cap
            print(f"warn: estimated {est:,} tokens is over the {check_piece.MAX_TOKENS:,} cap; recorded as unknown. "
                  "If you know the real count, pass --tokens N.", file=sys.stderr)
            est = None
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
    wd = piece_dir(a.name)
    wd.mkdir(parents=True, exist_ok=True)
    record_session(a.name)
    path = inside(wd, "meta.yaml")
    m = metamod.loads_yaml(path.read_text()) if path.exists() else {}
    m["size"] = "sketch" if a.sketch else "full"
    path.write_text(metamod.dumps_yaml(m), encoding="utf-8")
    print(f"{wd}/ · size {m['size']}" + (" · max 3 iterations" if a.sketch else ""))
    return 0


# ── direction: the human's optional steering, logged in notes.md ───────────


def append_direction(name: str, line: str) -> str:
    """Append one line to tac-work/<name>/notes.md under `## direction`; returns the new text."""
    wd = piece_dir(name)
    wd.mkdir(parents=True, exist_ok=True)
    path = inside(wd, "notes.md")
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
    record_session(a.name)  # a resumed or later session steering this piece also built it
    print(f"{line}  → human_role {notesmod.human_role(text)}")
    return 0


# ── style: the person's standing taste (~/.config/tac/style.md) ────────────

STYLE_TEMPLATE = """<!--
Your standing taste for /tac:create. Claude reads this at the start of every run and
works it into TAC's house style. To override the house style on a piece, insist in a
note. The hard rules (no franchise IP, real-world scale, near-black ground, seamless
loop, a renderable length) always hold.
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
    if a.log is not None:
        piece_dir(a.log)  # refuse a bad name even when there is no style file to log
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


def print_status(s: dict[str, Any], base: str) -> None:
    print(f"status: {safe(s.get('status'))}")
    for r in s.get("reasons") or []:
        print(f"  reason: {safe(r)}")
    if s.get("critique"):
        print(f"critique: {safe(s['critique'])}")
    for k in ("preview_url", "url"):
        if s.get(k):
            link = trusted_link(s[k], base)  # links are printed only when clean and on our hosts
            print(f"{k.replace('_', ' ')}: {link}" if link else f"{k.replace('_', ' ')}: (omitted: not a TAC link)")


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


RIGHTS_LINE = "You have the right to share this, and it doesn't copy anyone else's characters, brands or logos."
TERMS_LINE = "accept the updated terms: run /tac:login"


def cmd_submit(a: argparse.Namespace) -> int:
    if not a.confirm_rights and not a.dry_run:  # the user, not Claude, confirms this line in the conversation
        print(RIGHTS_LINE)
        print("nothing was uploaded: ask the user to confirm the line above, then re-run with --confirm-rights")
        return 1
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
    send["rights_confirmed"] = True  # only reachable with --confirm-rights; never written to meta.yaml
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
    if status == 403 and isinstance(resp, dict) and resp.get("error") == "terms_not_accepted":
        print(TERMS_LINE)
        return 1
    if status == 403 and isinstance(resp, dict) and resp.get("error") == "suspended":
        return die(str(resp.get("detail") or "this account is suspended"))  # die() prints through safe()
    if status not in (200, 201, 202) or not isinstance(resp, dict) or "id" not in resp:
        return die(f"upload failed: HTTP {status} {resp}")
    print(f"uploaded: submission {safe(resp['id'])} ({safe(resp.get('status'))})")
    record = {"id": resp["id"], "api": base, "url": resp.get("url"), "piece_url": resp.get("piece_url"),
              "submitted": time.time()}
    inside(piece_dir(a.name), ".submission.json").write_text(json.dumps(record, indent=2))
    wait_s = a.wait_seconds if a.wait_seconds is not None else (300.0 if a.wait else None)
    if wait_s is None:  # the default: return now; rendering (~1-2 min) and review happen on the platform
        print(uploaded_line(trusted_link(resp.get("piece_url"), base)))
        return 0
    share = share_url(resp, base)
    try:
        final = poll(base, resp["id"], creds["access_token"], wait_s)
    except ApiError as e:
        return die(f"status poll failed: {e}")
    final.setdefault("url", resp.get("url"))
    print_status(final, base)
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


def trusted_link(value: Any, base: str) -> str | None:
    """A server-sent link we may print (it lands in Claude's context), or None. Clean: a string with no
    whitespace, control/bidi chars, backslash or userinfo. And on our hosts: the API origin itself
    (same scheme/host/port, so plain http only where the API is loopback), or https on the site host."""
    if not isinstance(value, str) or not value or _URL_JUNK.search(value):
        return None
    try:
        u, api = urlsplit(value), urlsplit(base)
        if "@" in u.netloc or not u.hostname:
            return None
        if _origin(u) == _origin(api):
            return value
        if u.scheme == "https" and u.port in (None, 443) and u.hostname.lower() in site_hosts(base):
            return value
    except ValueError:
        pass
    return None


def share_url(resp: dict[str, Any], base: str) -> str | None:
    """The link for the success line: piece_url, else the status url, each only if trusted_link; else None."""
    return trusted_link(resp.get("piece_url"), base) or trusted_link(resp.get("url"), base)


SUBMITTED_NO_LINK = "submitted — see /tac:mine for its status"
UPLOADED = "Uploaded. Rendering on our servers (~1–2 min), then a person reviews it. /tac:mine shows its status"


def uploaded_line(piece_url: str | None) -> str:
    """The default submit's last line. piece_url only when trusted_link accepted it."""
    return UPLOADED + (f"; it'll be at {piece_url} once approved." if piece_url else ".")


def submitted_line(url: str | None) -> str:
    if not url:
        return SUBMITTED_NO_LINK
    return f"Submitted. Once it passes review it's on the wall: {url}. Share the link. /tac:mine shows who's watching."


def cmd_status(a: argparse.Namespace) -> int:
    creds = load_creds()
    if not creds:
        return die("not logged in — run /tac:login")
    base = session_base(creds)
    status, body = http("GET", f"{base}/v1/submissions/{a.id}",
                        headers={"Authorization": f"Bearer {creds['access_token']}"})
    if status != 200 or not isinstance(body, dict):
        return die(f"HTTP {status} {body}")
    print_status(body, base)
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


STATUS_LABELS = {"queued": "rendering", "rendering": "rendering", "in_review": "waiting for review",
                 "published": "published", "hidden": "hidden (reported)", "rejected": "rejected"}
PLATFORM_FAULT_NOTE = "not counted against your daily limit: our side failed, not your piece. Resubmit when you like."


def status_label(status: Any) -> str:
    return STATUS_LABELS.get(status, safe(status or "?")) if isinstance(status, str) or status is None else "?"


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
            if p.get("platform_fault") is True:
                notes_.append(("info", PLATFORM_FAULT_NOTE))
        if p.get("status") == "published":
            link = trusted_link(p.get("piece_url"), base)
            if link:
                notes_.append(("info", link))
        extra.append(notes_)
        series = next((p[k] for k in ("views_28d", "series_28d", "series") if isinstance(p.get(k), list)), [])
        rows.append((safe(p.get("title") or p.get("slug") or p.get("id")), status_label(p.get("status")),
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
            first, rest = ("    ✗ ", "      ") if kind == "reason" else ("    ", "    ")
            line = textwrap.fill(" ".join(safe(text).split()), width=width, initial_indent=first, subsequent_indent=rest)
            print(f"\x1b[2m{line}\x1b[0m" if dim else line)
    print("manage or unpublish at terminalart.club/me")
    return 0


# ── gallery: curated pieces only, as ids only ─────────────────────────────


def curated(p: Any) -> bool:
    """A house-artist piece or a club pick. Both flags are admin-set on the platform (users.house_artist via
    the admin endpoint, submissions.pick with no public writer), never taken from submitted meta. Strict
    `is True`: a truthy string or number from a malformed feed doesn't count."""
    return isinstance(p, dict) and (p.get("house_artist") is True or p.get("pick") is True)


def gallery_ids(doc: Any) -> tuple[list[str], int]:
    """(valid "handle/slug" ids of curated pieces in feed order, count of curated entries dropped as invalid).
    Community pieces are skipped entirely: even a validated slug is a stranger's words, and it would land
    in another user's Claude context. Titles, descriptions and bios never leave this function."""
    ids: list[str] = []
    dropped = 0
    pieces = doc.get("pieces") if isinstance(doc, dict) else None
    for p in pieces if isinstance(pieces, list) else []:
        if not curated(p):
            continue
        h, s = p.get("handle"), p.get("slug")
        if isinstance(h, str) and isinstance(s, str) and HANDLE_RE.fullmatch(h) and SLUG_RE.fullmatch(s):
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


def list_pieces() -> list[Path]:
    """Piece folders (with a piece file) across both roots, one per name, newest first."""
    found = [d for d in all_piece_dirs() if piece_source(d, d.name)]
    return sorted(found, key=lambda d: d.stat().st_mtime, reverse=True)


def cmd_play(a: argparse.Namespace) -> int:
    import review
    import termwin

    if a.name is None:  # omitted: list. An explicit "" is a bad name, refused below
        found = list_pieces()
        if not found:
            return die(f"no pieces in {root_label(home_root())}/ yet — make one with /tac:create")
        print("your pieces (newest first): " + ", ".join(f"{d.name} ({root_label(d.parent)})" for d in found))
        print("play one: tacctl play <name>")
        return 0
    wd = piece_dir(a.name)  # also keeps the name out of every path and AppleScript string
    root = wd.parent
    src = piece_source(wd, a.name) if wd.is_dir() else None
    if src is None:
        return die(f"no piece in {wd}/")
    src = src.resolve()
    if not src.is_relative_to(root.resolve()):  # a symlink out of the work root: not the person's piece
        return die(f"{wd}/ points outside {root}/; not playing it")
    argv = termwin.play_argv(PLUGIN / "bin" / "tac", src)
    where = "window" if a.window else "tab" if a.tab else "split"
    opened, why = (None, "--no-window") if a.no_window else termwin.open_play_window(argv, where=where)
    if opened and not opened.confirmed:
        print(f"opened a {opened.app} window, but couldn't confirm the piece started in it. If it isn't playing "
              "there, paste this into a terminal (Ctrl-C quits):")
        print(f"  {shlex.join(argv)}")
    elif opened and opened.where == "pane":
        print(f"playing {a.name} in a pane on the right ({opened.app}). Ctrl-C there stops it and closes the pane.")
        if opened.size and opened.size != (termwin.COLS, termwin.ROWS):
            c, r = opened.size
            print(f"the pane is {c}x{r}, not {termwin.COLS}x{termwin.ROWS}: the piece fills what's there. For the "
                  f"reel framing: tacctl play {a.name} --window")
    elif opened and opened.where == "tab":
        print(f"playing {a.name} in a new {opened.app} tab. Ctrl-C there stops it and closes the tab.")
    elif opened:
        if where != "window" and opened.note:
            print(f"couldn't open a {'pane' if where == 'split' else 'tab'} beside Claude Code ({safe(opened.note)}); "
                  "a window instead.")
        print(f"playing {a.name} in a new {opened.app} window. Ctrl-C there stops it.")
        if opened.size and opened.size != (termwin.COLS, termwin.ROWS):
            c, r = opened.size
            print(f"the window is {c}x{r}, not {termwin.COLS}x{termwin.ROWS} (the screen is too small at this font "
                  f"size): the piece fills what's there. For the reel framing, shrink the font and play again.")
    else:
        if not a.no_window:
            print(f"couldn't open a terminal window ({safe(why)}).")
        print("watch it live — paste into a terminal (Ctrl-C quits):")
        print(f"  {shlex.join(argv)}")
    if a.no_page:
        return 0
    page = review.build(home_root(), render=not a.no_render, only=a.name, dirs=list_pieces())
    print(f"review page (all your pieces): file://{page.resolve()}")
    if a.page:
        open_local(page)
    return 0


def seconds_arg(v: str) -> float:
    try:
        s = float(v)
    except ValueError:
        s = -1.0
    if not 0 <= s <= 3600:
        raise argparse.ArgumentTypeError("seconds per piece: 0 (one loop) to 3600")
    return s


def cmd_wall(a: argparse.Namespace) -> int:
    """/tac:wall: this week's wall (or the picks) in a pane beside Claude Code, else a window. Fetches only the
    playlist here; the pane fetches each piece's frames as it comes up."""
    import termwin
    import wall

    cache = wall.Cache()
    base = api_base()
    offline = False
    try:
        doc = wall.fetch_playlist(base, a.picks)
        cache.save_playlist(doc, base, a.picks)  # also drops cached pieces no longer on the wall
    except wall.WallError as e:
        doc = cache.load_playlist()
        cached = wall.playable_offline(doc, cache) if doc else []
        if not cached:
            return die(f"can't fetch the wall ({safe(e)}), and nothing is cached from the last 7 days. "
                       "Try again when online.")
        print(f"offline ({safe(e)}): playing the {len(cached)} cached piece{'s' * (len(cached) != 1)}.")
        offline = True
    if not doc["pieces"]:
        print("there are no picks yet." if a.picks else
              "the wall is empty: nothing published yet. Make something for it: /tac:create")
        return 0
    argv = [str(PLUGIN / "bin" / "tacctl"), "wall-play", "--seconds", f"{a.seconds:g}"] + (["--offline"] if offline else [])
    what = {"week+recent": "this week's wall, topped up with recent pieces",
            "picks": "the picks (nothing on this week's wall yet)",
            "recent": "the most recent pieces (nothing this week, no picks yet)"}.get(doc["source"], "this week's wall")
    n = len(doc["pieces"])
    where = "window" if a.window else "tab" if a.tab else "split"
    opened, why = (None, "--no-window") if a.no_window else termwin.open_play_window(argv, where=where)
    if opened and opened.confirmed:
        place = {"pane": f"a pane on the right ({opened.app})", "tab": f"a new {opened.app} tab"}.get(
            opened.where, f"a new {opened.app} window")
        print(f"playing {what}, {n} piece{'s' * (n != 1)}, {a.seconds:g} s each, in {place}. Ctrl-C there stops it.")
    else:
        if not a.no_window:
            print(f"couldn't open a terminal ({safe(why or 'unconfirmed')}).")
        print("watch it live — paste into a terminal (Ctrl-C quits):")
        print(f"  {shlex.join(argv)}")
    return 0


def cmd_wall_play(a: argparse.Namespace) -> int:
    """In the pane: plays the cached playlist (written by `tacctl wall`), fetching frames as it goes."""
    import wall

    cache = wall.Cache()
    doc = cache.load_playlist()
    if doc is None:
        return die("no wall playlist yet: run /tac:wall (tacctl wall) first")
    try:
        base = checked_base(doc["api"]) if doc.get("api") else None
    except ApiError:
        base = None
    try:
        return wall.play(doc, base, cache, a.seconds, a.offline or base is None, picks=doc.get("picks", False))
    except wall.WallError as e:
        return die(f"the wall stopped: {safe(e)}")


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
                       help="estimate tokens from the Claude Code sessions that built this piece")
        if name == "submit":
            p.add_argument("--dry-run", action="store_true", help="prepare + check only")
            p.add_argument("--confirm-rights", action="store_true",
                           help="the user confirmed: they have the right to share this, and it copies no one "
                                "else's characters, brands or logos")
            # --wait never takes a value from the next word (`--wait ember` must not eat the name); a
            # number goes in --wait-seconds N or --wait=N
            p.add_argument("--wait", action="store_true",
                           help="poll until rendered and reviewed; without it, submit returns right after the upload")
            p.add_argument("--wait-seconds", type=float, default=None, metavar="N",
                           help="with --wait: poll up to N seconds (default 300); implies --wait")
            p.add_argument("--no-wait", action="store_true", help=argparse.SUPPRESS)  # pre-0.1.1: now the default
    ft = sp.add_parser("fit", help="does a run fit the spare weekly window? exit 3 = no")
    ft.add_argument("--sketch", action="store_true")
    wl = sp.add_parser("wall", help="watch this week's wall (pre-rendered frames, nobody's code runs) in a pane")
    wl.add_argument("--picks", action="store_true", help="the picks instead of this week's wall")
    wl.add_argument("--seconds", type=seconds_arg, default=30.0, help="seconds per piece (0: one loop each); default 30")
    ww = wl.add_mutually_exclusive_group()
    ww.add_argument("--tab", action="store_true", help="a new tab instead of a pane on the right")
    ww.add_argument("--window", action="store_true", help="a new window")
    wl.add_argument("--no-window", action="store_true", help="just print the command to paste")
    wp = sp.add_parser("wall-play", help=argparse.SUPPRESS)
    wp.add_argument("--seconds", type=seconds_arg, default=30.0)
    wp.add_argument("--offline", action="store_true")
    rt = sp.add_parser("root", help="print ~/tac-work (where new pieces go), or with a name, that piece's folder")
    rt.add_argument("name", nargs="?")
    sa = sp.add_parser("start", help="create <work folder>/<name>/ and record the run size")
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
    sp.add_parser("gallery", help="curated pieces (house artists, club picks) as handle/slug lines, nothing else")
    pl = sp.add_parser("play", help="play a piece in a pane beside Claude Code (iTerm2, Ghostty), else a new "
                                    "terminal window; no name lists your pieces")
    pl.add_argument("name", nargs="?")
    pw = pl.add_mutually_exclusive_group()
    pw.add_argument("--tab", action="store_true", help="a new tab instead of a pane on the right")
    pw.add_argument("--window", action="store_true", help="a new window (the 80x66 reel framing)")
    pl.add_argument("--no-window", action="store_true", help="just print the command to paste")
    pl.add_argument("--page", action="store_true", help="also open the review page in the browser")
    pl.add_argument("--no-page", action="store_true", help="don't build the review page")
    pl.add_argument("--no-render", action="store_true", help="don't render missing previews")
    pl.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)  # pre-0.1.1: now the default
    argv = list(sys.argv[1:] if argv is None else argv)
    # --wait=N (0.1.1 spelling) → --wait --wait-seconds N; a bare --wait stays a flag
    argv = [x for arg in argv for x in (["--wait", "--wait-seconds", arg[7:]] if arg.startswith("--wait=") else [arg])]
    a = ap.parse_args(argv)
    try:
        return {"login": cmd_login, "logout": cmd_logout, "whoami": cmd_whoami, "prepare": cmd_prepare,
                "submit": cmd_submit, "status": cmd_status, "play": cmd_play, "direct": cmd_direct, "style": cmd_style, "mine": cmd_mine,
                "wall": cmd_wall, "wall-play": cmd_wall_play,
                "fit": cmd_fit, "start": cmd_start, "gallery": cmd_gallery, "root": cmd_root}[a.cmd](a)
    except ApiError as e:
        return die(str(e))
    except BadName as e:
        return die(str(e))


if __name__ == "__main__":
    sys.exit(main())
