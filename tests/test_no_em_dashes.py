"""
The house style bans em dashes, and until now nothing enforced it.

Worth a test rather than a habit because most of this repo's value is in its
prose: the manifests carry player-facing strings, the UI carries hint text, and
the README is the first thing anyone reads. One em dash pasted in from a browser
is invisible in review and permanent once it ships.

Third-party text is exempt. LICENSE is the GPL as published and the readmes
under assets/mods/ are other people's words, so rewriting either to suit our
punctuation rules would be both rude and, for the licence, wrong.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# built at runtime rather than typed, so this file cannot trip its own check.
# an escape would have done the same job right up until something normalised it
# back into the literal character, which is exactly what happened on the first
# cut of this file. chr() cannot be normalised into anything.
# 0x2013 is the en dash, add it here if those start creeping in too.
BANNED = {chr(0x2014): "em dash"}

SUFFIXES = {".py", ".md", ".json", ".js", ".mjs", ".html", ".css", ".txt",
            ".yml", ".yaml", ".ps1", ".sh", ".cpp", ".h", ".def", ".spec",
            ".manifest", ".cmake"}

EXACT_NAMES = {"CMakeLists.txt", ".gitignore", ".gitattributes", "requirements.txt"}

# matched against path PARTS, so "build" also covers addon-src/*/build/
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache",
             "build", "dist", "node_modules", "venv", ".venv"}

# third-party prose we do not get to restyle
SKIP_PATHS = {"LICENSE"}
SKIP_PREFIXES = ("assets/mods/",)


def candidates():
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO).as_posix()
        if any(part in SKIP_DIRS for part in path.relative_to(REPO).parts):
            continue
        if relative in SKIP_PATHS or relative.startswith(SKIP_PREFIXES):
            continue
        if path.suffix.lower() not in SUFFIXES and path.name not in EXACT_NAMES:
            continue
        yield path, relative


def test_no_em_dashes_anywhere_we_own():
    offences = []

    for path, relative in candidates():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # not text, or not readable. either way not this test's problem.
            continue
        if not any(ch in text for ch in BANNED):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for ch, name in BANNED.items():
                if ch in line:
                    offences.append(f"{relative}:{number}  ({name})  {line.strip()[:90]}")

    assert not offences, (
        f"{len(offences)} banned dash(es). use a comma, a colon or a full stop:\n  "
        + "\n  ".join(offences))


def test_the_check_actually_looks_at_something():
    """
    A filter this aggressive can end up excluding the whole repo and passing
    forever. Pin the floor so a future skip rule cannot quietly switch the test
    off.
    """
    found = {relative for _, relative in candidates()}

    assert len(found) > 50, f"only {len(found)} files scanned, the filters are too wide"
    for expected in ("README.md", "main.py", "shell.py", "ui/index.html",
                     "manifests/ffxiii.json", "patcher/engine.py"):
        assert expected in found, f"{expected} is not being scanned"


def test_third_party_text_is_exempt_rather_than_clean():
    """
    Documents the exemption so nobody 'fixes' the GPL. If LICENSE ever stops
    existing this fails and the skip rule gets revisited rather than rotting.
    """
    assert (REPO / "LICENSE").is_file()
    assert "LICENSE" not in {relative for _, relative in candidates()}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
