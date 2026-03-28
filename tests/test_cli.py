from pathlib import Path

from codex_autoresearch.cli import cmd_doctor, cmd_init, cmd_run, cmd_status


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

    assert cmd_run(str(config_path), iterations_override=None, branch=None, skip_branch=False) == 1

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
