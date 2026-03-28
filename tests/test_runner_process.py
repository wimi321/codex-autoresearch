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


def test_run_process_with_logs_captures_stdout_and_stderr(tmp_path: Path) -> None:
    runner = ResearchRunner(tmp_path, make_config())
    run_dir = tmp_path / ".autoresearch" / "runs" / "iteration-0001"
    run_dir.mkdir(parents=True, exist_ok=True)

    result = runner._run_process_with_logs(
        [
            "python3",
            "-c",
            "import sys; print('hello'); print('warn', file=sys.stderr)",
        ],
        cwd=tmp_path,
        stdout_path=run_dir / "codex.stdout.log",
        stderr_path=run_dir / "codex.stderr.log",
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"
    assert result.stderr.strip() == "warn"
    assert (run_dir / "codex.stdout.log").read_text().strip() == "hello"
    assert (run_dir / "codex.stderr.log").read_text().strip() == "warn"
