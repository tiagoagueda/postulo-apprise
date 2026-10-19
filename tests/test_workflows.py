"""The workflows, held to what the core's CI holds itself to (postulo/postulo#233, #790, #13).

A lock file read by an unpinned tool is pinned only in name, and a lock nobody audits says
nothing when an advisory is published against Apprise or what it brings. These read the
workflow files as text, so they need no YAML library and fail on the line that is wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".forgejo" / "workflows").glob("*.yml"))


def test_there_are_workflows_to_read():
    assert {path.name for path in WORKFLOWS} >= {"ci.yml", "release.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_uv_is_installed_at_a_named_version_and_checked(path):
    text = path.read_text(encoding="utf-8")
    assert "astral.sh/uv/install.sh" not in text, "the installer of whichever uv is newest"
    assert re.search(r'^\s+UV_VERSION: "\d+\.\d+\.\d+"$', text, re.M)
    assert re.search(r'^\s+UV_INSTALLER_SHA256: "[0-9a-f]{64}"$', text, re.M)
    installs = text.count("astral.sh/uv/${UV_VERSION}/install.sh")
    assert installs and installs == text.count("sha256sum -c -"), "every installer is checked"


def test_the_locked_dependencies_are_audited_on_a_schedule():
    text = (WORKFLOWS[0].parent / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+- cron: ", text, re.M), "a weekly run, for a quiet week"
    assert re.search(r"pip-audit==\d+\.\d+\.\d+ pip-audit --strict", text), "pinned, strict"
    assert "uv export --no-emit-project" in text, "the lock, not the installed environment"
