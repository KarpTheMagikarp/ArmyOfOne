"""
The add-on's decision logic, compiled and run as part of the normal suite.

The add-on itself only builds on Windows, but the guards are where a wrong
answer is silent and destroys a file the user cannot get back. Those are in a
platform-free header, so they can be built with whatever compiler is here and
checked on every run rather than only when someone opens Visual Studio.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "addon-src" / "ff13-exe-redirect"


@pytest.fixture(scope="module")
def policy_test(tmp_path_factory):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("no host C++ compiler")
    # mingw's g++ tacks .exe on whether you asked for it or not
    binary = tmp_path_factory.mktemp("addon") / ("policy_test" + (".exe" if os.name == "nt" else ""))
    build = subprocess.run(
        [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror",
         "-o", str(binary), str(SOURCE / "redirect_policy_test.cpp")],
        capture_output=True, text=True)
    if build.returncode != 0:
        pytest.fail(f"the policy header did not compile:\n{build.stderr}")
    return binary


def test_the_policy_header_compiles_without_warnings(policy_test):
    assert policy_test.is_file()


def test_every_guard_in_the_redirect_holds(policy_test):
    result = subprocess.run([str(policy_test)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    assert "all checks passed" in result.stdout
    # the guard whose failure destroys untouched.exe
    assert "GENERIC_WRITE is not redirected" in result.stdout


def test_the_addon_defers_to_the_tested_header():
    """
    the Win32 file must not grow its own copy of a decision. duplicated logic
    is logic that drifts out of test coverage.
    """
    source = (SOURCE / "ff13_exe_redirect.cpp").read_text(encoding="utf-8")
    assert '#include "redirect_policy.h"' in source
    assert "ShouldRedirect(" in source
    for reimplemented in ("bool WantsWrite(", "bool SameText("):
        assert reimplemented not in source, f"{reimplemented} belongs in the header"
