"""The workflows, held to what the core's CI holds itself to (postulo/postulo#233, #790, #13).

A lock file read by an unpinned tool is pinned only in name, and a lock nobody audits says
nothing when an advisory is published against Apprise or what it brings. The pinned uv and
its checked installer now live in the shared plugin CI, Postulo/plugin-ci
(postulo/postulo#1170), so what is held here is that every workflow calls that action, by
its full address, and that the audit and the older Pythons still run. These read the
workflow files as text, so they need no YAML library and fail on the line that is wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".forgejo" / "workflows").glob("*.yml"))
FOLDER = WORKFLOWS[0].parent
ACTION = "https://source.tiagoagueda.com/Postulo/plugin-ci@v1"


def test_there_are_workflows_to_read():
    assert {path.name for path in WORKFLOWS} >= {
        "ci.yml",
        "release.yml",
        "every-python.yml",
        "weekly.yml",
    }


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_every_workflow_runs_the_shared_action_rather_than_its_own_copy(path):
    text = path.read_text(encoding="utf-8")
    assert "astral.sh" not in text, "uv is installed by the shared action, pinned and checked"
    uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)", text, re.M)
    assert ACTION in uses
    # A bare `Postulo/plugin-ci` would be looked up on the instance's default actions host.
    assert set(uses) <= {"actions/checkout@v4", ACTION}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_no_job_is_held_back_by_an_if(path):
    """This Forgejo fails a job whose `if` is false instead of skipping it."""
    assert not re.search(r"^ {4}if:", path.read_text(encoding="utf-8"), re.M)


def test_the_locked_dependencies_are_audited_on_every_push_and_on_a_schedule():
    weekly = (FOLDER / "weekly.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+- cron: ", weekly, re.M), "a weekly run, for a quiet week"
    for name in ("ci.yml", "weekly.yml"):
        text = (FOLDER / name).read_text(encoding="utf-8")
        # The status check's name, which a branch protection rule may require.
        assert re.search(r"^  security:\n    name: Security audit$", text, re.M), name
        assert "mode: audit" in text, name


def test_the_pythons_a_push_does_not_test_are_tested_somewhere():
    """`requires-python` promises 3.12 and 3.13; `every-python.yml` is where they run (#13)."""
    text = (FOLDER / "every-python.yml").read_text(encoding="utf-8")
    assert re.search(r'python-version: \["3\.12", "3\.13"\]', text)
    assert re.search(r"^\s+- cron: ", text, re.M), "weekly, for a quiet week"
    assert "python-version: ${{ matrix.python-version }}" in text
