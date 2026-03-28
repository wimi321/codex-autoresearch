from pathlib import Path

from codex_autoresearch.config import ResearchConfig
from codex_autoresearch.runner import ResearchRunner


def make_config(command: str) -> ResearchConfig:
    return ResearchConfig(
        goal="demo",
        metric="score",
        direction="lower",
        verify="cat score.txt",
        scope=["score.txt"],
        codex_command=command,
    )


def test_build_codex_command_for_default_exec_form(tmp_path: Path) -> None:
    runner = ResearchRunner(tmp_path, make_config("codex exec"))
    runner.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    assert runner._build_codex_command() == [
        "codex",
        "-a",
        "never",
        "exec",
        "-s",
        "workspace-write",
        str(runner.prompt_path),
    ]


def test_build_codex_command_for_plain_codex_form(tmp_path: Path) -> None:
    runner = ResearchRunner(tmp_path, make_config("codex"))
    runner.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    assert runner._build_codex_command() == [
        "codex",
        "-a",
        "never",
        "exec",
        "-s",
        "workspace-write",
        str(runner.prompt_path),
    ]
