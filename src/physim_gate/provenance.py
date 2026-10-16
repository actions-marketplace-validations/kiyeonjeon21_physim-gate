"""Provenance helpers: environment capture, content hashes and reproducible timestamps."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from physim_gate.schema import Environment, Source


def utc_now() -> datetime:
    """Current UTC time, or ``SOURCE_DATE_EPOCH`` when set, for byte-reproducible output."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None and epoch.strip().isdigit():
        return datetime.fromtimestamp(int(epoch), tz=UTC)
    return datetime.now(tz=UTC).replace(microsecond=0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_path(path: Path) -> str:
    """Content hash of a file, or of a directory tree (relative paths and file contents).

    Adapters may read a directory per run. Its hash covers every regular file under it, in
    sorted order, so renaming or editing any file changes the hash.
    """
    if not path.is_dir():
        return sha256_file(path)
    digest = hashlib.sha256()
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(child.relative_to(path).as_posix().encode("utf-8") + b"\0")
        digest.update(sha256_file(child).encode("ascii") + b"\n")
    return digest.hexdigest()


def capture_environment(**extra: str | int | float | bool | None) -> Environment:
    """Describe the current interpreter and platform. No hostnames or user names are recorded."""
    return Environment(
        python=platform.python_version(),
        platform=platform.platform(terse=True),
        machine=platform.machine(),
        extra=dict(extra),
    )


def capture_git_source(directory: Path | None = None) -> Source:
    """Best-effort git provenance for ``directory``; empty when git or a repository is absent."""

    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    if commit is None:
        return Source()
    status = git("status", "--porcelain", "--untracked-files=no")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    return Source(
        repository=git("config", "--get", "remote.origin.url"),
        commit=commit,
        branch=None if branch == "HEAD" else branch,
        dirty=None if status is None else bool(status),
    )
