"""Run the shell steps of the composite GitHub Action (``action.yml``) the way a runner would.

GitHub substitutes ``${{ ... }}`` expressions into each step's ``env`` and then runs the script
with ``bash -eo pipefail``, so the tests set the same variables and run the script text the same
way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "highway-merge"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def step(name: str) -> dict[str, str]:
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    return next(s for s in action["runs"]["steps"] if s.get("name") == name)


def run_step(name: str, tmp_path: Path, path: str | None = None, **env: str) -> dict[str, str]:
    """Run one step; return its outputs, step summary, stdout and exit code."""
    outputs, summary = tmp_path / "github_output", tmp_path / "step_summary"
    outputs.write_text("")
    summary.write_text("")
    bin_dir = str(Path(sys.executable).parent)
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step(name)["run"]],
        env={
            **os.environ,
            "PATH": path or f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(outputs),
            "GITHUB_STEP_SUMMARY": str(summary),
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.split("=", 1) for line in outputs.read_text().splitlines() if "=" in line]
    return {
        **dict(lines),
        "summary": summary.read_text(),
        "stdout": proc.stdout + proc.stderr,
        "code": str(proc.returncode),
    }


def gate(tmp_path: Path, out: Path, candidate: Path) -> dict[str, str]:
    return run_step(
        "Gate",
        tmp_path,
        BASELINE=str(EXAMPLE / "baseline.json"),
        CANDIDATE=str(candidate),
        CONFIG=str(EXAMPLE / "gate.yaml"),
        ADAPTER="json",
        ADAPTER_OPTIONS="",
        OUTPUT_DIR=str(out),
    )


def test_gate_step_reports_the_verdict(tmp_path: Path) -> None:
    result = gate(tmp_path, tmp_path / "out", EXAMPLE / "candidate.json")
    assert result["code"] == "0", result["stdout"]
    assert result["exit-code"] == "1"
    assert result["verdict"] == "FAIL"
    assert "FAIL" in result["summary"]


def test_gate_step_never_reports_a_stale_verdict(tmp_path: Path) -> None:
    """A failed compare must not leave the previous run's verdict in the outputs or summary."""
    out = tmp_path / "out"
    assert gate(tmp_path, out, EXAMPLE / "candidate.json")["verdict"] == "FAIL"
    assert (out / "comparison.json").exists()

    result = gate(tmp_path, out, tmp_path / "missing.json")
    assert result["exit-code"] == "2"
    assert "verdict" not in result or result["verdict"] == ""
    assert result["summary"] == ""
    for name in ("comparison.json", "junit.xml", "report.html", "summary.md"):
        assert not (out / name).exists(), name


def fake_gh(tmp_path: Path, script: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\n{script}\n")
    gh.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def comment(tmp_path: Path, path: str, head_repo: str) -> dict[str, str]:
    summary = tmp_path / "summary.md"
    summary.write_text("<!-- physim-gate -->\n### PASS\n")
    return run_step(
        "Comment on the pull request",
        tmp_path,
        path=path,
        GH_TOKEN="token",
        REPOSITORY="owner/repo",
        HEAD_REPOSITORY=head_repo,
        PR_NUMBER="7",
        SUMMARY=str(summary),
    )


def test_comment_step_skips_pull_requests_from_forks(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    path = fake_gh(tmp_path, f'echo "$@" >> {calls}')
    result = comment(tmp_path, path, head_repo="someone/repo")
    assert result["code"] == "0", result["stdout"]
    assert "::notice" in result["stdout"]
    assert "fork" in result["stdout"]
    assert not calls.exists()


def test_comment_step_failure_is_a_warning_not_a_failed_gate(tmp_path: Path) -> None:
    path = fake_gh(tmp_path, 'echo "HTTP 403: Resource not accessible by integration" >&2; exit 1')
    result = comment(tmp_path, path, head_repo="owner/repo")
    assert result["code"] == "0", result["stdout"]
    assert "::warning" in result["stdout"]
    assert "pull-requests: write" in result["stdout"]


def test_comment_step_updates_its_own_comment(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    path = fake_gh(
        tmp_path,
        f'echo "$@" >> {calls}\ncase "$*" in *--paginate*) echo 42 ;; esac',
    )
    result = comment(tmp_path, path, head_repo="owner/repo")
    assert result["code"] == "0", result["stdout"]
    assert "--method PATCH repos/owner/repo/issues/comments/42" in calls.read_text()
