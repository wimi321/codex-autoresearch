from pathlib import Path
import subprocess

from codex_autoresearch.config import ResearchConfig
from codex_autoresearch.gittools import GitError
from codex_autoresearch.runner import ResearchRunner


def make_config() -> ResearchConfig:
    return ResearchConfig(
        goal="demo",
        metric="score",
        direction="higher",
        verify="printf '1\n'",
        scope=["src/**", "tests/**"],
    )


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, text=True, capture_output=True)


def test_ensure_setup_bootstraps_gitignore_and_log_header(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    runner = ResearchRunner(tmp_path, make_config())

    runner.ensure_setup()

    assert (tmp_path / ".gitignore").read_text() == ".autoresearch/\n"
    assert runner.log_path.read_text() == "iteration\tcommit\tmetric\tdelta\tguard\tstatus\tsummary\n"


def test_ensure_setup_rejects_unrelated_dirty_paths(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("todo\n")
    runner = ResearchRunner(tmp_path, make_config())

    try:
        runner.ensure_setup()
    except GitError as exc:
        assert "notes.txt" in str(exc)
    else:
        raise AssertionError("expected ensure_setup to reject unrelated dirty files")
