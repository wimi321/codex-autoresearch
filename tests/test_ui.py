import io
import json
from pathlib import Path

from codex_autoresearch.ui import build_action_command, build_handler, collect_dashboard_state, export_sandbox_patch, load_results_history, load_results_preview, load_run_timeline, normalize_stop_at, preset_payload, read_log_excerpt, render_config_toml, render_ui_html, save_config, simple_goal_payload, simple_mode_preview, simple_run_workspace, validate_simple_goal


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
    assert state["gitInstalled"] is True
    assert state["codexInstalled"] is True
    assert state["suggestion"]["preset"] == "python"
    assert state["simpleStart"]["canStart"] is True
    assert state["simpleStart"]["blocker"] is None
    assert state["config"]["goal"] == "Increase coverage"
    assert state["history"][0]["status"] == "baseline"
    assert state["simplePlan"]["preset"] == "python"
    assert state["simplePlan"]["files"][0] == ".autoresearch/simple-mode.toml"
    assert state["suggestion"]["next_step"] == "autore start --iterations 5"


def test_render_and_save_config(tmp_path: Path) -> None:
    rendered = render_config_toml(
        {
            "goal": "Shrink bundle",
            "metric": "bundle kb",
            "direction": "lower",
            "verify": "npm run build",
            "guard": "npm test",
            "scope": "src/**, app/**",
            "iterations": 8,
            "minDelta": 1.5,
        }
    )

    assert 'goal = "Shrink bundle"' in rendered
    assert 'direction = "lower"' in rendered
    assert 'scope = ["src/**", "app/**"]' in rendered

    path = save_config(tmp_path, "autoresearch.toml", {"goal": "Hello", "metric": "score"})
    assert path.exists()
    assert 'goal = "Hello"' in path.read_text()


def test_render_ui_html_includes_simple_mode_starters() -> None:
    html = render_ui_html()

    assert 'id="simpleStarterTitle"' in html
    assert 'id="simpleReadiness"' in html
    assert 'id="simpleStarterList"' in html
    assert "const starterGoals = {" in html
    assert "function renderSimpleStarters(preset)" in html


def test_render_ui_html_includes_simple_mode_next_steps_guidance() -> None:
    html = render_ui_html()

    assert 'id="simpleFlowTitle"' in html
    assert 'id="simpleFlowStepWorkspace"' in html
    assert 'id="simpleFlowStepFinish"' in html
    assert "simpleFlowWorkspaceSafe" in html
    assert "simpleFlowFinishCurrent" in html
    assert 'document.getElementById("simpleFlowStepWorkspace").textContent = simplePlan.usesSafeCopy' in html


def test_render_ui_html_localizes_runtime_panels_and_re_renders_on_language_switch() -> None:
    html = render_ui_html()

    assert "heroGitReady" in html
    assert "function statusText(status)" in html
    assert "function renderTaskList(tasks)" in html
    assert "function renderSimpleStartState(state)" in html
    assert "renderState(window._state);" in html
    assert "simple_start: copy[lang].simpleStartBtn" in html
    assert 'if (message === "missing_codex") return copy[lang].simpleBlockedCodex;' in html
    assert 'if (message === "missing_git") return copy[lang].simpleBlockedGit;' in html
    assert "taskIterations(task)" in html


def test_render_ui_html_escapes_dynamic_html_content() -> None:
    html = render_ui_html()

    assert "function escapeHtml(value)" in html
    assert "${escapeHtml(config.goal)}" in html
    assert "${escapeHtml(item.summary || \"\")}" in html
    assert "${escapeHtml(task.command.join(\" \"))}" in html
    assert 'data-log="${escapeHtml(item.stderr)}"' in html


def test_render_ui_html_exposes_safe_copy_bring_back_hint() -> None:
    html = render_ui_html()

    assert 'id="simpleActionHint"' in html
    assert 'const actionHint = document.getElementById("simpleActionHint");' in html
    assert 'task.import_status === "ready" && task.patch_path' in html
    assert "actionHint.textContent = copy[lang].simpleApplyReady;" in html


def test_render_ui_html_includes_live_simple_run_preview() -> None:
    html = render_ui_html()

    assert 'id="simplePreviewTitle"' in html
    assert 'id="simplePreviewHeadline"' in html
    assert 'id="simplePreviewRun"' in html
    assert "function renderSimplePreview(state)" in html
    assert 'document.getElementById("simpleGoalInput").addEventListener("input", () => renderSimplePreview(window._state || {}));' in html
    assert 'previewRun.textContent = [' in html


def test_render_ui_html_includes_plain_language_simple_checks_explainer() -> None:
    html = render_ui_html()

    assert 'id="simpleChecksTitle"' in html
    assert 'id="simpleChecksVerifyCode"' in html
    assert 'id="simpleChecksGuardCode"' in html
    assert "function renderSimpleChecks(state)" in html
    assert "renderSimpleChecks(state);" in html


def test_render_ui_html_includes_plain_language_simple_plan_explainer() -> None:
    html = render_ui_html()

    assert 'id="simpleExplainTitle"' in html
    assert 'id="simpleExplainList"' in html
    assert "function simplePresetNarrative(preset)" in html
    assert "function renderSimpleExplainers(state)" in html
    assert "renderSimpleExplainers(state);" in html


def test_render_ui_html_surfaces_simple_mode_scope_guardrails() -> None:
    html = render_ui_html()

    assert 'id="simplePlanScopeLabel"' in html
    assert 'id="simplePlanScope"' in html
    assert "simpleExplainScopePrefix" in html
    assert 'document.getElementById("simplePlanScope").innerHTML = (simplePlan.scope || []).length' in html


def test_render_ui_html_uses_resolved_simple_goal_for_preview_and_start() -> None:
    html = render_ui_html()

    assert "function resolvedSimpleGoal(state = window._state || {})" in html
    assert "const placeholderGoal = goalInput ? String(goalInput.placeholder || \"\").trim() : \"\";" in html
    assert "|| String(simplePlan.defaultGoal || \"\").trim()" in html
    assert "goal: resolvedSimpleGoal()," in html
    assert "const goal = resolvedSimpleGoal(state);" in html


def test_render_ui_html_localizes_already_running_conflict() -> None:
    html = render_ui_html()

    assert "simpleAlreadyRunning" in html
    assert 'if (message === "task_already_running") return copy[lang].simpleAlreadyRunning;' in html


def test_load_results_history_and_run_timeline(tmp_path: Path) -> None:
    results = tmp_path / ".autoresearch" / "results.tsv"
    results.parent.mkdir(parents=True)
    results.write_text(
        "iteration\tcommit\tmetric\tdelta\tguard\tstatus\tsummary\n"
        "0\tbaseline\t12.000000\t0.000000\t-\tbaseline\tstart\n"
        "1\tabc\t11.000000\t1.000000\tpass\tkeep\tbetter\n"
    )
    run_dir = tmp_path / ".autoresearch" / "runs" / "iteration-0001"
    run_dir.mkdir(parents=True)
    (run_dir / "codex.stderr.log").write_text("stderr")

    history = load_results_history(tmp_path, ".autoresearch/results.tsv")
    timeline = load_run_timeline(tmp_path)

    assert len(history) == 2
    assert history[-1]["summary"] == "better"
    assert timeline[-1]["name"] == "iteration-0001"


def test_preset_payload_and_read_log_excerpt(tmp_path: Path) -> None:
    payload = preset_payload("python")
    assert payload["direction"] == "higher"
    assert "pytest" in payload["verify"]

    log_path = tmp_path / ".autoresearch" / "runs" / "iteration-0001" / "codex.stderr.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("a\nb\nc\n")
    excerpt = read_log_excerpt(tmp_path, ".autoresearch/runs/iteration-0001/codex.stderr.log", lines=2)
    assert excerpt["exists"] is True
    assert excerpt["content"] == "b\nc"


def test_simple_goal_payload_uses_detected_preset_and_goal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("codex_autoresearch.ui.suggest_repo_defaults", lambda cwd: {"preset": "python"})

    payload = simple_goal_payload(tmp_path, "Improve login clarity", 7)

    assert payload["goal"] == "Improve login clarity"
    assert payload["iterations"] == 7
    assert payload["direction"] == "higher"
    assert payload["metric"] == "passed tests"
    assert "python -m pytest -q" in payload["verify"]
    assert payload["guard"] == "python -m pytest -q"


def test_simple_goal_payload_falls_back_to_beginner_friendly_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("codex_autoresearch.ui.suggest_repo_defaults", lambda cwd: {"preset": "python"})

    payload = simple_goal_payload(tmp_path, "", 5)

    assert payload["goal"] == "Find the most likely user-facing bug in scope, fix it, and keep the current test suite green."


def test_validate_simple_goal_rejects_blank_goal() -> None:
    try:
        validate_simple_goal("   ")
    except ValueError as exc:
        assert str(exc) == "simple goal is required"
    else:
        raise AssertionError("validate_simple_goal should reject an empty goal")


def test_simple_mode_preview_surfaces_safe_copy_and_expected_files(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, text=True, capture_output=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "notes.txt").write_text("dirty\n")

    preview = simple_mode_preview(tmp_path, "autoresearch.toml")

    assert preview["preset"] == "python"
    assert preview["usesSafeCopy"] is True
    assert "python -m pytest -q" in preview["verify"]
    assert preview["guard"] == "python -m pytest -q"
    assert preview["files"][0] == "autoresearch.toml"
    assert ".gitignore" in preview["files"]
    assert ".autoresearch/runs/" in preview["files"]


def test_simple_mode_preview_includes_allowed_edit_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("codex_autoresearch.ui.suggest_repo_defaults", lambda cwd: {"preset": "python"})

    preview = simple_mode_preview(tmp_path, "autoresearch.toml")

    assert preview["scope"] == ["src/**", "tests/**"]


def test_collect_dashboard_state_reports_simple_start_blocker_when_codex_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / "tests").mkdir()

    def fake_which(name: str) -> str | None:
        if name == "git":
            return "/usr/bin/git"
        return None

    monkeypatch.setattr("codex_autoresearch.ui.shutil.which", fake_which)

    state = collect_dashboard_state(tmp_path, "autoresearch.toml")

    assert state["gitInstalled"] is True
    assert state["codexInstalled"] is False
    assert state["simpleStart"]["canStart"] is False
    assert state["simpleStart"]["blocker"] == "missing_codex"


def test_collect_dashboard_state_reports_simple_start_blocker_when_git_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / "tests").mkdir()

    def fake_which(name: str) -> str | None:
        if name == "codex":
            return "/usr/bin/codex"
        return None

    monkeypatch.setattr("codex_autoresearch.ui.shutil.which", fake_which)

    state = collect_dashboard_state(tmp_path, "autoresearch.toml")

    assert state["gitExists"] is True
    assert state["gitInstalled"] is False
    assert state["codexInstalled"] is True
    assert state["simpleStart"]["canStart"] is False
    assert state["simpleStart"]["blocker"] == "missing_git"


def test_build_handler_rejects_simple_start_while_a_task_is_running(tmp_path: Path) -> None:
    class BusyTaskStore:
        def latest_running_task_id(self) -> str | None:
            return "task-007"

        def start(self, *args, **kwargs):
            raise AssertionError("start should not be called while another task is running")

    handler_class = build_handler(tmp_path, "autoresearch.toml", BusyTaskStore())
    handler = object.__new__(handler_class)
    handler.path = "/api/actions"
    request_body = json.dumps({"action": "simple_start", "configPath": "autoresearch.toml", "goal": "Improve onboarding", "iterations": 3}).encode()
    handler.headers = {"Content-Length": str(len(request_body))}
    handler.rfile = io.BytesIO(request_body)
    handler.wfile = io.BytesIO()
    seen: dict[str, int] = {}
    handler.send_response = lambda status: seen.setdefault("status", status)
    handler.send_header = lambda *args, **kwargs: None
    handler.end_headers = lambda: None
    handler.send_error = lambda *args, **kwargs: None

    handler.do_POST()

    payload = json.loads(handler.wfile.getvalue().decode())
    assert seen["status"] == 409

    assert payload["error"] == "task_already_running"
    assert payload["taskId"] == "task-007"


def test_build_handler_simple_start_uses_default_goal_when_blank(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr("codex_autoresearch.ui.simple_start_blocker", lambda *args, **kwargs: None)
    monkeypatch.setattr("codex_autoresearch.ui.simple_run_workspace", lambda cwd, config_path: (cwd, None))

    class RecordingTaskStore:
        def latest_running_task_id(self) -> str | None:
            return None

        def start(self, label, command, *, cwd=None, original_cwd=None, stop_at=None, initial_output=""):
            return {
                "id": "task-001",
                "label": label,
                "command": command,
                "cwd": str(cwd),
                "status": "running",
                "output": initial_output,
                "started_at": "2026-03-29T00:00:00+00:00",
            }

    handler_class = build_handler(tmp_path, "autoresearch.toml", RecordingTaskStore())
    handler = object.__new__(handler_class)
    handler.path = "/api/actions"
    request_body = json.dumps({"action": "simple_start", "configPath": "autoresearch.toml", "goal": "   ", "iterations": 3}).encode()
    handler.headers = {"Content-Length": str(len(request_body))}
    handler.rfile = io.BytesIO(request_body)
    handler.wfile = io.BytesIO()
    seen: dict[str, int] = {}
    handler.send_response = lambda status: seen.setdefault("status", status)
    handler.send_header = lambda *args, **kwargs: None
    handler.end_headers = lambda: None
    handler.send_error = lambda *args, **kwargs: None

    handler.do_POST()

    payload = json.loads(handler.wfile.getvalue().decode())
    assert seen["status"] == 202
    assert payload["task"]["label"] == "simple_start"
    assert 'goal = "Find the most likely user-facing bug in scope, fix it, and keep the current test suite green."' in (tmp_path / "autoresearch.toml").read_text()


def test_build_handler_simple_start_tracks_original_repo_for_safe_copy_runs(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "tests").mkdir()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setattr("codex_autoresearch.ui.simple_start_blocker", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "codex_autoresearch.ui.simple_run_workspace",
        lambda cwd, config_path: (sandbox, "[autore] using a safe copy\n"),
    )

    seen: dict[str, object] = {}

    class RecordingTaskStore:
        def latest_running_task_id(self) -> str | None:
            return None

        def start(self, label, command, *, cwd=None, original_cwd=None, stop_at=None, initial_output=""):
            seen["label"] = label
            seen["command"] = command
            seen["cwd"] = cwd
            seen["original_cwd"] = original_cwd
            seen["initial_output"] = initial_output
            return {
                "id": "task-001",
                "label": label,
                "command": command,
                "cwd": str(cwd),
                "original_cwd": str(original_cwd) if original_cwd else None,
                "status": "running",
                "output": initial_output,
                "started_at": "2026-03-29T00:00:00+00:00",
            }

    handler_class = build_handler(tmp_path, "autoresearch.toml", RecordingTaskStore())
    handler = object.__new__(handler_class)
    handler.path = "/api/actions"
    request_body = json.dumps({"action": "simple_start", "configPath": "autoresearch.toml", "goal": "Improve onboarding", "iterations": 3}).encode()
    handler.headers = {"Content-Length": str(len(request_body))}
    handler.rfile = io.BytesIO(request_body)
    handler.wfile = io.BytesIO()
    seen_status: dict[str, int] = {}
    handler.send_response = lambda status: seen_status.setdefault("status", status)
    handler.send_header = lambda *args, **kwargs: None
    handler.end_headers = lambda: None
    handler.send_error = lambda *args, **kwargs: None

    handler.do_POST()

    payload = json.loads(handler.wfile.getvalue().decode())
    assert seen_status["status"] == 202
    assert payload["runCwd"] == str(sandbox)
    assert seen["label"] == "simple_start"
    assert seen["cwd"] == sandbox
    assert seen["original_cwd"] == tmp_path
    assert seen["initial_output"] == "[autore] using a safe copy\n"
    assert 'goal = "Improve onboarding"' in (sandbox / "autoresearch.toml").read_text()


def test_build_handler_simple_start_omits_original_repo_when_running_in_place(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr("codex_autoresearch.ui.simple_start_blocker", lambda *args, **kwargs: None)
    monkeypatch.setattr("codex_autoresearch.ui.simple_run_workspace", lambda cwd, config_path: (cwd, None))

    seen: dict[str, object] = {}

    class RecordingTaskStore:
        def latest_running_task_id(self) -> str | None:
            return None

        def start(self, label, command, *, cwd=None, original_cwd=None, stop_at=None, initial_output=""):
            seen["cwd"] = cwd
            seen["original_cwd"] = original_cwd
            return {
                "id": "task-001",
                "label": label,
                "command": command,
                "cwd": str(cwd),
                "original_cwd": str(original_cwd) if original_cwd else None,
                "status": "running",
                "output": initial_output,
                "started_at": "2026-03-29T00:00:00+00:00",
            }

    handler_class = build_handler(tmp_path, "autoresearch.toml", RecordingTaskStore())
    handler = object.__new__(handler_class)
    handler.path = "/api/actions"
    request_body = json.dumps({"action": "simple_start", "configPath": "autoresearch.toml", "goal": "Improve onboarding", "iterations": 3}).encode()
    handler.headers = {"Content-Length": str(len(request_body))}
    handler.rfile = io.BytesIO(request_body)
    handler.wfile = io.BytesIO()
    seen_status: dict[str, int] = {}
    handler.send_response = lambda status: seen_status.setdefault("status", status)
    handler.send_header = lambda *args, **kwargs: None
    handler.end_headers = lambda: None
    handler.send_error = lambda *args, **kwargs: None

    handler.do_POST()

    assert seen_status["status"] == 202
    assert seen["cwd"] == tmp_path
    assert seen["original_cwd"] is None


def test_simple_mode_preview_keeps_main_config_untouched_when_it_exists(tmp_path: Path) -> None:
    (tmp_path / "autoresearch.toml").write_text("[research]\n")

    preview = simple_mode_preview(tmp_path, "autoresearch.toml")

    assert preview["files"][0] == ".autoresearch/simple-mode.toml"


def test_normalize_stop_at_accepts_and_rejects_values() -> None:
    assert normalize_stop_at(None) is None
    assert normalize_stop_at("") is None
    assert normalize_stop_at("2026-03-29T15:30").startswith("2026-03-29T15:30:00")

    try:
        normalize_stop_at("not-a-date")
    except ValueError as exc:
        assert "stopAt" in str(exc)
    else:
        raise AssertionError("normalize_stop_at should reject invalid values")


def test_simple_run_workspace_uses_safe_copy_when_repo_is_dirty(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, text=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")

    run_cwd, intro = simple_run_workspace(tmp_path, "autoresearch.toml")

    assert run_cwd != tmp_path
    assert intro is not None
    assert "safe copy" in intro
    assert (run_cwd / "src" / "app.py").exists()
    assert (run_cwd / ".git").exists()


def test_simple_run_workspace_ignores_starter_config_when_main_config_exists(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, text=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "autore"], cwd=tmp_path, check=True, text=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "autore@example.com"], cwd=tmp_path, check=True, text=True, capture_output=True)
    (tmp_path / "autoresearch.toml").write_text("[research]\n")
    subprocess.run(["git", "add", "autoresearch.toml"], cwd=tmp_path, check=True, text=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add config"], cwd=tmp_path, check=True, text=True, capture_output=True)
    starter = tmp_path / ".autoresearch" / "simple-mode.toml"
    starter.parent.mkdir(parents=True)
    starter.write_text("[research]\n")

    run_cwd, intro = simple_run_workspace(tmp_path, "autoresearch.toml")

    assert run_cwd == tmp_path
    assert intro is None


def test_export_sandbox_patch_writes_patch_into_original_repo(tmp_path: Path) -> None:
    import subprocess

    original = tmp_path / "original"
    sandbox = tmp_path / "sandbox"
    original.mkdir()
    sandbox.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=sandbox, check=True, text=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "autore"], cwd=sandbox, check=True, text=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "autore@example.com"], cwd=sandbox, check=True, text=True, capture_output=True)
    (sandbox / "note.txt").write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=sandbox, check=True, text=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=sandbox, check=True, text=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "autoresearch/test"], cwd=sandbox, check=True, text=True, capture_output=True)
    (sandbox / "note.txt").write_text("changed\n")
    subprocess.run(["git", "add", "-A"], cwd=sandbox, check=True, text=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=sandbox, check=True, text=True, capture_output=True)

    patch_path = export_sandbox_patch(original, sandbox, "task-001")

    assert patch_path is not None
    assert patch_path.endswith(".patch")
    assert (original / ".autoresearch" / "inbox").exists()
    assert "changed" in Path(patch_path).read_text()
