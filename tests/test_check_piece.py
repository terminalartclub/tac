import json
import subprocess
import sys
from pathlib import Path

import pytest

from check_piece import check_dir, check_source
from conftest import ROOT

TOOL = ROOT / "tools" / "check_piece.py"
OK_BODY = "for frame in range(10):\n    canvas.clear()\n    canvas.write(Text('x'))\n    await sleep(0.1)\n"


def test_good_fixture_passes(good: Path) -> None:
    assert check_dir(good) == []


def test_cli_contract(good: Path) -> None:
    r = subprocess.run([sys.executable, str(TOOL), str(good)], capture_output=True, text=True)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"ok": True, "reasons": []}
    (good / "piece.py").write_text("import os\n" + OK_BODY)
    r = subprocess.run([sys.executable, str(TOOL), str(good)], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["ok"] is False and any("'os'" in x for x in out["reasons"])


def test_meta_json_accepted(good: Path) -> None:
    (good / "meta.yaml").unlink()
    (good / "meta.json").write_text(json.dumps({"title": "ember", "model": "claude-opus-5-5", "tokens": None}))
    assert check_dir(good) == []


@pytest.mark.parametrize("piece", sorted((ROOT / "pieces").glob("*/*")), ids=lambda p: p.name)
def test_seed_pieces_pass(piece: Path) -> None:
    assert check_dir(piece) == []


@pytest.mark.parametrize("src", [
    "from rich.text import Text\nfrom rich.style import Style\nimport math, random, colorsys, functools, itertools\n",
    "import sys\n_c = sys.modules.get('_tac_x')\nsys.modules['_tac_x'] = _c\n",
    "import types\nm = types.ModuleType('_tac_x')\nm.key = getattr(m, 'key', None)\n",
    "import bisect, cmath\n",
    "def sdf_open(x):\n    return x\n",  # names merely containing a banned word are fine
    "x = getattr(canvas, 'key', None)\ny = hasattr(canvas, 'key')\n",  # the allowed form (wake)
    "class P:\n    def __init__(self, x):\n        self.x = x\n",
])
def test_allowed(src: str) -> None:
    assert check_source(src + OK_BODY) == []


@pytest.mark.parametrize("src,needle", [
    ("import os\n", "'os'"),
    ("import subprocess\n", "'subprocess'"),
    ("import socket\n", "'socket'"),
    ("from urllib import request\n", "urllib"),
    ("import asyncio\n", "asyncio"),
    ("from . import x\n", "not allowed"),
    ("open('/etc/passwd')\n", "'open'"),
    ("exec('1')\n", "'exec'"),
    ("eval('1')\n", "'eval'"),
    ("compile('1', 'x', 'exec')\n", "'compile'"),
    ("__import__('os')\n", "__import__"),
    ("x = globals()\n", "'globals'"),
    ("getattr(canvas, '__class__')\n", "dunder"),
    ("getattr(canvas, name)\n", "literal"),
    ("x = canvas.__class__\n", "dunder attribute"),
    ("import random\nrandom._os\n", "private attribute"),
    ("import sys\nsys.path.append('x')\n", "sys.path"),
    ("import sys\nsys.modules['os']\n", "_tac_"),
    ("import sys\nsys.modules.get('os')\n", "_tac_"),
    ("from rich.console import os\n", "'os'"),
    ("import rich.console\nrich.console.os.system('x')\n", ".os"),
    ("from rich.console import Console\nConsole().save_html('x')\n", "save_html"),
    ("s = '{0.__class__}'.format(canvas)\n", "dunder"),
    ("import types\ntypes.FunctionType\n", "types.FunctionType"),
    ("from types import CodeType\n", "CodeType"),
    ("g = (i for i in [])\ng.gi_frame\n", "gi_frame"),
    ("while True:\n    pass\n", "while True"),
    # reflection builtins outside the exact `getattr(obj, "public")` call form
    ("g = getattr\ng(canvas, 'x')\n", "may only be called directly"),                  # alias
    ("[getattr][0](canvas, 'x')\n", "may only be called directly"),                      # list indirection
    ("(getattr,)[0](canvas, 'x')\n", "may only be called directly"),                     # tuple indirection
    ("list(map(getattr, [canvas], ['x']))\n", "may only be called directly"),           # passed as argument
    ("f = lambda o, n: getattr(o, n)\n", "literal, public"),                             # lambda-wrapped
    ("(g := getattr)(canvas, 'x')\n", "may only be called directly"),                    # walrus
    ("def f(o, g=getattr):\n    return g(o, 'x')\n", "may only be called directly"),     # default arg
    ("def f(getattr):\n    return getattr(canvas, 'x')\n", "parameter name"),           # shadowing param
    ("n = chr(95) * 2 + 'class' + chr(95) * 2\ngetattr(canvas, n)\n", "literal, public"),  # built name
    ("getattr(canvas, 'x' + 'y')\n", "literal, public"),
    ("getattr(canvas, *['x'])\n", "literal, public"),
    ("hasattr(canvas, 'x', 1)\n", "literal, public"),
    ("import functools\nfunctools.reduce(getattr, ['x'], canvas)\n", "may only be called directly"),
    ("x = type(canvas)\n", "'type'"),
    ("x = object\n", "'object'"),
    ("super()\n", "'super'"),
    ("dir(canvas)\n", "'dir'"),
    ("setattr(canvas, 'x', 1)\n", "'setattr'"),
    ("import random\nrandom.getattr\n", ".getattr"),
    ("class K(object):\n    pass\n", "plain"),
    # modules re-exported by rich: rich.console.sys / .threading / .os / .re
    ("import rich.console\nrich.console.sys\n", ".sys"),
    ("import rich.style\nrich.style.sys.settrace(None)\n", ".sys"),
    ("import rich.console\nrich.console.threading\n", ".threading"),
    ("from rich import console\nconsole.sys.setrecursionlimit(10)\n", ".sys"),
    ("from rich.console import threading\n", "'threading'"),
    ("from rich.syntax import textwrap\n", "'textwrap'"),
    ("import rich.text\nrich.text.re.compile('x')\n", "module name"),
    ("import rich.syntax\nrich.syntax.textwrap\n", "module name"),
    ("class K:\n    def __getattr__(self, n):\n        return n\n", "__getattr__"),
])
def test_rejected(src: str, needle: str) -> None:
    reasons = check_source(src + OK_BODY)
    assert reasons and any(needle in r for r in reasons), reasons


def test_contract_and_size_rules() -> None:
    assert any("sleep" in r for r in check_source("canvas.write(Text('x'))\n"))
    assert any("syntax" in r for r in check_source("def (:\n"))
    long = OK_BODY + "x = 1\n" * 1500
    assert any("1500" in r for r in check_source(long))


def test_line_numbers_match_the_file() -> None:
    reasons = check_source("x = 1\nimport os\n" + OK_BODY)
    assert reasons[0].startswith("piece.py:2:")


@pytest.mark.parametrize("meta,needle", [
    ({"title": "x"}, "model is required"),
    ({"title": "x", "model": "gpt-4"}, "model is required"),
    ({"model": "claude-opus-5-5"}, "title is required"),
    ({"title": "x", "model": "claude-opus-5-5", "tokens": -1}, "tokens"),
    ({"title": "x", "model": "claude-opus-5-5", "tokens": True}, "tokens"),
    ({"title": "x", "model": "claude-opus-5-5", "tokens": "lots"}, "tokens"),
    ({"title": "x", "model": "claude-opus-5-5", "tokens": 2_000_001}, "tokens"),
    ({"title": "x", "model": "claude-opus-5-5", "tokens": 1.0}, "tokens"),
    ({"title": "x", "model": "claude-opus-5-5", "loop_s": 0}, "loop_s"),
    ({"title": "x", "model": "claude-opus-5-5", "created": "Oct 2"}, "created"),
    ({"title": "x", "model": "claude-opus-5-5", "handle": "Bad Handle"}, "handle"),
    ({"title": "x", "model": "claude-opus-5-5", "description": "y" * 401}, "description"),
    ({"title": "x", "model": "claude-opus-5-5", "process_notes": ["a"] * 5}, "process_notes"),
])
def test_bad_meta(good: Path, meta: dict, needle: str) -> None:
    (good / "meta.yaml").unlink()
    (good / "meta.json").write_text(json.dumps(meta))
    reasons = check_dir(good)
    assert any(needle in r for r in reasons), reasons


def test_bad_meta_yaml_syntax(good: Path) -> None:
    (good / "meta.yaml").write_text("title: x\n  nested: y\n")
    assert any("flat" in r for r in check_dir(good))


def test_missing_meta(good: Path) -> None:
    (good / "meta.yaml").unlink()
    assert any("missing meta" in r for r in check_dir(good))


def test_process_rules(good: Path) -> None:
    proc = good / "process"
    big = proc / "02-big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 600_001)
    assert any("max 600 KB" in r for r in check_dir(good))
    big.unlink()
    (proc / "02.jpg").write_bytes(b"x")
    assert any("only .png" in r for r in check_dir(good))
    (proc / "02.jpg").unlink()
    (proc / "03.png").write_bytes(b"GIF89a")
    assert any("not a PNG" in r for r in check_dir(good))
    for i in range(3, 7):
        (proc / f"0{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert any("max 4" in r for r in check_dir(good))


def test_unexpected_files(good: Path) -> None:
    (good / "run.sh").write_text("rm -rf /")
    assert any("unexpected files" in r for r in check_dir(good))


def test_repo_layout_handle_must_match(tmp_path: Path, good: Path) -> None:
    d = tmp_path / "pieces" / "someone" / "ember"
    d.parent.mkdir(parents=True)
    good.rename(d)
    assert any("handle must be 'someone'" in r for r in check_dir(d))
    (d / "meta.yaml").write_text((d / "meta.yaml").read_text() + 'handle: "someone"\n')
    assert check_dir(d) == []


def test_docstring_says_lint_not_sandbox() -> None:
    import check_piece
    assert "LINT, NOT A SANDBOX" in check_piece.__doc__


def test_no_reachable_module_passes_the_lint() -> None:
    """Walk module objects reachable from every allowed module through attribute names the lint
    permits; each must be an allowed module itself (guards the `random` exemption too)."""
    import importlib
    import types

    import check_piece as cp
    from check_piece import check_source

    def lint_ok(attr: str) -> bool:
        return check_source(f"import math\nx = math.{attr}\n" + OK_BODY) == []

    allowed = cp.ALLOWED_MODULES | cp.ALLOWED_RICH
    todo = [importlib.import_module(m) for m in sorted(allowed - {"sys", "types"})]
    seen: set[str] = set()
    leaks = []
    while todo:
        mod = todo.pop()
        if mod.__name__ in seen:
            continue
        seen.add(mod.__name__)
        for k, v in vars(mod).items():
            if isinstance(v, types.ModuleType) and lint_ok(k):
                if v.__name__ in allowed or v.__name__.startswith("rich."):
                    todo.append(v)
                else:
                    leaks.append(f"{mod.__name__}.{k} -> {v.__name__}")
    assert leaks == []


@pytest.mark.parametrize("tokens", [0, 2_000_000, None])
def test_tokens_boundaries_pass(good: Path, tokens) -> None:
    (good / "meta.yaml").unlink()
    (good / "meta.json").write_text(json.dumps({"title": "x", "model": "claude-opus-5-5", "tokens": tokens}))
    assert not [r for r in check_dir(good) if "tokens" in r]
