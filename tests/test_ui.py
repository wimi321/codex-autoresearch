from pathlib import Path

from codex_autoresearch.ui import build_action_command, collect_dashboard_state, load_results_preview


def test_build_action_command_for_start_and_nightly() -> None:
    assert build_action_command("start", {"configPath": "autoresearch.toml", "iterations": 4, "resume": True}) == [
        "start",
        "--config",
        "autoresearch.toml",
        "--iterations",
        "4",
        "--resume",
    ]
    assert build_action_command("nightly", {"configPath": "autoresearch.toml", "iterations": 6}) == [
        "nightly",
        "--config",
        "autoresearch.toml",
        "--iterations",
        "6",
        "--force",
    ]


def test_load_results_preview_reads_recent_rows(tmp_path: Path) -> None:
    log_path = tmp_path / ".autoresearch" / "results.tsv"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "iteration\tcommit\tmetric\tdelta\tguard\tstatus\tsummary\n"
        "0\tbaseline\t10.000000\t0.000000\t-\tbaseline\tinit\n"
        "1\tabc\t9.000000\t1.000000\tpass\tkeep\timproved\n"
    )

    rows = load_results_preview(tmp_path, ".autoresearch/results.tsv")

    assert rows[-1]["iteration"] == "1"
    assert rows[-1]["status"] == "keep"


def test_collect_dashboard_state_reads_config_and_results(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr("codex_autoresearch.ui.shutil.which", lambda _: "/usr/bin/codex")
    (tmp_path / "autoresearch.toml").write_text(
        "[research]\n"
        'goal = "Increase coverage"\n'
        'metric = "coverage percent"\n'
        'direction = "higher"\n'
        'verify = "pytest --collect-only -q"\n'
        'scope = ["src/**", "tests/**"]\n'
        'guard = "pytest"\n'
        "iterations = 5\n"
        "\n"
        "[runtime]\n"
        'codex_command = "codex exec"\n'
        "auto_stage_all = true\n"
    )
    results = tmp_path / ".autoresearch" / "results.tsv"
    results.parent.mkdir(parents=True)
    results.write_text(
        "iteration\tcommit\tmetric\tdelta\tguard\tstatus\tsummary\n"
        "0\tbaseline\t12.000000\t0.000000\t-\tbaseline\tinit\n"
    )

    state = collect_dashboard_state(tmp_path, "autoresearch.toml")

    assert state["repoName"] == tmp_path.name
    assert state["configExists"] is True
    assert state["gitExists"] is True
    assert state["codexInstalled"] is True
    assert state["suggestion"]["preset"] == "python"
    assert state["config"]["goal"] == "Increase coverage"
