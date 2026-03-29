from pathlib import Path

from codex_autoresearch.cli import cmd_doctor, cmd_init, cmd_nightly, cmd_onboard, cmd_quickstart, cmd_run, cmd_start, cmd_start_demo, cmd_status, cmd_watch, render_nightly_workflow, suggest_repo_defaults
from codex_autoresearch.config import ResearchConfig


def write_config(tmp_path: Path, *, iterations: int | None = 3) -> Path:
    iterations_line = f"iterations = {iterations}\n" if iterations is not None else ""
    config_path = tmp_path / "autoresearch.toml"
    config_path.write_text(
        (
            "[research]\n"
            'goal = "Increase coverage"\n'
            'metric = "collected tests"\n'
            'direction = "higher"\n'
            'verify = "pytest --collect-only -q"\n'
            'scope = ["src/**", "tests/**"]\n'
            'guard = "pytest"\n'
            f"{iterations_line}"
            "\n"
            "[runtime]\n"
            'codex_command = "codex exec"\n'
            "auto_stage_all = true\n"
        )
    )
    return config_path


def test_cmd_init_uses_detected_python_preset(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()

    assert cmd_init(force=False, preset="auto") == 0

    assert "pytest --cov=src" in (tmp_path / "autoresearch.toml").read_text()
    assert "using 'python' preset" in capsys.readouterr().out


def test_cmd_run_requires_iterations_when_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = write_config(tmp_path, iterations=None)

    assert cmd_run(
        str(config_path),
        iterations_override=None,
        branch=None,
        skip_branch=False,
        resume=False,
    ) == 1

    assert "Iterations must be set in config or passed with --iterations." in capsys.readouterr().err


def test_cmd_status_prints_existing_results_log(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path)
    log_path = tmp_path / ".autoresearch" / "results.tsv"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("iteration\tcommit\tmetric\n0\tbaseline\t14.0\n")

    assert cmd_status("autoresearch.toml") == 0

    assert "0\tbaseline\t14.0" in capsys.readouterr().out


def test_cmd_doctor_reports_missing_requirements(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("codex_autoresearch.cli.shutil.which", lambda _: None)

    assert cmd_doctor("autoresearch.toml") == 1

    output = capsys.readouterr().out
    assert "git repo missing" in output
    assert "Codex CLI missing" in output
    assert "autoresearch.toml missing" in output


def test_cmd_doctor_fix_creates_missing_git_and_config(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("codex_autoresearch.cli.shutil.which", lambda _: "/usr/bin/codex")

    assert cmd_doctor("autoresearch.toml", fix=True) == 0

    output = capsys.readouterr().out
    assert "applied fixes" in output
    assert (tmp_path / ".git").exists()
    assert (tmp_path / "autoresearch.toml").exists()
    assert ".autoresearch/" in (tmp_path / ".gitignore").read_text()


def test_cmd_doctor_prints_config_summary_when_ready(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr("codex_autoresearch.cli.shutil.which", lambda _: "/usr/bin/codex")

    assert cmd_doctor("autoresearch.toml") == 0

    output = capsys.readouterr().out
    assert "Autoresearch doctor is happy:" in output
    assert "- goal: Increase coverage" in output
    assert "- metric: collected tests (higher is better)" in output
    assert "- verify: pytest --collect-only -q" in output
    assert "- suggested preset: python" in output
    assert "- next step: autore run --iterations 5" in output


def test_cmd_run_prints_research_summary_when_iterations_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = write_config(tmp_path, iterations=None)

    assert cmd_run(
        str(config_path),
        iterations_override=None,
        branch=None,
        skip_branch=True,
        resume=False,
    ) == 1

    assert "Iterations must be set in config or passed with --iterations." in capsys.readouterr().err


def test_cmd_watch_reads_results_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path)
    log_path = tmp_path / ".autoresearch" / "results.tsv"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("iteration\tcommit\tmetric\n0\tbaseline\t14.0\n")

    assert cmd_watch("autoresearch.toml", stream="results", follow=False, interval=0.01, lines=20) == 0

    assert "baseline" in capsys.readouterr().out


def test_cmd_start_creates_missing_config(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr("codex_autoresearch.cli.shutil.which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr("codex_autoresearch.cli.cmd_run", lambda *args, **kwargs: 0)

    assert cmd_start("autoresearch.toml", "auto", 3, False, True, None, False, ".autoresearch-demo", False) == 0

    output = capsys.readouterr().out
    assert "no config found" in output
    assert (tmp_path / "autoresearch.toml").exists()


def test_cmd_start_stops_if_doctor_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("codex_autoresearch.cli.cmd_doctor", lambda config_path, fix=False: 1)
    monkeypatch.setattr("codex_autoresearch.cli.cmd_run", lambda *args, **kwargs: 0)

    assert cmd_start("autoresearch.toml", "generic", 3, False, True, None, False, ".autoresearch-demo", False) == 1


def test_cmd_start_runs_doctor_with_fix_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "autoresearch.toml").write_text("placeholder")

    calls: list[tuple[str, bool]] = []

    def fake_doctor(config_path: str, fix: bool = False) -> int:
        calls.append((config_path, fix))
        return 0

    monkeypatch.setattr("codex_autoresearch.cli.cmd_doctor", fake_doctor)
    monkeypatch.setattr("codex_autoresearch.cli.cmd_run", lambda *args, **kwargs: 0)

    assert cmd_start("autoresearch.toml", "generic", 3, False, True, None, False, ".autoresearch-demo", False) == 0
    assert calls == [("autoresearch.toml", True)]


def test_cmd_start_demo_creates_copyable_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert cmd_start_demo("demo-out", run_demo=False, iterations=1) == 0

    demo = tmp_path / "demo-out"
    assert (demo / "autoresearch.toml").exists()
    assert (demo / "score.txt").read_text().strip() == "10"
    output = capsys.readouterr().out
    assert "demo created at" in output
    assert "autore start --resume --skip-branch" in output


def test_cmd_start_demo_can_run_immediately(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    def fake_run(*args, **kwargs):
        demo = tmp_path / "demo-run"
        scratch = demo / ".autoresearch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "results.tsv").write_text(
            "iteration\tcommit\tmetric\tdelta\tguard\tstatus\tsummary\n"
            "0\tbaseline\t10.000000\t0.000000\t-\tbaseline\tinitial baseline\n"
            "1\tabc1234\t0.000000\t10.000000\t-\tkeep\tdemo success\n"
        )
        (demo / "score.txt").write_text("0\n")
        return 0

    monkeypatch.setattr("codex_autoresearch.cli.cmd_run", fake_run)

    assert cmd_start("autoresearch.toml", "auto", 1, False, True, None, True, "demo-run", True) == 0
    assert (tmp_path / "demo-run" / "autoresearch.toml").exists()


def test_cmd_quickstart_uses_demo_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    answers = iter(["", "", ""])
    monkeypatch.setattr("codex_autoresearch.cli.builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("codex_autoresearch.cli.cmd_start", lambda *args, **kwargs: 0)

    assert cmd_quickstart(".autoresearch-demo") == 0


def test_suggest_repo_defaults_for_python_repo(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    suggestion = suggest_repo_defaults(tmp_path)
    assert suggestion["preset"] == "python"
    assert "pytest" in suggestion["guard_hint"]


def test_suggest_repo_defaults_from_config_when_repo_shape_is_generic(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    suggestion = suggest_repo_defaults(tmp_path, config=ResearchConfig.load(config_path))
    assert suggestion["preset"] == "python"


def test_render_nightly_workflow_includes_guard_and_artifacts(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    config = ResearchConfig.load(config_path)

    workflow = render_nightly_workflow("autoresearch.toml", config, 7, "3.12", "main")

    assert "name: autoresearch-nightly" in workflow
    assert "python-version: '3.12'" in workflow
    assert "autore doctor --config autoresearch.toml --fix" in workflow
    assert "autore run --config autoresearch.toml --resume --iterations 7 --skip-branch" in workflow
    assert "Preflight guard" in workflow
    assert ".autoresearch/results.tsv" in workflow


def test_cmd_nightly_writes_workflow_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path)

    assert cmd_nightly("autoresearch.toml", ".github/workflows/autoresearch-nightly.yml", 5, "3.11", "main", False) == 0

    workflow = (tmp_path / ".github" / "workflows" / "autoresearch-nightly.yml").read_text()
    assert "autoresearch-nightly" in workflow
    assert "upload-artifact@v4" in workflow
    assert "Wrote nightly workflow" in capsys.readouterr().out


def test_cmd_onboard_can_prepare_repo_and_write_nightly(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr("codex_autoresearch.cli.shutil.which", lambda _: "/usr/bin/codex")

    assert cmd_onboard("autoresearch.toml", ".github/workflows/autoresearch-nightly.yml", 4, True, False) == 0

    output = capsys.readouterr().out
    assert "This repo is ready." in output
    assert "Copy next:" in output
    assert (tmp_path / "autoresearch.toml").exists()
    assert (tmp_path / ".gitignore").exists()
    assert (tmp_path / ".github" / "workflows" / "autoresearch-nightly.yml").exists()
