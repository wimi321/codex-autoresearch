from __future__ import annotations

from pathlib import Path
import subprocess


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise GitError(result.stderr.strip() or result.stdout.strip())
    return result


def require_repo_clean(cwd: Path, allowed_paths: set[str] | None = None) -> None:
    result = git(["status", "--porcelain"], cwd)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return
    if allowed_paths:
        leftovers = []
        for line in lines:
            path = line[3:]
            if path not in allowed_paths:
                leftovers.append(line)
        if not leftovers:
            return
        lines = leftovers
    raise GitError("working tree is not clean:\n" + "\n".join(lines))


def short_head(cwd: Path) -> str:
    return git(["rev-parse", "--short", "HEAD"], cwd).stdout.strip()


def branch_exists(cwd: Path, branch: str) -> bool:
    result = git(["show-ref", "--verify", f"refs/heads/{branch}"], cwd, check=False)
    return result.returncode == 0


def checkout_new_branch(cwd: Path, branch: str) -> None:
    git(["checkout", "-b", branch], cwd)


def ensure_gitignore_has(cwd: Path, entries: list[str]) -> None:
    path = cwd / ".gitignore"
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [entry for entry in entries if entry not in existing]
    if not missing:
        return
    lines = existing[:]
    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(missing)
    path.write_text("\n".join(lines) + "\n")


def create_experiment_commit(cwd: Path, message: str, auto_stage_all: bool) -> str:
    if auto_stage_all:
        git(["add", "-A"], cwd)
    status = git(["status", "--porcelain"], cwd).stdout.strip()
    if not status:
        raise GitError("no file changes produced by Codex")
    git(["commit", "-m", message], cwd)
    return short_head(cwd)


def revert_last_commit(cwd: Path) -> None:
    git(["revert", "--no-edit", "HEAD"], cwd)
