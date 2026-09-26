"""Static analysis for sandboxed code, and what "verified" means.

``analyse(code)`` parses the source, refuses the constructs no engineering calculation needs
(``eval``/``exec``, dynamic imports, process spawning, file deletion, absolute-path opens,
banned modules) and runs pyflakes over it. The runner combines this with the task's own tests:

    verified  =  static analysis passed  AND  tests were supplied  AND  every test passed

"Ran without crashing" is not on that list.
"""
from __future__ import annotations

import ast
import io
import re

from workbench.sandbox.manifest import BANNED_IMPORTS

BANNED_CALLS: tuple[str, ...] = (
    "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "breakpoint", "input", "exit", "quit",
    "os.system", "os.popen", "os.execv", "os.execve", "os.execl", "os.execlp", "os.execvp", "os.spawnl", "os.spawnv",
    "os.fork", "os.kill", "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename", "os.replace", "os.chmod",
    "os.chown", "os.symlink", "os.link", "os.startfile", "os.putenv", "os.setuid", "os.setgid",
    "shutil.rmtree", "shutil.move", "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output",
    "ctypes.CDLL", "ctypes.WinDLL", "importlib.import_module", "sys.exit",
    "setattr", "delattr", "getattr",
)
_ABS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})")


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _banned_module(name: str) -> bool:
    return any(name == b or name.startswith(b + ".") for b in BANNED_IMPORTS)


class _Reporter:
    """pyflakes reporter that collects messages instead of printing them."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def unexpectedError(self, filename, msg):  # noqa: N802 (pyflakes API)
        self.messages.append(f"{filename}: {msg}")

    def syntaxError(self, filename, msg, lineno, offset, text):  # noqa: N802
        self.messages.append(f"{filename}:{lineno}: {msg}")

    def flake(self, message):
        self.messages.append(str(message))


def _pyflakes(code: str) -> list[str]:
    try:
        from pyflakes.api import check
    except Exception:
        return []
    reporter = _Reporter()
    try:
        check(code, "main.py", reporter)
    except Exception as exc:
        reporter.messages.append(f"pyflakes failed: {exc}")
    return reporter.messages


def analyse(code: str) -> dict:
    """Static verdict on one piece of code. ``ok`` is False when anything banned is present."""
    out: dict = {"ok": True, "syntax_error": None, "banned_imports": [], "banned_calls": [], "pyflakes": [],
                 "complexity": {"lines": len(code.splitlines()), "functions": 0, "max_nesting": 0}}
    try:
        tree = ast.parse(code, filename="main.py")
    except SyntaxError as exc:
        out["ok"] = False
        out["syntax_error"] = f"line {exc.lineno}: {exc.msg}"
        return out

    max_depth = 0

    def walk(node: ast.AST, depth: int) -> None:
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        for child in ast.iter_child_nodes(node):
            nested = isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.FunctionDef,
                                        ast.AsyncFunctionDef, ast.ClassDef))
            walk(child, depth + (1 if nested else 0))

    walk(tree, 0)
    out["complexity"]["max_nesting"] = max_depth

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["complexity"]["functions"] += 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _banned_module(alias.name):
                    out["banned_imports"].append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _banned_module(mod):
                out["banned_imports"].append(f"line {node.lineno}: from {mod} import ...")
            for alias in node.names:
                full = f"{mod}.{alias.name}" if mod else alias.name
                if _banned_module(full):
                    out["banned_imports"].append(f"line {node.lineno}: from {mod} import {alias.name}")
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in BANNED_CALLS:
                out["banned_calls"].append(f"line {node.lineno}: {name}()")
            elif name in ("open", "io.open", "pathlib.Path", "Path") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and _ABS_PATH.match(first.value):
                    out["banned_calls"].append(f"line {node.lineno}: {name}() with absolute path {first.value!r}")
        elif isinstance(node, ast.Attribute) and node.attr in ("__subclasses__", "__globals__", "__builtins__", "__code__"):
            out["banned_calls"].append(f"line {node.lineno}: attribute {node.attr}")

    out["pyflakes"] = _pyflakes(code)
    out["ok"] = not (out["banned_imports"] or out["banned_calls"])
    return out
