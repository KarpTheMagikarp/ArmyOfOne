"""
Keeps the comments in the house style: no banner or all-caps heading
comments, and no docstring long enough to be a design doc. Long reasoning
goes in docs/.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_DOC_MAX = 30
FUNCTION_DOC_MAX = 25

BANNER = re.compile(r"^\s*(#|//|/\*|<!--)\s*[-=*#~_]{4,}")
# a comment line that's nothing but capitals, like "# WHY THIS EXISTS"
SHOUTING = re.compile(r"^\s*(#|//|\*)?\s*[A-Z][A-Z0-9'/&,]*(\s+[A-Z0-9'/&,]+){2,}\s*:?\s*$")


def ours(*patterns):
    for pattern in patterns:
        for path in sorted(ROOT.glob(pattern)):
            if "__pycache__" not in path.parts:
                yield path


PY = list(ours("*.py", "patcher/**/*.py", "tools/**/*.py"))
NATIVE = list(ours("addon-src/**/*.cpp", "addon-src/**/*.h", "tools/**/*.js", "ui/index.html"))


def comment_lines(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        tree = ast.parse(text)
        docs = set()
        for node in ast.walk(tree):
            doc = _doc_node(node)
            if doc is not None:
                docs.update(range(doc.lineno, doc.end_lineno + 1))
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#") or number in docs:
                yield number, line
        return
    inside = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("/*", "<!--")):
            inside = True
        if inside or stripped.startswith("//"):
            yield number, line
        if "*/" in stripped or "-->" in stripped:
            inside = False


def _doc_node(node):
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            return first
    return None


@pytest.mark.parametrize("path", PY + NATIVE, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_banners_or_shouted_headings(path):
    bad = [f"{n}: {line.strip()}" for n, line in comment_lines(path)
           if BANNER.match(line) or SHOUTING.match(line)]
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("path", PY, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_docstrings_stay_short(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    long = []
    for node in ast.walk(tree):
        doc = _doc_node(node)
        if doc is None:
            continue
        size = doc.end_lineno - doc.lineno + 1
        limit = MODULE_DOC_MAX if isinstance(node, ast.Module) else FUNCTION_DOC_MAX
        if size > limit:
            long.append(f"{getattr(node, 'name', '<module>')} is {size} lines (max {limit})")
    assert not long, "\n".join(long)
