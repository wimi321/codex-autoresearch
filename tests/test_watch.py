from pathlib import Path

from codex_autoresearch.config import ResearchConfig
from codex_autoresearch.runner import ResearchRunner


def make_config() -> ResearchConfig:
    return ResearchConfig(
        goal="demo",
        metric="score",
        direction="higher",
        verify="printf '1\n'",
        scope=["src/**", "tests/**"],
    )


def test_latest_run_dir_returns_newest_iteration(tmp_path: Path) -> None:
    runner = ResearchRunner(tmp_path, make_config())
    (runner.session_dir / "iteration-0001").mkdir(parents=True, exist_ok=True)
    (runner.session_dir / "iteration-0002").mkdir(parents=True, exist_ok=True)

    latest = runner.latest_run_dir()

    assert latest is not None
    assert latest.name == "iteration-0002"


def test_watch_file_reads_tail_when_not_following(tmp_path: Path, capsys) -> None:
    runner = ResearchRunner(tmp_path, make_config())
    path = tmp_path / "log.txt"
    path.write_text("a\nb\nc\n")

    result = runner.watch_file(path, follow=False, interval_seconds=0.01, lines=2)

    assert result == 0
    assert capsys.readouterr().out == "b\nc\n"
