"""Lint a TAC piece directory before it is rendered or published.

    python check_piece.py <piece_dir>     → stdout {"ok": bool, "reasons": [str]}; exit 0 ok, 1 rejected

THIS IS LINT, NOT A SANDBOX. A static AST allowlist catches honest mistakes and the
obvious escapes (file/network/process modules, exec/eval/open, dunder walks), but a
determined author can still find a path out of CPython. Anything that executes a piece
(render_piece.py, CI, the platform) must also run it in an isolated, secret-free,
time-limited environment.

Stdlib only, so it runs anywhere `python3` does.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meta import MetaError, load as load_meta  # noqa: E402
from notes import HUMAN_ROLES, human_role  # noqa: E402

MAX_LINES = 1500
MAX_PIECE_BYTES = 200_000
MAX_NOTES_BYTES = 200_000
MAX_PROCESS = 4
MAX_PROCESS_BYTES = 600_000
MAX_TOKENS = 2_000_000  # same ceiling as the platform (models.MAX_TOKENS)

# math/random/colorsys/functools/itertools/sys/rich.* per the community rules; bisect and
# cmath are pure-math stdlib (chlorine, wake need them); types only for ModuleType /
# SimpleNamespace (wake's sys.modules cache).
ALLOWED_MODULES = {"math", "random", "colorsys", "functools", "itertools", "sys", "bisect",
                   "cmath", "types"}
ALLOWED_RICH = {"rich", "rich.text", "rich.style", "rich.color", "rich.panel", "rich.table",
                "rich.columns", "rich.syntax", "rich.console", "rich.align", "rich.box",
                "rich.segment", "rich.padding", "rich.rule", "rich.cells", "rich.measure",
                "rich.color_triplet", "rich.layout", "rich.tree", "rich.emoji", "rich.spinner"}
TYPES_ATTRS = {"ModuleType", "SimpleNamespace"}
SYS_ATTRS = {"modules"}
CACHE_KEY = re.compile(r"^_tac_[A-Za-z0-9_]+$")

BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                "breakpoint", "input", "help", "exit", "quit", "memoryview", "setattr",
                "delattr", "dir", "type", "object", "super", "classmethod", "staticmethod",
                "property", "__builtins__", "__loader__", "__spec__"}
# Reflection builtins: the name may appear ONLY as the callee of `getattr(obj, "public_literal"[, default])`.
# Any other occurrence (alias, list/tuple, argument, lambda, walrus, default arg, shadowing) is rejected,
# so a runtime-built attribute name can never reach them.
ATTR_BUILTINS = {"getattr", "hasattr"}
# Attribute names that are top-level module names reach a module re-exported by an allowed one
# (rich.console.sys, rich.console.threading, rich.syntax.os, ...): reject them structurally.
# `random` is exempt — pieces call Random().random(), and no allowed module exposes a `random`
# module attribute (tests/test_check_piece.py walks the reachable modules to keep that true).
MODULE_ATTR_EXEMPT = {"random"}
MODULE_NAMES = (set(sys.stdlib_module_names) | {"rich", "PIL", "fontTools", "threading"}) - MODULE_ATTR_EXEMPT
BANNED_ATTRS = {"sys", "threading", "os", "subprocess", "socket", "shutil", "builtins", "importlib", "io", "pathlib",
                "save_html", "save_svg", "save_text", "from_path", "read_text", "write_text",
                "read_bytes", "write_bytes", "system", "popen", "f_globals", "f_locals", "f_back",
                "f_builtins", "gi_frame", "cr_frame", "ag_frame", "tb_frame", "gi_code", "cr_code",
                "func_globals", "mro"}
DUNDER = re.compile(r"__\w+__")
ALLOWED_FILES = {"piece.py", "meta.yaml", "meta.json", "notes.md", "process"}

HANDLE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$")
SLUG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)?$")
MODEL = re.compile(r"^claude-[a-z0-9][a-z0-9.-]*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _wrap(code: str) -> str:
    # Same wrapping as vscreen: the body runs inside `async def`, so top-level await is legal.
    return "async def __script__():\n" + "".join(f"    {ln}\n" for ln in code.splitlines())


class _Lint(ast.NodeVisitor):
    def __init__(self) -> None:
        self.reasons: list[str] = []
        self.module_alias: dict[str, str] = {}  # local name → module ("sys", "types", ...)
        self.ok_reflection: set[int] = set()  # id() of getattr/hasattr Name nodes in the allowed call form

    def bad(self, node: ast.AST, msg: str) -> None:
        line = max(1, getattr(node, "lineno", 2) - 1)
        self.reasons.append(f"piece.py:{line}: {msg}")

    # imports
    def _module_ok(self, mod: str) -> bool:
        return mod in ALLOWED_MODULES or mod in ALLOWED_RICH

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if not self._module_ok(a.name):
                self.bad(node, f"import of {a.name!r} is not allowed")
            else:
                self.module_alias[(a.asname or a.name).split(".")[0]] = a.name.split(".")[0]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if node.level or not self._module_ok(mod):
            self.bad(node, f"import from {mod or '.'!r} is not allowed")
            return
        for a in node.names:
            if a.name == "*":
                self.bad(node, "star imports are not allowed")
            elif (a.name.startswith("_") or a.name in BANNED_ATTRS or a.name in BANNED_NAMES
                  or a.name in MODULE_NAMES):
                self.bad(node, f"importing {a.name!r} from {mod} is not allowed")
            elif mod == "types" and a.name not in TYPES_ATTRS:
                self.bad(node, f"types.{a.name} is not allowed (only {sorted(TYPES_ATTRS)})")
            elif mod == "sys":
                self.bad(node, "use `import sys` and sys.modules['_tac_<name>'] only")
            elif self._module_ok(f"{mod}.{a.name}") or mod in ALLOWED_RICH or mod in ALLOWED_MODULES:
                pass

    # names and attributes
    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BANNED_NAMES or DUNDER.fullmatch(node.id):
            self.bad(node, f"{node.id!r} is not allowed")
        elif node.id in ATTR_BUILTINS and id(node) not in self.ok_reflection:
            self.bad(node, f"{node.id} may only be called directly as {node.id}(obj, \"public_name\")")

    def _arg_names(self, args: ast.arguments) -> None:
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
            if a is not None and (a.arg in BANNED_NAMES or a.arg in ATTR_BUILTINS):
                self.bad(a, f"parameter name {a.arg!r} is not allowed")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in BANNED_NAMES or node.name in ATTR_BUILTINS or (
                DUNDER.fullmatch(node.name) and node.name != "__init__"):
            self.bad(node, f"defining {node.name!r} is not allowed")
        self._arg_names(node.args)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._arg_names(node.args)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Plain data classes only: no bases, metaclass/keywords or decorators (hook surface).
        if node.bases or node.keywords or node.decorator_list:
            self.bad(node, "classes must be plain: no base classes, metaclass or decorators")
        if node.name in BANNED_NAMES or node.name in ATTR_BUILTINS or DUNDER.fullmatch(node.name):
            self.bad(node, f"class name {node.name!r} is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if DUNDER.fullmatch(attr):
            self.bad(node, f"dunder attribute .{attr} is not allowed")
        elif attr.startswith("_"):
            self.bad(node, f"private attribute .{attr} is not allowed")
        elif attr in BANNED_ATTRS or attr in BANNED_NAMES or attr in ATTR_BUILTINS:
            self.bad(node, f"attribute .{attr} is not allowed")
        elif attr in MODULE_NAMES:
            self.bad(node, f"attribute .{attr} is a module name (modules re-exported by rich etc. are off limits)")
        base = node.value
        if isinstance(base, ast.Name):
            mod = self.module_alias.get(base.id)
            if mod == "sys" and attr not in SYS_ATTRS:
                self.bad(node, f"sys.{attr} is not allowed (sys is only for sys.modules caching)")
            if mod == "types" and attr not in TYPES_ATTRS:
                self.bad(node, f"types.{attr} is not allowed")
        if attr == "modules" and not (isinstance(base, ast.Name) and self.module_alias.get(base.id) == "sys"):
            self.bad(node, ".modules is only allowed as sys.modules")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_sys_modules(node.value) and not self._cache_key(node.slice):
            self.bad(node, "sys.modules keys must be string literals named '_tac_<something>'")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Name) and f.id in ATTR_BUILTINS:
            name = node.args[1] if len(node.args) > 1 else None
            ok = (2 <= len(node.args) <= (3 if f.id == "getattr" else 2) and not node.keywords
                  and not any(isinstance(x, ast.Starred) for x in node.args)
                  and isinstance(name, ast.Constant) and isinstance(name.value, str)
                  and name.value.isidentifier() and not name.value.startswith("_")
                  and name.value not in BANNED_ATTRS)
            if ok:
                self.ok_reflection.add(id(f))
            else:
                self.bad(node, f"{f.id}() needs a literal, public attribute name")
        if isinstance(f, ast.Attribute) and self._is_sys_modules(f.value):
            if f.attr not in ("get", "setdefault", "pop") or not node.args or not self._cache_key(node.args[0]):
                self.bad(node, "sys.modules is only for get/setdefault/pop/[...] on '_tac_<name>' keys")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and DUNDER.search(node.value):
            self.bad(node, "strings containing dunder names are not allowed")
        elif isinstance(node.value, bytes):
            self.bad(node, "bytes literals are not allowed")

    def _is_sys_modules(self, n: ast.AST) -> bool:
        return (isinstance(n, ast.Attribute) and n.attr == "modules" and isinstance(n.value, ast.Name)
                and self.module_alias.get(n.value.id) == "sys")

    @staticmethod
    def _cache_key(n: ast.AST) -> bool:
        return isinstance(n, ast.Constant) and isinstance(n.value, str) and bool(CACHE_KEY.match(n.value))


def check_source(code: str) -> list[str]:
    reasons: list[str] = []
    n_lines = len(code.splitlines())
    if n_lines > MAX_LINES:
        reasons.append(f"piece.py is {n_lines} lines (max {MAX_LINES})")
    if len(code.encode("utf-8")) > MAX_PIECE_BYTES:
        reasons.append(f"piece.py is over {MAX_PIECE_BYTES // 1000} KB")
    try:
        tree = ast.parse(_wrap(code), "<piece>")
    except SyntaxError as e:
        return reasons + [f"piece.py:{max(1, (e.lineno or 2) - 1)}: syntax error: {e.msg}"]
    lint = _Lint()
    for stmt in tree.body[0].body:  # type: ignore[attr-defined]  # skip our own wrapper def
        lint.visit(stmt)
    reasons += lint.reasons
    body = tree.body[0].body  # type: ignore[attr-defined]
    for stmt in body:
        if isinstance(stmt, ast.While) and isinstance(stmt.test, ast.Constant) and stmt.test.value:
            reasons.append(f"piece.py:{stmt.lineno - 1}: top-level `while True` — use `for frame in range(N)`")
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for need in ("canvas", "sleep"):
        if need not in names:
            reasons.append(f"piece.py never uses `{need}` (script contract: canvas.write + await sleep)")
    if not any(isinstance(n, ast.Await) for n in ast.walk(tree)):
        reasons.append("piece.py never awaits (needs `await sleep(dt)` in the frame loop)")
    return reasons


def check_meta(meta: dict[str, Any]) -> list[str]:
    r: list[str] = []

    def is_int(v: Any) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)

    title = meta.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 80:
        r.append("meta: title is required (string, ≤80 chars)")
    model = meta.get("model")
    if not isinstance(model, str) or not MODEL.match(model):
        r.append("meta: model is required, e.g. 'claude-opus-5-5'")
    if "handle" in meta and not (isinstance(meta["handle"], str) and HANDLE.match(meta["handle"])):
        r.append("meta: handle must be lowercase letters/digits/hyphens, ≤39 chars")
    if "description" in meta and not (isinstance(meta["description"], str) and len(meta["description"]) <= 400):
        r.append("meta: description must be a string ≤400 chars")
    if "tokens" in meta and not (meta["tokens"] is None or (is_int(meta["tokens"]) and 0 <= meta["tokens"] <= MAX_TOKENS)):
        r.append(f"meta: tokens must be a whole number 0..{MAX_TOKENS:,} or null (unknown)")
    if "iterations" in meta and not (meta["iterations"] is None or (is_int(meta["iterations"]) and meta["iterations"] >= 0)):
        r.append("meta: iterations must be a non-negative int")
    if "loop_s" in meta:
        v = meta["loop_s"]
        if not (v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 600)):
            r.append("meta: loop_s must be a number of seconds in (0, 600]")
    if "license" in meta and not (isinstance(meta["license"], str) and meta["license"].strip()):
        r.append("meta: license must be a non-empty string")
    if "created" in meta and not (isinstance(meta["created"], str) and DATE.match(meta["created"])):
        r.append("meta: created must be a YYYY-MM-DD string")
    if "house_artist" in meta and not isinstance(meta["house_artist"], bool):
        r.append("meta: house_artist must be true/false")
    if "size" in meta and meta["size"] not in ("sketch", "full"):
        r.append("meta: size must be 'sketch' or 'full'")
    if meta.get("size") == "sketch" and is_int(meta.get("iterations")) and meta["iterations"] > 3:
        r.append(f"meta: a sketch is at most 3 iterations (this one has {meta['iterations']}) — use size: full")
    if "human_role" in meta and meta["human_role"] not in HUMAN_ROLES:
        r.append(f"meta: human_role must be one of {', '.join(HUMAN_ROLES)}")
    pn = meta.get("process_notes")
    if pn is not None and not (isinstance(pn, list) and len(pn) <= MAX_PROCESS and all(isinstance(s, str) for s in pn)):
        r.append(f"meta: process_notes must be a list of ≤{MAX_PROCESS} strings")
    return r


def check_dir(piece_dir: Path) -> list[str]:
    if not piece_dir.is_dir():
        return [f"{piece_dir} is not a directory"]
    reasons: list[str] = []
    extra = sorted(p.name for p in piece_dir.iterdir() if p.name not in ALLOWED_FILES and not p.name.startswith("."))
    if extra:
        reasons.append(f"unexpected files: {', '.join(extra)} (allowed: piece.py, meta.yaml|json, notes.md, process/)")
    src = piece_dir / "piece.py"
    if not src.is_file():
        reasons.append("missing piece.py")
    else:
        try:
            reasons += check_source(src.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            reasons.append("piece.py is not UTF-8")
    try:
        meta = load_meta(piece_dir)
        reasons += check_meta(meta)
    except MetaError as e:
        meta = {}
        reasons.append(f"meta: {e}")
    # In the community repo layout pieces/<handle>/<slug>/, the path must agree with the meta.
    if piece_dir.resolve().parent.parent.name == "pieces":
        handle, slug = piece_dir.resolve().parent.name, piece_dir.resolve().name
        if not SLUG.match(slug):
            reasons.append(f"slug {slug!r}: lowercase, one word or two hyphenated")
        if meta.get("handle") != handle:
            reasons.append(f"meta: handle must be {handle!r} (the folder it lives in)")
    notes = piece_dir / "notes.md"
    if notes.exists() and notes.stat().st_size > MAX_NOTES_BYTES:
        reasons.append(f"notes.md is over {MAX_NOTES_BYTES // 1000} KB")
    # human_role is computed from notes.md's `## direction` log; a declared role must match it.
    declared = meta.get("human_role")
    if declared in HUMAN_ROLES and declared != "none":
        text = notes.read_text(encoding="utf-8", errors="replace") if notes.exists() else ""
        if human_role(text) != declared:
            reasons.append(f"meta: human_role {declared!r} doesn't match the `## direction` log in notes.md "
                           f"(computed {human_role(text)!r})")
    proc = piece_dir / "process"
    if proc.exists():
        files = sorted(p for p in proc.iterdir() if not p.name.startswith("."))
        if len(files) > MAX_PROCESS:
            reasons.append(f"process/ has {len(files)} files (max {MAX_PROCESS})")
        for p in files:
            if p.suffix.lower() != ".png" or not p.is_file():
                reasons.append(f"process/{p.name}: only .png files")
                continue
            if p.stat().st_size > MAX_PROCESS_BYTES:
                reasons.append(f"process/{p.name} is {p.stat().st_size // 1000} KB (max {MAX_PROCESS_BYTES // 1000} KB)")
            with p.open("rb") as fh:
                if fh.read(8) != b"\x89PNG\r\n\x1a\n":
                    reasons.append(f"process/{p.name} is not a PNG")
    return reasons


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2
    reasons = check_dir(Path(args[0]))
    print(json.dumps({"ok": not reasons, "reasons": reasons}, ensure_ascii=False))
    return 0 if not reasons else 1


if __name__ == "__main__":
    sys.exit(main())
