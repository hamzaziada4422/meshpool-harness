"""Zero-token mutation feedback (WP7): break the code under test in small ways and see whether the agent's tests
notice. Python only. Works on a temp copy; the real folder is never touched."""
from __future__ import annotations

import ast
import copy
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache"}
MAX_PER_FILE = 25


def is_test_file(rel: str) -> bool:
    rel = rel.replace(os.sep, "/")
    name = rel.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")
                                     or "tests" in rel.split("/")[:-1])


def _imports(path: str) -> set[str]:
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
            out |= {n.module + "." + a.name for a in n.names}
    return out


def code_under_test(root: str, changed: set[str]) -> tuple[list[str], list[str]]:
    """(test files the agent touched, project .py files they import that the agent did not touch), relative paths."""
    root = os.path.abspath(root)
    rels = set()
    for f in changed:
        try:
            rel = os.path.relpath(f, root)
        except ValueError:
            continue
        if not rel.startswith("..") and f.endswith(".py") and os.path.isfile(os.path.join(root, rel)):
            rels.add(rel)
    tests = sorted(r for r in rels if is_test_file(r))
    if not tests:
        return [], []
    mods: dict[str, str] = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(dp, f), root)
                if not is_test_file(rel):
                    mod = rel[:-3].replace(os.sep, ".")
                    mods[mod] = rel
                    if mod.endswith(".__init__"):
                        mods[mod[:-9]] = rel
    wanted: set[str] = set()
    for t in tests:
        for imp in _imports(os.path.join(root, t)):
            parts = imp.split(".")
            for i in range(len(parts), 0, -1):
                m = ".".join(parts[:i])
                if m in mods:
                    wanted.add(mods[m])
                    break
    return tests, sorted(r for r in wanted if r not in rels)


_CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_CMP_SWAP = {ast.Lt: ast.Gt, ast.Gt: ast.Lt}
_ARITH = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Mod,
          ast.Mod: ast.FloorDiv}
_SYM = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=", ast.Add: "+",
        ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%"}


def _sites(tree: ast.AST) -> list[tuple]:
    """Every possible single change: (kind, node index in walk order, detail, line)."""
    out = []
    for i, n in enumerate(ast.walk(tree)):
        line = getattr(n, "lineno", 0)
        if isinstance(n, ast.Compare) and len(n.ops) == 1:
            op = type(n.ops[0])
            if op in _CMP:
                out.append(("cmp", i, (op, _CMP[op]), line))
            if op in _CMP_SWAP:
                out.append(("cmp", i, (op, _CMP_SWAP[op]), line))
        elif isinstance(n, ast.BinOp) and type(n.op) in _ARITH:
            out.append(("arith", i, (type(n.op), _ARITH[type(n.op)]), line))
        elif isinstance(n, ast.Constant) and type(n.value) is int:
            out.append(("const", i, (n.value, n.value + 1), line))
            out.append(("const", i, (n.value, n.value - 1), line))
        elif isinstance(n, ast.BoolOp):
            out.append(("bool", i, (type(n.op), ast.Or if isinstance(n.op, ast.And) else ast.And), line))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            out.append(("not", i, None, line))
        elif isinstance(n, ast.Return) and n.value is not None and not (
                isinstance(n.value, ast.Constant) and n.value.value is None):
            out.append(("ret", i, None, line))
        elif isinstance(n, ast.If) and not (len(n.body) == 1 and isinstance(n.body[0], ast.Pass)):
            out.append(("ifpass", i, None, line))
    return out


def _spread(items: list, k: int) -> list:
    if len(items) <= k:
        return items
    return [items[(j * len(items)) // k] for j in range(k)]


def _apply(tree: ast.AST, site: tuple) -> str | None:
    kind, idx, detail, line = site
    t = copy.deepcopy(tree)
    nodes = list(ast.walk(t))
    n = nodes[idx]
    if kind == "cmp":
        n.ops = [detail[1]()]
        desc = f"`{_SYM[detail[0]]}` changed to `{_SYM[detail[1]]}`"
    elif kind == "arith":
        n.op = detail[1]()
        desc = f"`{_SYM[detail[0]]}` changed to `{_SYM[detail[1]]}`"
    elif kind == "const":
        n.value = detail[1]
        desc = f"constant {detail[0]} changed to {detail[1]}"
    elif kind == "bool":
        n.op = detail[1]()
        desc = f"`{'and' if detail[0] is ast.And else 'or'}` changed to `{'or' if detail[0] is ast.And else 'and'}`"
    elif kind == "not":
        # replace the UnaryOp by its operand: find the parent
        for p in nodes:
            for fld, v in ast.iter_fields(p):
                if v is n:
                    setattr(p, fld, n.operand)
                elif isinstance(v, list):
                    for j, x in enumerate(v):
                        if x is n:
                            v[j] = n.operand
        desc = "`not` removed"
    elif kind == "ret":
        n.value = ast.Constant(value=None)
        desc = "return value replaced with `None`"
    else:
        n.body = [ast.Pass()]
        desc = "body of this `if` replaced with `pass`"
    ast.fix_missing_locations(t)
    try:
        src = ast.unparse(t)
    except Exception:  # noqa: BLE001
        return None
    return src, f"line {line}: {desc}"


def mutants(source: str, limit: int = MAX_PER_FILE) -> list[tuple[str, str]]:
    """Up to `limit` (mutated source, description) pairs, one change each, spread across the file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    sites = _sites(tree)
    # skip docstring-like and annotation-ish constants cheaply: keep only constants outside the module docstring
    out = []
    base = ast.unparse(tree)
    for s in _spread(sites, limit * 2):
        r = _apply(tree, s)
        if r and r[0] != base:
            out.append((r[0], r[1]))
        if len(out) >= limit:
            break
    return out


def _runner(tests: list[str], cwd: str) -> list[list[str]]:
    pytest = importlib.util.find_spec("pytest") is not None
    looks = False
    for t in tests:
        try:
            looks = looks or bool(re.search(r"^(def test_|class Test|import pytest|from pytest)",
                                           open(os.path.join(cwd, t), encoding="utf-8").read(), re.M))
        except OSError:
            pass
    if pytest and looks:
        return [[sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests]]
    return [[sys.executable, f] for f in tests]


def _run(cmds: list[list[str]], cwd: str, timeout: float, should_stop=None) -> bool | None:
    """True = pass, False = fail, None = timed out or stopped. The process (group) is killed on either."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=cwd + os.pathsep + os.environ.get("PYTHONPATH", ""))
    for c in cmds:
        kw = {"start_new_session": True} if os.name != "nt" else {}
        p = subprocess.Popen(c, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **kw)
        end = time.time() + timeout
        while p.poll() is None:
            if (should_stop and should_stop()) or time.time() > end:
                try:
                    if os.name != "nt":
                        os.killpg(p.pid, 9)
                    else:
                        p.kill()
                except OSError:
                    pass
                p.wait()
                return None
            time.sleep(0.2)
        if p.returncode != 0:
            return False
    return True


def survivors(root: str, changed: set[str], per_mutant: float = 10, total: float = 60, should_stop=None) -> list[dict]:
    """Mutants of the code under test that the agent's tests do not catch. [] when none, or when feedback is not
    possible (no code under test, tests fail on the original, timeouts)."""
    tests, targets = code_under_test(root, changed)
    if not tests or not targets:
        return []
    start = time.time()
    tmp = tempfile.mkdtemp(prefix="mh_mut_")
    try:
        work = os.path.join(tmp, "p")
        shutil.copytree(root, work, ignore=shutil.ignore_patterns(*SKIP_DIRS), symlinks=True)
        cmds = _runner(tests, work)
        if _run(cmds, work, per_mutant, should_stop) is not True:        # the tests must pass on the unmutated copy first
            return []
        alive: list[dict] = []
        for rel in targets:
            path = os.path.join(work, rel)
            try:
                orig = open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            for src, desc in mutants(orig):
                if time.time() - start > total or (should_stop and should_stop()):
                    return alive
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(src)
                    res = _run(cmds, work, per_mutant, should_stop)
                finally:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(orig)
                if res is True:
                    alive.append({"file": rel, "line": int(desc.split(":")[0].split()[1]), "desc": desc})
        return alive
    except Exception:  # noqa: BLE001 - feedback is optional
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def format_survivors(alive: list[dict], limit: int = 8) -> str:
    return "\n".join(f"- {a['file']}, {a['desc']}" for a in alive[:limit])
