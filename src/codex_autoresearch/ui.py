from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlparse
import webbrowser

from .cli import suggest_repo_defaults
from .config import GENERIC_TEMPLATE, NODE_TEMPLATE, PYTHON_TEMPLATE, ResearchConfig
from .gittools import git


def cmd_ui(config_path: str, host: str, port: int, open_browser: bool) -> int:
    repo_root = Path.cwd()
    task_store = TaskStore(repo_root)
    handler = build_handler(repo_root, config_path, task_store)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"[autore] UI running at {url}")
    print("[autore] press Ctrl+C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[autore] UI stopped")
    finally:
        server.server_close()
    return 0


def collect_dashboard_state(cwd: Path, config_path: str) -> dict[str, Any]:
    config_file = cwd / config_path
    config_exists = config_file.exists()
    config_summary: dict[str, Any] | None = None
    git_installed = shutil.which("git") is not None
    codex_installed = shutil.which("codex") is not None
    suggestion = suggest_repo_defaults(cwd)
    if config_exists:
        config = ResearchConfig.load(config_file)
        suggestion = suggest_repo_defaults(cwd, config=config)
        config_summary = {
            "goal": config.goal,
            "metric": config.metric,
            "direction": config.direction,
            "verify": config.verify,
            "guard": config.guard or "",
            "iterations": config.iterations,
            "min_delta": config.min_delta,
            "scope": config.scope,
            "log_tsv": config.log_tsv,
        }

    results = load_results_preview(cwd, config_summary["log_tsv"] if config_summary else ".autoresearch/results.tsv")
    run_dirs = sorted((cwd / ".autoresearch" / "runs").glob("iteration-*")) if (cwd / ".autoresearch" / "runs").exists() else []
    latest_run = run_dirs[-1].name if run_dirs else None
    return {
        "repoName": cwd.name,
        "cwd": str(cwd),
        "configPath": config_path,
        "configExists": config_exists,
        "gitExists": (cwd / ".git").exists(),
        "gitInstalled": git_installed,
        "codexInstalled": codex_installed,
        "suggestion": suggestion,
        "simplePlan": simple_mode_preview(cwd, config_path, git_installed=git_installed),
        "simpleStart": simple_start_readiness(cwd, config_path, codex_installed=codex_installed, git_installed=git_installed),
        "config": config_summary,
        "results": results,
        "history": load_results_history(cwd, config_summary["log_tsv"] if config_summary else ".autoresearch/results.tsv"),
        "timeline": load_run_timeline(cwd),
        "latestRun": latest_run,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


def load_results_preview(cwd: Path, log_path: str) -> list[dict[str, str]]:
    return load_results_history(cwd, log_path)[-8:]


def load_results_history(cwd: Path, log_path: str) -> list[dict[str, str]]:
    path = cwd / log_path
    if not path.exists():
        return []
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    header = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        if line == lines[0]:
            continue
        values = line.split("\t")
        row = dict(zip(header, values, strict=False))
        rows.append(row)
    return rows


def load_run_timeline(cwd: Path) -> list[dict[str, str]]:
    run_root = cwd / ".autoresearch" / "runs"
    if not run_root.exists():
        return []
    items: list[dict[str, str]] = []
    for run_dir in sorted(path for path in run_root.iterdir() if path.is_dir()):
        stat = run_dir.stat()
        items.append(
            {
                "name": run_dir.name,
                "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "stdout": str((run_dir / "codex.stdout.log").relative_to(cwd)) if (run_dir / "codex.stdout.log").exists() else "",
                "stderr": str((run_dir / "codex.stderr.log").relative_to(cwd)) if (run_dir / "codex.stderr.log").exists() else "",
            }
        )
    return items


def preset_payload(preset: str) -> dict[str, Any]:
    template = {
        "python": PYTHON_TEMPLATE,
        "node": NODE_TEMPLATE,
        "generic": GENERIC_TEMPLATE,
    }.get(preset, GENERIC_TEMPLATE)
    with tempfile.NamedTemporaryFile("w+", suffix=".toml", delete=False) as handle:
        handle.write(template)
        handle.flush()
        temp_path = Path(handle.name)
    try:
        config = ResearchConfig.load(temp_path)
        return {
            "goal": config.goal,
            "metric": config.metric,
            "direction": config.direction,
            "verify": config.verify,
            "guard": config.guard or "",
            "iterations": config.iterations or 5,
            "minDelta": config.min_delta,
            "scope": ", ".join(config.scope),
        }
    finally:
        temp_path.unlink(missing_ok=True)


def read_log_excerpt(cwd: Path, relative_path: str, lines: int = 120) -> dict[str, Any]:
    target = (cwd / relative_path).resolve()
    if cwd.resolve() not in target.parents and target != cwd.resolve():
        raise ValueError("log path must stay inside the repository")
    if not target.exists():
        return {"path": relative_path, "content": "", "exists": False}
    text = target.read_text()
    excerpt = "\n".join(text.splitlines()[-lines:])
    return {"path": relative_path, "content": excerpt, "exists": True}


def render_config_toml(payload: dict[str, Any]) -> str:
    goal = payload.get("goal", "").strip() or "Increase a mechanical metric with Codex"
    metric = payload.get("metric", "").strip() or "example score"
    direction = payload.get("direction", "higher").strip() or "higher"
    verify = payload.get("verify", "").strip() or "./scripts/verify.sh"
    guard = payload.get("guard", "").strip()
    scope_items = [item.strip() for item in str(payload.get("scope", "src/**, tests/**")).split(",") if item.strip()]
    iterations = int(payload.get("iterations", 5) or 5)
    min_delta = float(payload.get("minDelta", 0.0) or 0.0)
    branch_prefix = payload.get("branchPrefix", "autoresearch").strip() or "autoresearch"
    log_tsv = payload.get("logTsv", ".autoresearch/results.tsv").strip() or ".autoresearch/results.tsv"
    scratch_dir = payload.get("scratchDir", ".autoresearch").strip() or ".autoresearch"
    prompt_file = payload.get("promptFile", ".autoresearch/prompt.md").strip() or ".autoresearch/prompt.md"
    codex_command = payload.get("codexCommand", "codex exec").strip() or "codex exec"
    scope_rendered = ", ".join(json.dumps(item) for item in scope_items)
    return (
        "[research]\n"
        f"goal = {json.dumps(goal)}\n"
        f"metric = {json.dumps(metric)}\n"
        f"direction = {json.dumps(direction)}\n"
        f"verify = {json.dumps(verify)}\n"
        f"scope = [{scope_rendered}]\n"
        f"guard = {json.dumps(guard)}\n"
        f"iterations = {iterations}\n"
        f"min_delta = {min_delta}\n\n"
        "[runtime]\n"
        f"codex_command = {json.dumps(codex_command)}\n"
        "auto_stage_all = true\n"
        "codex_timeout_seconds = 1800\n"
        "verify_timeout_seconds = 300\n"
        "guard_timeout_seconds = 300\n\n"
        "[git]\n"
        f"branch_prefix = {json.dumps(branch_prefix)}\n\n"
        "[files]\n"
        f"prompt_file = {json.dumps(prompt_file)}\n"
        f"log_tsv = {json.dumps(log_tsv)}\n"
        f"scratch_dir = {json.dumps(scratch_dir)}\n"
    )


def save_config(cwd: Path, config_path: str, payload: dict[str, Any]) -> Path:
    target = cwd / config_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_config_toml(payload))
    return target


def default_simple_goal_for_preset(preset: str) -> str:
    return {
        "python": "Find the most likely user-facing bug in scope, fix it, and keep the current test suite green.",
        "node": "Make the main user flow easier to understand, simplify the wording, and preserve the current behavior.",
        "generic": "Find the highest-impact issue inside the allowed scope, fix it cleanly, and verify the project still works.",
    }.get(preset, "Find the highest-impact issue inside the allowed scope, fix it cleanly, and verify the project still works.")


def default_simple_scope_for_preset(preset: str) -> str:
    return {
        "python": "src/**, tests/**, docs/**, README.md, CHANGELOG.md",
        "node": "src/**, app/**, tests/**, docs/**, README.md, CHANGELOG.md",
        "generic": "src/**, app/**, docs/**, README.md, CHANGELOG.md",
    }.get(preset, "src/**, app/**, docs/**, README.md, CHANGELOG.md")


def simple_goal_payload(cwd: Path, goal: str, iterations: int) -> dict[str, Any]:
    preset = suggest_repo_defaults(cwd)["preset"]
    values = preset_payload(preset)
    values["goal"] = goal.strip() or default_simple_goal_for_preset(preset)
    values["iterations"] = iterations
    values["scope"] = default_simple_scope_for_preset(preset)
    # Simple mode favors responsiveness over maximum reasoning depth.
    values["codexCommand"] = 'codex exec -c model_reasoning_effort="medium"'
    if preset == "python":
        values["metric"] = "passed tests"
        values["direction"] = "higher"
        values["verify"] = (
            "python -m pytest -q 2>&1 | "
            "python -c \"import re,sys; text=sys.stdin.read(); "
            "match=re.search(r'(\\\\d+) passed', text); "
            "print(match.group(1) if match else '0'); "
            "raise SystemExit(0 if match else 1)\""
        )
        values["guard"] = "python -m pytest -q"
    return values


def simple_mode_config_path(cwd: Path, config_path: str) -> str:
    if config_path == ".autoresearch/simple-mode.toml":
        return config_path
    if not (cwd / config_path).exists():
        return config_path
    return ".autoresearch/simple-mode.toml"


def simple_mode_allowed_paths(cwd: Path, config_path: str) -> set[str]:
    return {simple_mode_config_path(cwd, config_path), ".gitignore", ".autoresearch/"}


def validate_simple_goal(goal: Any) -> str:
    text = str(goal or "").strip()
    if not text:
        raise ValueError("simple goal is required")
    return text


def simple_mode_preview(cwd: Path, config_path: str, *, git_installed: bool | None = None) -> dict[str, Any]:
    payload = simple_goal_payload(cwd, "", 5)
    scope = [item.strip() for item in str(payload.get("scope", "")).split(",") if item.strip()]
    gitignore = cwd / ".gitignore"
    simple_config_path = simple_mode_config_path(cwd, config_path)
    tracked_files = [simple_config_path]
    existing_gitignore = gitignore.read_text().splitlines() if gitignore.exists() else []
    if ".autoresearch/" not in existing_gitignore:
        tracked_files.append(".gitignore")
    tracked_files.extend(
        [
            ".autoresearch/prompt.md",
            ".autoresearch/results.tsv",
            ".autoresearch/runs/",
        ]
    )
    return {
        "preset": suggest_repo_defaults(cwd)["preset"],
        "defaultGoal": payload["goal"],
        "verify": payload["verify"],
        "guard": payload["guard"] or "",
        "scope": scope,
        "usesSafeCopy": bool(dirty_worktree_lines(cwd, allowed_paths=simple_mode_allowed_paths(cwd, config_path), git_installed=git_installed)),
        "files": tracked_files,
    }


def simple_start_blocker(
    cwd: Path,
    config_path: str,
    *,
    codex_installed: bool | None = None,
    git_installed: bool | None = None,
) -> str | None:
    del cwd, config_path
    if codex_installed is None:
        codex_installed = shutil.which("codex") is not None
    if git_installed is None:
        git_installed = shutil.which("git") is not None
    if not codex_installed:
        return "missing_codex"
    if not git_installed:
        return "missing_git"
    return None


def simple_start_readiness(
    cwd: Path,
    config_path: str,
    *,
    codex_installed: bool | None = None,
    git_installed: bool | None = None,
) -> dict[str, Any]:
    blocker = simple_start_blocker(cwd, config_path, codex_installed=codex_installed, git_installed=git_installed)
    return {
        "canStart": blocker is None,
        "blocker": blocker,
    }


def dirty_worktree_lines(cwd: Path, allowed_paths: set[str] | None = None, *, git_installed: bool | None = None) -> list[str]:
    if not (cwd / ".git").exists():
        return []
    if git_installed is None:
        git_installed = shutil.which("git") is not None
    if not git_installed:
        return []
    result = git(["status", "--porcelain"], cwd, check=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not allowed_paths:
        return lines
    allowed_directories = [path for path in allowed_paths if path.endswith("/")]
    blocked: list[str] = []
    for line in lines:
        path = line[3:]
        if path in allowed_paths:
            continue
        if any(path.startswith(prefix) for prefix in allowed_directories):
            continue
        blocked.append(line)
    return blocked


def ensure_git_identity(cwd: Path) -> None:
    if not git(["config", "user.name"], cwd, check=False).stdout.strip():
        git(["config", "user.name", "autore"], cwd)
    if not git(["config", "user.email"], cwd, check=False).stdout.strip():
        git(["config", "user.email", "autore@example.com"], cwd)


def simple_run_workspace(cwd: Path, config_path: str) -> tuple[Path, str | None]:
    blocked = dirty_worktree_lines(cwd, allowed_paths=simple_mode_allowed_paths(cwd, config_path))
    if not blocked:
        return cwd, None

    sandbox_root = Path(tempfile.mkdtemp(prefix="autore-ui-run-"))
    sandbox = sandbox_root / cwd.name
    ignore = shutil.ignore_patterns(
        ".git",
        ".venv",
        ".pytest_cache",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
    )
    shutil.copytree(cwd, sandbox, ignore=ignore)
    git(["init", "-b", "main"], sandbox)
    ensure_git_identity(sandbox)
    git(["add", "-A"], sandbox)
    git(["commit", "-m", "chore: ui sandbox snapshot"], sandbox)
    return sandbox, f"[autore] current repo has uncommitted changes, so this run is using a safe copy:\n{sandbox}\n"


def export_sandbox_patch(original_cwd: Path, sandbox_cwd: Path, task_id: str) -> str | None:
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], sandbox_cwd, check=False).stdout.strip()
    if not branch or branch == "HEAD":
        return None
    diff = git(["diff", "main...HEAD"], sandbox_cwd, check=False).stdout
    if not diff.strip():
        return None
    inbox = original_cwd / ".autoresearch" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    safe_branch = branch.replace("/", "-")
    patch_path = inbox / f"{task_id}-{safe_branch}.patch"
    patch_path.write_text(diff)
    return str(patch_path)


def _git_text(cwd: Path, revspec: str, path: str) -> str | None:
    result = git(["show", f"{revspec}:{path}"], cwd, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def bring_back_sandbox_changes(original_cwd: Path, sandbox_cwd: Path) -> tuple[bool, str]:
    status_output = git(["diff", "--name-status", "main...HEAD"], sandbox_cwd, check=False).stdout
    lines = [line for line in status_output.splitlines() if line.strip()]
    if not lines:
        return False, "no changes to bring back"

    conflicts: list[str] = []
    planned: list[tuple[str, str]] = []
    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        change, rel_path = parts
        rel_path = rel_path.strip()
        baseline = _git_text(sandbox_cwd, "main", rel_path)
        result_text = _git_text(sandbox_cwd, "HEAD", rel_path)
        target = original_cwd / rel_path
        current = target.read_text() if target.exists() else None

        if change.startswith("A"):
            if current is not None:
                conflicts.append(rel_path)
                continue
            planned.append((change, rel_path))
            continue

        if change.startswith("D"):
            if current != baseline:
                conflicts.append(rel_path)
                continue
            planned.append((change, rel_path))
            continue

        if current != baseline:
            conflicts.append(rel_path)
            continue
        if result_text is None:
            conflicts.append(rel_path)
            continue
        planned.append((change, rel_path))

    if conflicts:
        preview = "\n".join(conflicts[:10])
        return False, f"bring-back found conflicting files:\n{preview}"

    for change, rel_path in planned:
        target = original_cwd / rel_path
        if change.startswith("D"):
            target.unlink(missing_ok=True)
            continue
        result_text = _git_text(sandbox_cwd, "HEAD", rel_path)
        if result_text is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result_text)

    return True, f"applied {len(planned)} file changes"


def normalize_stop_at(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid stopAt datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat()


def active_task_conflict(task_store: TaskStore) -> dict[str, str] | None:
    task_id = task_store.latest_running_task_id()
    if not task_id:
        return None
    return {"error": "task_already_running", "taskId": task_id}


def build_action_command(action: str, payload: dict[str, Any]) -> list[str]:
    config_path = payload.get("configPath", "autoresearch.toml")
    iterations = str(int(payload.get("iterations", 5)))
    if action == "doctor_fix":
        return ["doctor", "--config", config_path, "--fix"]
    if action == "onboard":
        argv = ["onboard", "--config", config_path, "--iterations", iterations]
        if payload.get("writeNightly", True):
            argv.append("--write-nightly")
        if payload.get("force", True):
            argv.append("--force")
        return argv
    if action == "nightly":
        argv = ["nightly", "--config", config_path, "--iterations", iterations, "--force"]
        workflow_path = payload.get("workflowPath")
        if workflow_path:
            argv.extend(["--workflow-path", workflow_path])
        return argv
    if action == "start":
        argv = ["start", "--config", config_path, "--iterations", iterations]
        if payload.get("resume"):
            argv.append("--resume")
        return argv
    if action == "demo":
        return ["start", "--demo", "--run", "--iterations", iterations]
    raise ValueError(f"unknown action: {action}")


@dataclass(slots=True)
class Task:
    id: str
    label: str
    command: list[str]
    cwd: str
    status: str
    output: str
    started_at: str
    original_cwd: str | None = None
    stop_at: str | None = None
    patch_path: str | None = None
    import_status: str | None = None
    ended_at: str | None = None


class TaskStore:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._next_id = 1

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(task) for task in sorted(self._tasks.values(), key=lambda item: item.id, reverse=True)]

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return asdict(task) if task else None

    def mark_import_status(self, task_id: str, status: str, note: str | None = None) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.import_status = status
            if note:
                task.output = (task.output + "\n" + note).strip()[-50000:]

    def snapshot_json(self) -> str:
        return json.dumps({"tasks": self.list()}, sort_keys=True)

    def start(
        self,
        label: str,
        command: list[str],
        *,
        cwd: Path | None = None,
        original_cwd: Path | None = None,
        stop_at: str | None = None,
        initial_output: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            task_id = f"task-{self._next_id:03d}"
            self._next_id += 1
            task_cwd = str((cwd or self.cwd).resolve())
            task = Task(
                id=task_id,
                label=label,
                command=command,
                cwd=task_cwd,
                original_cwd=str(original_cwd.resolve()) if original_cwd else None,
                status="running",
                output=initial_output,
                started_at=datetime.now(timezone.utc).isoformat(),
                stop_at=stop_at,
            )
            self._tasks[task_id] = task
        thread = threading.Thread(target=self._run_task, args=(task_id,), daemon=True)
        thread.start()
        if stop_at:
            timer = threading.Thread(target=self._stop_at_deadline, args=(task_id, stop_at), daemon=True)
            timer.start()
        return asdict(task)

    def stop(self, task_id: str) -> bool:
        with self._lock:
            process = self._processes.get(task_id)
            task = self._tasks.get(task_id)
            if task is None or task.status != "running":
                return False
            task.status = "stopping"
        if process is not None and process.poll() is None:
            process.terminate()
        return True

    def latest_running_task_id(self) -> str | None:
        with self._lock:
            for task in sorted(self._tasks.values(), key=lambda item: item.id, reverse=True):
                if task.status in {"running", "stopping"}:
                    return task.id
        return None

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            command = list(task.command)
            cwd = Path(task.cwd)

        env = os.environ.copy()
        src_path = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "codex_autoresearch.cli", *command],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with self._lock:
            self._processes[task_id] = process
        assert process.stdout is not None
        chunks: list[str] = []
        for line in process.stdout:
            chunks.append(line)
            with self._lock:
                self._tasks[task_id].output = "".join(chunks)[-50000:]
        returncode = process.wait()
        patch_path: str | None = None
        import_status: str | None = None
        output_suffix = ""
        original_cwd = Path(task.original_cwd) if task.original_cwd else None
        if returncode == 0 and original_cwd is not None and original_cwd.resolve() != cwd.resolve():
            patch_path = export_sandbox_patch(original_cwd, cwd, task_id)
            if patch_path:
                import_status = "ready"
                output_suffix = f"\n[autore] best sandbox changes were saved for bring-back:\n{patch_path}\n"
        with self._lock:
            prior_status = self._tasks[task_id].status
            self._tasks[task_id].status = "stopped" if prior_status == "stopping" else ("done" if returncode == 0 else "failed")
            self._tasks[task_id].ended_at = datetime.now(timezone.utc).isoformat()
            self._tasks[task_id].patch_path = patch_path
            self._tasks[task_id].import_status = import_status
            self._tasks[task_id].output = ("".join(chunks) + output_suffix)[-50000:]
            self._processes.pop(task_id, None)

    def _stop_at_deadline(self, task_id: str, stop_at: str) -> None:
        try:
            deadline = datetime.fromisoformat(stop_at)
        except ValueError:
            return
        while True:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or task.status not in {"running", "stopping"}:
                    return
            if datetime.now(deadline.tzinfo or timezone.utc) >= deadline:
                self.stop(task_id)
                return
            time.sleep(1.0)


def build_handler(repo_root: Path, config_path: str, task_store: TaskStore) -> type[BaseHTTPRequestHandler]:
    class UIHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(render_ui_html())
                return
            if parsed.path == "/api/state":
                self._send_json(collect_dashboard_state(repo_root, config_path))
                return
            if parsed.path == "/api/tasks":
                self._send_json({"tasks": task_store.list()})
                return
            if parsed.path == "/api/tasks/stream":
                self._stream_tasks()
                return
            if parsed.path.startswith("/api/preset/"):
                preset = parsed.path.rsplit("/", 1)[-1]
                self._send_json({"preset": preset, "values": preset_payload(preset)})
                return
            if parsed.path == "/api/log":
                query = parse_qs(parsed.query)
                path = query.get("path", [""])[0]
                self._send_json(read_log_excerpt(repo_root, path))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/config":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                config_target = payload.get("configPath", config_path)
                save_config(repo_root, config_target, payload)
                self._send_json({"saved": True, "configPath": config_target}, status=HTTPStatus.CREATED)
                return
            if parsed.path != "/api/actions":
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            action = payload.get("action", "")
            if action == "simple_start":
                conflict = active_task_conflict(task_store)
                if conflict is not None:
                    self._send_json(conflict, status=HTTPStatus.CONFLICT)
                    return
                try:
                    requested_config_path = payload.get("configPath", config_path)
                    blocker = simple_start_blocker(repo_root, requested_config_path)
                    if blocker:
                        raise ValueError(blocker)
                    config_target = simple_mode_config_path(repo_root, requested_config_path)
                    iterations = max(1, int(payload.get("iterations", 5) or 5))
                    raw_goal = str(payload.get("goal", "") or "").strip()
                    goal = validate_simple_goal(raw_goal or simple_goal_payload(repo_root, "", iterations)["goal"])
                    stop_at = normalize_stop_at(payload.get("stopAt"))
                    run_cwd, intro = simple_run_workspace(repo_root, config_target)
                    save_config(run_cwd, config_target, simple_goal_payload(run_cwd, goal, iterations))
                    command = build_action_command(
                        "start",
                        {
                            "configPath": config_target,
                            "iterations": iterations,
                            "resume": bool(payload.get("resume", False)),
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                original_cwd = repo_root if run_cwd.resolve() != repo_root.resolve() else None
                task = task_store.start(
                    "simple_start",
                    command,
                    cwd=run_cwd,
                    original_cwd=original_cwd,
                    stop_at=stop_at,
                    initial_output=intro or "",
                )
                self._send_json({"task": task, "configPath": config_target, "runCwd": str(run_cwd)}, status=HTTPStatus.ACCEPTED)
                return
            if action == "stop":
                task_id = payload.get("taskId") or task_store.latest_running_task_id()
                if not task_id:
                    self._send_json({"stopped": False, "reason": "no running task"}, status=HTTPStatus.OK)
                    return
                stopped = task_store.stop(task_id)
                self._send_json({"stopped": stopped, "taskId": task_id}, status=HTTPStatus.OK)
                return
            if action == "apply_best":
                task_id = payload.get("taskId", "")
                task = task_store.get(task_id)
                if not task or not task.get("patch_path") or not task.get("original_cwd"):
                    self._send_json({"error": "no saved result to bring back for this task"}, status=HTTPStatus.BAD_REQUEST)
                    return
                patch_path = str(task["patch_path"])
                sandbox_cwd = Path(str(task["cwd"]))
                original_cwd = Path(str(task["original_cwd"]))
                applied, message = bring_back_sandbox_changes(original_cwd, sandbox_cwd)
                if not applied:
                    task_store.mark_import_status(task_id, "failed", f"[autore] bring-back failed:\n{message}")
                    self._send_json({"applied": False, "error": message, "patchPath": patch_path}, status=HTTPStatus.CONFLICT)
                    return
                task_store.mark_import_status(task_id, "applied", f"[autore] best sandbox changes were applied to:\n{original_cwd}")
                self._send_json({"applied": True, "taskId": task_id, "patchPath": patch_path}, status=HTTPStatus.OK)
                return
            conflict = active_task_conflict(task_store)
            if conflict is not None:
                self._send_json(conflict, status=HTTPStatus.CONFLICT)
                return
            try:
                command = build_action_command(action, payload)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            task = task_store.start(action, command)
            self._send_json({"task": task}, status=HTTPStatus.ACCEPTED)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _send_html(self, body: str) -> None:
            data = body.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _stream_tasks(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_payload = ""
            try:
                while True:
                    payload = task_store.snapshot_json()
                    if payload != last_payload:
                        self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
                        last_payload = payload
                    time.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError):
                return

    return UIHandler


def render_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex Autoresearch UI</title>
  <style>
    :root {
      --bg: #f5f5f7;
      --panel: rgba(255, 255, 255, 0.94);
      --panel-strong: rgba(255, 255, 255, 0.98);
      --ink: #1d1d1f;
      --muted: #6e6e73;
      --line: rgba(29, 29, 31, 0.08);
      --accent: #0071e3;
      --accent-2: #0071e3;
      --gold: #8e8e93;
      --shadow: 0 10px 30px rgba(0, 0, 0, 0.06);
      --radius: 24px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Helvetica Neue", sans-serif;
      background: linear-gradient(180deg, #fbfbfd 0%, #f5f5f7 100%);
      min-height: 100vh;
    }
    .skip-link {
      position: absolute;
      left: 12px;
      top: -48px;
      background: var(--ink);
      color: white;
      padding: 10px 14px;
      border-radius: 12px;
      z-index: 5;
    }
    .skip-link:focus {
      top: 12px;
    }
    .shell {
      width: min(960px, calc(100vw - 32px));
      margin: 28px auto 56px;
      position: relative;
      z-index: 1;
    }
    .hero, .panel {
      background: var(--panel);
      backdrop-filter: blur(18px);
      border: 1px solid rgba(255,255,255,0.9);
      box-shadow: var(--shadow);
      border-radius: var(--radius);
    }
    .hero {
      padding: 32px;
      animation: rise 0.55s ease;
    }
    .eyebrow {
      display: inline-flex;
      gap: 10px;
      align-items: center;
      padding: 8px 14px;
      border-radius: 999px;
      background: #eef2f7;
      font-size: 12px;
      letter-spacing: 0.01em;
      color: var(--accent-2);
    }
    h1, h2 {
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
      margin: 0;
      letter-spacing: -0.04em;
      text-wrap: balance;
    }
    h1 {
      font-size: clamp(34px, 5vw, 54px);
      line-height: 1.02;
      margin-top: 20px;
      max-width: 12ch;
    }
    .hero-grid, .grid {
      display: grid;
      gap: 18px;
    }
    .hero-grid {
      grid-template-columns: 1.4fr 0.9fr;
      align-items: end;
      margin-top: 18px;
    }
    .summary {
      color: var(--muted);
      font-size: 18px;
      line-height: 1.5;
      max-width: 40ch;
    }
    .lang-toggle {
      display: inline-flex;
      gap: 8px;
      padding: 6px;
      background: #eef2f7;
      border-radius: 999px;
      border: 1px solid rgba(29,29,31,0.06);
    }
    .top-controls {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .mode-toggle {
      display: inline-flex;
      gap: 8px;
      padding: 6px;
      background: #eef2f7;
      border-radius: 999px;
      border: 1px solid rgba(29,29,31,0.06);
    }
    .lang-toggle button, .action, .ghost {
      border: 0;
      cursor: pointer;
      transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
    }
    .mode-toggle button {
      padding: 9px 14px;
      border-radius: 999px;
      background: transparent;
      color: var(--muted);
      font-weight: 600;
    }
    .mode-toggle button.active {
      background: white;
      color: var(--ink);
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
    }
    button:focus-visible, input:focus-visible {
      outline: 3px solid rgba(24, 101, 107, 0.42);
      outline-offset: 2px;
    }
    .lang-toggle button {
      padding: 9px 14px;
      border-radius: 999px;
      background: transparent;
      color: var(--muted);
      font-weight: 600;
    }
    .lang-toggle button.active {
      background: white;
      color: var(--ink);
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .stat {
      padding: 16px;
      border-radius: 20px;
      background: rgba(255,255,255,0.75);
      border: 1px solid var(--line);
    }
    .stat label {
      display: block;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 10px;
    }
    .stat strong {
      font-size: 26px;
      display: block;
      line-height: 1.1;
      font-variant-numeric: tabular-nums;
    }
    .grid {
      grid-template-columns: repeat(12, minmax(0, 1fr));
      margin-top: 20px;
    }
    .panel {
      padding: 24px;
      min-height: 100px;
      animation: rise 0.5s ease both;
    }
    .span-7 { grid-column: span 7; }
    .span-5 { grid-column: span 5; }
    .span-6 { grid-column: span 6; }
    .span-4 { grid-column: span 4; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .panel h2 {
      font-size: 28px;
      margin-bottom: 14px;
    }
    .guide-grid {
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 14px;
    }
    .simple-shell {
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
      align-items: stretch;
    }
    .guide-card {
      padding: 22px;
      border-radius: 22px;
      background: rgba(255,255,255,0.96);
      border: 1px solid var(--line);
    }
    .step-list {
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }
    .step-item {
      display: grid;
      grid-template-columns: 34px 1fr;
      gap: 12px;
      align-items: start;
    }
    .step-num {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      background: var(--ink);
      color: white;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
    }
    .chips {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 16px;
    }
    .chip {
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255,255,255,0.85);
      border: 1px solid var(--line);
      font-size: 13px;
      color: var(--muted);
    }
    .actions {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .action, .ghost {
      border-radius: 999px;
      padding: 13px 18px;
      font-size: 15px;
      font-weight: 600;
      text-align: center;
      box-shadow: none;
      touch-action: manipulation;
    }
    .action {
      background: var(--accent);
      color: white;
    }
    .ghost {
      background: #f5f5f7;
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .action:hover, .ghost:hover, .lang-toggle button:hover {
      transform: translateY(-1px);
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 16px;
    }
    .starter-shell {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }
    .starter-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .starter-btn {
      border: 1px solid rgba(29,29,31,0.08);
      border-radius: 18px;
      padding: 14px;
      background: #f5f5f7;
      color: var(--ink);
      text-align: left;
      display: grid;
      gap: 8px;
      min-height: 124px;
      box-shadow: none;
    }
    .starter-btn strong {
      font-size: 14px;
      line-height: 1.3;
    }
    .starter-btn span {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    label.field {
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 14px;
    }
    input {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.88);
      border-radius: 16px;
      padding: 13px 14px;
      font-size: 15px;
      color: var(--ink);
    }
    textarea, select {
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.88);
      border-radius: 16px;
      padding: 13px 14px;
      font-size: 15px;
      color: var(--ink);
      width: 100%;
    }
    textarea {
      min-height: 88px;
      resize: vertical;
      font-family: inherit;
    }
    .config-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .preset-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }
    .config-grid .wide {
      grid-column: 1 / -1;
    }
    .toolbar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
      align-items: center;
    }
    .inline-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 16px;
      align-items: center;
    }
    .goal-box {
      min-height: 124px;
      font-size: 17px;
      line-height: 1.45;
    }
    .helper-note {
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 18px;
      background: #f5f5f7;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }
    .simple-secondary {
      display: none;
    }
    .preview-lead {
      margin-top: 10px;
      color: var(--ink);
      font-size: 15px;
      line-height: 1.7;
    }
    .preview-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }
    .preview-card {
      padding: 14px 16px;
      border-radius: 18px;
      background: #f5f5f7;
      border: 1px solid var(--line);
      min-height: 100%;
    }
    .plan-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 14px;
    }
    .plan-card {
      padding: 14px 16px;
      border-radius: 18px;
      background: #f5f5f7;
      border: 1px solid var(--line);
      min-height: 100%;
    }
    .plan-card.wide {
      grid-column: 1 / -1;
    }
    .plan-label {
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .plan-value {
      color: var(--ink);
      line-height: 1.6;
      word-break: break-word;
      white-space: pre-wrap;
    }
    .plan-code-list {
      display: grid;
      gap: 8px;
    }
    .plan-code {
      display: block;
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(0,113,227,0.08);
      color: var(--ink);
      font-family: "SFMono-Regular", "Menlo", monospace;
      font-size: 13px;
    }
    .progress-stack {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .progress-card {
      padding: 16px 18px;
      border-radius: 20px;
      background: #f5f5f7;
      border: 1px solid var(--line);
    }
    .status-note {
      color: var(--accent-2);
      font-size: 13px;
      font-weight: 700;
    }
    .chart-shell {
      background: rgba(255,255,255,0.72);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 14px;
    }
    .chart {
      width: 100%;
      height: 220px;
      display: block;
    }
    .empty {
      padding: 18px;
      border-radius: 18px;
      background: rgba(255,255,255,0.7);
      border: 1px dashed var(--line);
    }
    .timeline {
      display: grid;
      gap: 12px;
    }
    .timeline-item {
      position: relative;
      padding: 14px 16px 14px 22px;
      border-radius: 18px;
      background: rgba(255,255,255,0.84);
      border: 1px solid var(--line);
    }
    .timeline-item::before {
      content: "";
      position: absolute;
      left: 10px;
      top: 18px;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent);
    }
    .linkish {
      border: 0;
      background: transparent;
      color: var(--accent-2);
      font-weight: 700;
      padding: 0;
      cursor: pointer;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 12px 10px;
      text-align: left;
      vertical-align: top;
      font-variant-numeric: tabular-nums;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .terminal {
      background: #1c1c1e;
      color: #f5f5f7;
      border-radius: 22px;
      padding: 16px;
      min-height: 220px;
      white-space: pre-wrap;
      overflow: auto;
      font-family: "SFMono-Regular", "Menlo", monospace;
      font-size: 13px;
      line-height: 1.6;
    }
    .task-list {
      display: grid;
      gap: 10px;
      margin-bottom: 12px;
      max-height: 180px;
      overflow: auto;
    }
    .task {
      padding: 12px 14px;
      border-radius: 18px;
      background: rgba(255,255,255,0.86);
      border: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 12px;
      cursor: pointer;
      width: 100%;
      text-align: left;
    }
    .task.active { outline: 2px solid rgba(24,101,107,0.35); }
    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .pill.ok { background: rgba(24,101,107,0.12); color: var(--accent-2); }
    .pill.bad { background: rgba(213,92,63,0.14); color: var(--accent); }
    .pill.warn { background: rgba(197,139,42,0.16); color: #8d5d00; }
    .small {
      font-size: 13px;
      color: var(--muted);
      line-height: 1.6;
    }
    .hidden-by-mode {
      display: none !important;
    }
    @keyframes rise {
      from { opacity: 0; transform: translateY(12px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @media (max-width: 920px) {
      .hero-grid, .grid { grid-template-columns: 1fr; }
      .span-7, .span-5, .span-6, .span-4, .span-8, .span-12 { grid-column: span 1; }
      .actions, .form-grid, .stats, .config-grid, .guide-grid, .simple-shell, .plan-grid, .preview-grid, .starter-grid, .progress-stack { grid-template-columns: 1fr; }
      h1 { max-width: none; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation: none !important;
        transition: none !important;
        scroll-behavior: auto !important;
      }
    }
  </style>
</head>
<body>
  <a href="#main" class="skip-link">Skip to main content</a>
  <main class="shell" id="main">
    <section class="hero">
      <div class="eyebrow" id="eyebrow">Codex Autoresearch UI</div>
      <div class="hero-grid">
        <div>
          <div class="top-controls">
            <div class="lang-toggle" aria-label="Language switch">
              <button class="active" data-lang="en">EN</button>
              <button data-lang="zh">中文</button>
            </div>
            <div class="mode-toggle" aria-label="Mode switch">
              <button class="active" data-mode="beginner" id="modeBeginnerBtn">Simple</button>
              <button data-mode="advanced" id="modeAdvancedBtn">Advanced</button>
            </div>
          </div>
          <h1 id="title">Run your repo like a measured Codex lab.</h1>
          <p class="summary" id="summary">A local control room for setup, nightly workflows, and bounded Codex runs. No YAML hunting. No command memorizing.</p>
          <div class="chips" id="heroChips"></div>
        </div>
        <div class="stats advanced-only">
          <div class="stat"><label id="repoLabel">Repository</label><strong id="repoName">-</strong></div>
          <div class="stat"><label id="presetLabel">Preset</label><strong id="presetName">-</strong></div>
          <div class="stat"><label id="configLabel">Config</label><strong id="configStatus">-</strong></div>
          <div class="stat"><label id="runLabel">Latest Run</label><strong id="runName">-</strong></div>
        </div>
      </div>
    </section>
    <section class="grid">
      <div class="panel span-12">
        <h2 id="simpleTitle">Tell it what you want, then press start</h2>
        <div class="simple-shell">
          <div class="guide-card">
            <div class="small" id="simpleIntro">You only need to write what you want done, choose how long to let it run, and press start.</div>
            <label class="field" style="margin-top:14px;">
              <span id="simpleGoalLabel">What do you want to make happen?</span>
              <textarea id="simpleGoalInput" class="goal-box" placeholder="Example: Fix the homepage loading bug, make the copy simpler, and improve the signup flow."></textarea>
            </label>
            <div class="form-grid" style="margin-top:14px;">
              <label class="field"><span id="simpleIterationsLabel">Run how many rounds</span><input id="simpleIterationsInput" type="number" min="1" value="5"></label>
              <label class="field"><span id="simpleStopAtLabel">Or stop at this time</span><input id="simpleStopAtInput" type="datetime-local"></label>
            </div>
            <div class="inline-actions">
              <button class="action" id="simpleStartBtn" type="button">Start now</button>
              <button class="ghost" id="simpleStopBtn" type="button" disabled>Stop</button>
              <button class="ghost" id="simpleApplyBtn" type="button" disabled>Bring back best result</button>
              <span class="status-note" id="simpleRunStatus"></span>
            </div>
            <div class="small" id="simpleActionHint" aria-live="polite"></div>
            <div class="helper-note" id="simpleHelper">Write one clear goal, choose how long to run, then press start.</div>
            <div class="helper-note" id="simpleReadiness" aria-live="polite">Checking local setup...</div>
          </div>
          <div class="guide-card">
            <div class="small" id="progressIntro">Live progress</div>
            <div class="progress-stack">
              <div class="progress-card">
                <div class="small" id="currentStatusLabel">Current status</div>
                <div id="simpleProgressSummary">No run yet.</div>
              </div>
              <div class="progress-card">
                <div class="small" id="currentTaskLabel">Current task</div>
                <div id="simpleCurrentTask">Nothing is running.</div>
              </div>
            </div>
            <div class="terminal" id="simpleTerminal" aria-live="polite">No task selected yet.</div>
          </div>
        </div>
      </div>
      <div class="panel span-7 advanced-only">
        <h2 id="actionsTitle">One-click actions</h2>
        <div class="small" id="actionsCopy">Use the UI for the first 90% of setup, then drop into logs when you want detail.</div>
        <div class="form-grid">
          <label class="field"><span id="iterationsLabel">Iterations</span><input id="iterations" name="iterations" type="number" inputmode="numeric" autocomplete="off" min="1" value="5"></label>
          <label class="field"><span id="configPathLabel">Config path</span><input id="configPath" name="config_path" type="text" autocomplete="off" value="autoresearch.toml"></label>
        </div>
        <div class="actions">
          <button class="action" data-action="doctor_fix" id="btnDoctor">Repair setup gaps</button>
          <button class="action" data-action="onboard" id="btnOnboard">Onboard + nightly</button>
          <button class="ghost" data-action="nightly" id="btnNightly">Generate nightly workflow</button>
          <button class="ghost" data-action="start" id="btnStart">Start bounded loop</button>
          <button class="ghost" data-action="demo" id="btnDemo">Run built-in demo</button>
        </div>
      </div>
      <div class="panel span-5 advanced-only">
        <h2 id="healthTitle">Repository health</h2>
        <div class="chips" id="healthChips"></div>
        <p class="small" id="useCaseText"></p>
        <div class="small"><strong id="metricHintLabel">Metric hint</strong><div id="metricHint">-</div></div>
        <div class="small" style="margin-top:12px;"><strong id="guardHintLabel">Guard hint</strong><div id="guardHint">-</div></div>
      </div>
      <div class="panel span-6 advanced-only">
        <h2 id="configTitle">Research config</h2>
        <div id="configBody" class="small">No config loaded yet.</div>
      </div>
      <div class="panel span-6 advanced-only">
        <h2 id="editorTitle">Config editor</h2>
        <div class="config-grid">
          <div class="preset-row wide">
            <button class="ghost" id="presetPythonBtn" type="button">My repo is Python</button>
            <button class="ghost" id="presetNodeBtn" type="button">My repo is Node</button>
            <button class="ghost" id="presetGenericBtn" type="button">I am not sure</button>
          </div>
          <label class="field"><span id="fieldGoal">Goal</span><textarea id="goalInput" class="wide"></textarea></label>
          <label class="field"><span id="fieldMetric">Metric</span><input id="metricInput" type="text" autocomplete="off"></label>
          <label class="field"><span id="fieldDirection">Direction</span><select id="directionInput"><option value="higher">higher</option><option value="lower">lower</option></select></label>
          <label class="field wide"><span id="fieldVerify">Verify command</span><textarea id="verifyInput"></textarea></label>
          <label class="field wide"><span id="fieldGuard">Guard command</span><textarea id="guardInput"></textarea></label>
          <label class="field wide"><span id="fieldScope">Scope</span><input id="scopeInput" type="text" autocomplete="off" placeholder="src/**, tests/**"></label>
          <label class="field"><span id="fieldIterations">Default iterations</span><input id="defaultIterationsInput" type="number" min="1" value="5"></label>
          <label class="field"><span id="fieldMinDelta">Min delta</span><input id="minDeltaInput" type="number" step="0.1" value="0.0"></label>
        </div>
        <div class="toolbar">
          <button class="action" id="saveConfigBtn" type="button">Save config</button>
          <span class="status-note" id="saveStatus"></span>
        </div>
      </div>
      <div class="panel span-6 advanced-only">
        <h2 id="resultsTitle">Recent results</h2>
        <div id="resultsBody" class="small">No results yet.</div>
      </div>
      <div class="panel span-6 advanced-only">
        <h2 id="chartTitle">Metric chart</h2>
        <div id="chartBody" class="chart-shell"></div>
      </div>
      <div class="panel span-4 advanced-only">
        <h2 id="tasksTitle">Task queue</h2>
        <div class="task-list" id="taskList"></div>
      </div>
      <div class="panel span-8 advanced-only">
        <h2 id="outputTitle">Task output</h2>
        <div class="terminal" id="terminal" aria-live="polite">No task selected yet.</div>
      </div>
      <div class="panel span-12 advanced-only">
        <h2 id="timelineTitle">Run timeline</h2>
        <div id="timelineBody" class="timeline"></div>
      </div>
      <div class="panel span-12 advanced-only">
        <h2 id="logTitle">Run log viewer</h2>
        <div class="small" id="logMeta">Pick a run log from the timeline.</div>
        <div class="terminal" id="logViewer">No log selected yet.</div>
      </div>
    </section>
  </main>
  <script>
    const copy = {
      en: {
        eyebrow: "Codex Autoresearch UI",
        title: "One goal. One tap. Clear progress.",
        summary: "Type what you want changed, choose how long to run, and watch the result live.",
        repoLabel: "Repository",
        presetLabel: "Preset",
        configLabel: "Config",
        runLabel: "Latest Run",
        modeBeginnerBtn: "Simple",
        modeAdvancedBtn: "Advanced",
        simpleTitle: "Say what you want. Then press Start.",
        simpleIntro: "Write the result you want. Pick how long it should run. Press start.",
        simpleGoalLabel: "What do you want to make happen?",
        simpleStarterTitle: "Try a starter goal",
        simpleIterationsLabel: "Run how many rounds",
        simpleStopAtLabel: "Or stop at this time",
        simpleStartBtn: "Start now",
        simpleStopBtn: "Stop",
        simpleApplyBtn: "Bring back best result",
        simpleHelper: "The app will choose the safest workspace and keep the run measurable.",
        simpleReadinessChecking: "Checking local setup...",
        simpleReadyCurrent: "Ready to start. This run can work directly in the current repo.",
        simpleReadySafe: "Ready to start. Uncommitted changes were found, so the run will use a safe copy and you can bring back the best result later.",
        simplePreviewTitle: "Run preview",
        simplePreviewGoalLabel: "Goal",
        simplePreviewRunLabel: "How it will run",
        simplePreviewFinishLabel: "What comes back",
        simplePreviewEmpty: "Write a goal and the app will prepare the run for you.",
        simplePreviewRunCurrent: "Runs directly in this repo.",
        simplePreviewRunSafe: "Runs in a safe copy first.",
        simplePreviewFinishCurrent: "The best verified result stays in this repo.",
        simplePreviewFinishSafe: "The best verified patch can be brought back after the run.",
        simplePreviewRoundsPrefix: "Rounds",
        simplePreviewUntilPrefix: "Run until",
        simplePreviewVerifyPrefix: "Progress check",
        simplePreviewGuardPrefix: "Safety check",
        simplePreviewGuardNone: "No extra safety check",
        simpleChecksTitle: "What the app checks for you",
        simpleChecksVerifyLabel: "Progress check",
        simpleChecksVerifyText: "After each round, the app runs this command to measure whether the repo improved.",
        simpleChecksGuardLabel: "Safety check",
        simpleChecksGuardText: "Before a result counts, this extra command also has to pass.",
        simpleChecksGuardNone: "No extra safety check for this plan.",
        simpleExplainTitle: "Why this plan fits your repo",
        simpleExplainPresetPython: "This looks like a Python repo, so the app will use Python-friendly defaults for you.",
        simpleExplainPresetNode: "This looks like a Node repo, so the app will use Node-friendly defaults for you.",
        simpleExplainPresetGeneric: "This repo does not clearly match Python or Node, so the app will use safe generic defaults.",
        simpleExplainWorkspaceCurrent: "Your worktree is clean enough, so the run can work directly in this repo.",
        simpleExplainWorkspaceSafe: "Your repo has uncommitted changes, so the run will happen in a safe copy first.",
        simpleExplainScopePrefix: "It will only edit files inside",
        simpleExplainVerifyPrefix: "It checks progress by running",
        simpleExplainGuardPrefix: "Before a result counts, this safety check also has to pass",
        simpleExplainGuardNone: "There is no extra safety check beyond the main progress check.",
        simpleFlowTitle: "What happens after you press start",
        simpleFlowWrite: "Autoresearch writes a starter config with repo-specific defaults for you.",
        simpleFlowWorkspaceCurrent: "It runs directly in this repo because the current worktree is clean enough.",
        simpleFlowWorkspaceSafe: "It runs in a safe copy because uncommitted changes were found in this repo.",
        simpleFlowFinishCurrent: "It verifies each round and leaves the best result directly in this repo.",
        simpleFlowFinishSafe: "It verifies each round, then lets you bring the best patch back into this repo.",
        simpleBlockedCodex: "Install the Codex CLI first. Once `codex` is available, this page can start runs for you.",
        simpleBlockedGit: "Install Git first. Once the `git` command is available, this page can prepare and run the repo for you.",
        simpleAlreadyRunning: "A run is already in progress. Stop it or wait for it to finish before starting another.",
        simplePlanTitle: "Before you start",
        simplePlanIntro: "Simple mode auto-writes the config, picks the repo preset, and decides whether to run here or in a safe copy.",
        simplePlanPresetLabel: "Detected preset",
        simplePlanWorkspaceLabel: "Run workspace",
        simplePlanScopeLabel: "Allowed edit scope",
        simplePlanVerifyLabel: "Progress check",
        simplePlanGuardLabel: "Safety check",
        simplePlanFilesLabel: "Files it will create or update",
        simpleGuardNone: "No extra safety check",
        simpleWorkspaceCurrent: "Current repo",
        simpleWorkspaceCurrentDetail: "The worktree looks clean enough, so the run can work directly in this checkout.",
        simpleWorkspaceSafe: "Safe copy",
        simpleWorkspaceSafeDetail: "Uncommitted changes were detected, so the run will happen in a snapshot and the best patch can be brought back.",
        progressIntro: "Live progress",
        currentStatusLabel: "Current status",
        currentTaskLabel: "Current task",
        actionsTitle: "One-click actions",
        actionsCopy: "Use the UI for the first 90% of setup, then drop into logs when you want detail.",
        iterationsLabel: "Iterations",
        configPathLabel: "Config path",
        btnDoctor: "Repair Setup Gaps",
        btnOnboard: "Onboard + Nightly",
        btnNightly: "Generate Nightly Workflow",
        btnStart: "Start Bounded Loop",
        btnDemo: "Run Built-In Demo",
        healthTitle: "Repository health",
        metricHintLabel: "Metric hint",
        guardHintLabel: "Guard hint",
        configTitle: "Research config",
        editorTitle: "Config editor",
        fieldGoal: "Goal",
        presetPythonBtn: "My repo is Python",
        presetNodeBtn: "My repo is Node",
        presetGenericBtn: "I am not sure",
        fieldMetric: "Metric",
        fieldDirection: "Direction",
        fieldVerify: "Verify command",
        fieldGuard: "Guard command",
        fieldScope: "Scope",
        fieldIterations: "Default iterations",
        fieldMinDelta: "Min delta",
        chartTitle: "Metric chart",
        timelineTitle: "Run timeline",
        logTitle: "Run log viewer",
        resultsTitle: "Recent results",
        tasksTitle: "Task queue",
        outputTitle: "Task output",
        configMissing: "Missing",
        configReady: "Ready",
        noConfig: "No config loaded yet.",
        noResults: "No results yet.",
        noTask: "No task selected yet.",
        noTimeline: "No run timeline yet.",
        noLog: "No log selected yet.",
        runNone: "None yet",
        saveConfig: "Save config",
        saveDone: "Config saved.",
        saveFailed: "Could not save config.",
        copyNext: "Suggested next command",
        heroGitReady: "git ready",
        heroGitMissing: "git missing",
        heroCodexReady: "codex ready",
        heroCodexMissing: "codex missing",
        heroConfigReadyChip: "config ready",
        heroConfigMissingChip: "config missing",
        healthPresetPrefix: "preset",
        configSummaryGoal: "Goal",
        configSummaryMetric: "Metric",
        configSummaryVerify: "Verify",
        configSummaryGuard: "Guard",
        valueNone: "none",
        resultsIterationHeader: "Iteration",
        resultsStatusHeader: "Status",
        resultsMetricHeader: "Metric",
        resultsGuardHeader: "Guard",
        chartAriaLabel: "Metric history chart",
        chartBest: "best",
        chartWorst: "worst",
        timelineIteration: "Iteration",
        timelineStatus: "status",
        timelineMetric: "metric",
        timelineGuard: "guard",
        timelineStdout: "stdout",
        timelineStderr: "stderr",
        statusRunning: "Running",
        statusStopping: "Stopping",
        statusDone: "Done",
        statusFailed: "Failed",
        simpleIdle: "No run yet.",
        simpleTaskIdle: "Nothing is running.",
        simpleStarted: "Run started.",
        simpleStopped: "Stop requested.",
        simpleStartFailed: "Could not start the run.",
        simpleStopFailed: "Could not stop the run.",
        simpleApplyReady: "Best result is ready to bring back.",
        simpleApplyDone: "Best result was applied to this project.",
        simpleApplyFailed: "Could not bring the result back automatically.",
        simpleNeedGoal: "Write what you want first.",
        simpleWaiting: "Waiting to start",
        simpleRunningUntil: "Running until",
        simpleRounds: "Rounds",
        simpleTaskPrefix: "Task",
        taskStartedSuffix: "started..."
      },
      zh: {
        eyebrow: "Codex Autoresearch 控制台",
        title: "一个目标，一键开始，进度清楚可见。",
        summary: "写下你想完成什么，选运行多久，然后直接看实时进度。",
        repoLabel: "仓库",
        presetLabel: "预设",
        configLabel: "配置",
        runLabel: "最近运行",
        modeBeginnerBtn: "简单模式",
        modeAdvancedBtn: "高级模式",
        simpleTitle: "写下目标，然后点开始",
        simpleIntro: "写下你想要的结果，选运行多久，然后点开始。",
        simpleGoalLabel: "你想让它做什么？",
        simpleStarterTitle: "可以先点一个示例目标",
        simpleIterationsLabel: "跑多少轮",
        simpleStopAtLabel: "或者跑到这个时间",
        simpleStartBtn: "立即开始",
        simpleStopBtn: "停止",
        simpleApplyBtn: "带回最佳结果",
        simpleHelper: "应用会自动选择更安全的运行方式，并持续检查结果。",
        simpleReadinessChecking: "正在检查本地环境...",
        simpleReadyCurrent: "已经可以开始，这次会直接在当前仓库里运行。",
        simpleReadySafe: "已经可以开始，但检测到未提交改动，所以会先用安全副本运行，之后你可以把最佳结果带回来。",
        simplePreviewTitle: "运行预览",
        simplePreviewGoalLabel: "目标",
        simplePreviewRunLabel: "会怎么运行",
        simplePreviewFinishLabel: "最后会得到什么",
        simplePreviewEmpty: "先写一个目标，应用就会自动准备这次运行。",
        simplePreviewRunCurrent: "会直接在当前仓库里运行。",
        simplePreviewRunSafe: "会先在安全副本里运行。",
        simplePreviewFinishCurrent: "最佳且验证通过的结果会直接留在当前仓库。",
        simplePreviewFinishSafe: "运行结束后，可以把最佳且验证通过的补丁带回当前仓库。",
        simplePreviewRoundsPrefix: "轮数",
        simplePreviewUntilPrefix: "运行到",
        simplePreviewVerifyPrefix: "进展检查",
        simplePreviewGuardPrefix: "安全检查",
        simplePreviewGuardNone: "没有额外安全检查",
        simpleChecksTitle: "应用会替你检查什么",
        simpleChecksVerifyLabel: "进展检查",
        simpleChecksVerifyText: "每一轮后，应用都会运行这个命令，用它判断项目有没有变得更好。",
        simpleChecksGuardLabel: "安全检查",
        simpleChecksGuardText: "在结果算数之前，这个额外命令也必须通过。",
        simpleChecksGuardNone: "这个方案没有额外的安全检查。",
        simpleExplainTitle: "为什么会这样安排",
        simpleExplainPresetPython: "这个仓库看起来像 Python 项目，所以应用会直接帮你套用适合 Python 的默认设置。",
        simpleExplainPresetNode: "这个仓库看起来像 Node 项目，所以应用会直接帮你套用适合 Node 的默认设置。",
        simpleExplainPresetGeneric: "这个仓库暂时看不出明显的 Python 或 Node 特征，所以应用会先用更稳妥的通用默认设置。",
        simpleExplainWorkspaceCurrent: "当前工作区足够干净，所以这次可以直接在这个仓库里运行。",
        simpleExplainWorkspaceSafe: "当前仓库有未提交改动，所以这次会先在安全副本里运行。",
        simpleExplainScopePrefix: "这次运行只会改这些范围里的文件",
        simpleExplainVerifyPrefix: "它会通过这个进展检查命令判断有没有进展",
        simpleExplainGuardPrefix: "在结果算数之前，这个安全检查也必须通过",
        simpleExplainGuardNone: "除了主判断命令之外，这次没有额外的安全检查。",
        simpleFlowTitle: "点开始之后会发生什么",
        simpleFlowWrite: "Autoresearch 会先帮你写好一个带仓库默认值的起步配置。",
        simpleFlowWorkspaceCurrent: "因为当前工作区足够干净，所以这次会直接在这个仓库里运行。",
        simpleFlowWorkspaceSafe: "因为检测到未提交改动，所以这次会先在安全副本里运行。",
        simpleFlowFinishCurrent: "每一轮都会执行验证，最佳结果会直接保留在当前仓库里。",
        simpleFlowFinishSafe: "每一轮都会执行验证，最后你可以把最佳补丁带回当前仓库。",
        simpleBlockedCodex: "请先安装 Codex CLI。只有系统里有 `codex` 命令后，这个页面才能替你启动运行。",
        simpleBlockedGit: "请先安装 Git。只有系统里有 `git` 命令后，这个页面才能替你准备仓库并启动运行。",
        simpleAlreadyRunning: "已经有一个运行在进行中。请先停止它，或者等它完成后再启动新的运行。",
        simplePlanTitle: "开始前你会看到这些",
        simplePlanIntro: "简单模式会自动写配置、判断仓库类型，并决定直接在当前仓库运行还是先用安全副本。",
        simplePlanPresetLabel: "识别到的预设",
        simplePlanWorkspaceLabel: "运行位置",
        simplePlanScopeLabel: "允许改动的范围",
        simplePlanVerifyLabel: "进展检查",
        simplePlanGuardLabel: "安全检查",
        simplePlanFilesLabel: "会创建或更新的文件",
        simpleGuardNone: "没有额外安全检查",
        simpleWorkspaceCurrent: "当前仓库",
        simpleWorkspaceCurrentDetail: "当前工作区足够干净，所以会直接在这个仓库里运行。",
        simpleWorkspaceSafe: "安全副本",
        simpleWorkspaceSafeDetail: "检测到未提交改动，所以会先在快照里运行，再把最佳补丁带回来。",
        progressIntro: "实时进度",
        currentStatusLabel: "当前状态",
        currentTaskLabel: "当前任务",
        actionsTitle: "一键操作",
        actionsCopy: "前 90% 的常见操作都可以在这里点掉，想看细节时再进日志。",
        iterationsLabel: "迭代次数",
        configPathLabel: "配置路径",
        btnDoctor: "修复准备缺项",
        btnOnboard: "上手并生成夜跑",
        btnNightly: "生成 nightly workflow",
        btnStart: "开始有边界循环",
        btnDemo: "运行内置 demo",
        healthTitle: "仓库健康度",
        metricHintLabel: "指标建议",
        guardHintLabel: "守卫建议",
        configTitle: "研究配置",
        editorTitle: "配置编辑器",
        fieldGoal: "目标",
        presetPythonBtn: "我的项目是 Python",
        presetNodeBtn: "我的项目是 Node",
        presetGenericBtn: "我也不确定",
        fieldMetric: "指标",
        fieldDirection: "方向",
        fieldVerify: "Verify 命令",
        fieldGuard: "Guard 命令",
        fieldScope: "作用范围",
        fieldIterations: "默认迭代次数",
        fieldMinDelta: "最小增量",
        chartTitle: "指标图表",
        timelineTitle: "运行时间线",
        logTitle: "运行日志查看器",
        resultsTitle: "最近结果",
        tasksTitle: "任务队列",
        outputTitle: "任务输出",
        configMissing: "缺失",
        configReady: "就绪",
        noConfig: "还没有加载到配置。",
        noResults: "还没有结果。",
        noTask: "还没有选中的任务。",
        noTimeline: "还没有运行时间线。",
        noLog: "还没有选中的日志。",
        runNone: "还没有",
        saveConfig: "保存配置",
        saveDone: "配置已保存。",
        saveFailed: "配置保存失败。",
        copyNext: "建议下一步命令",
        heroGitReady: "git 已就绪",
        heroGitMissing: "git 缺失",
        heroCodexReady: "codex 已就绪",
        heroCodexMissing: "codex 缺失",
        heroConfigReadyChip: "配置已就绪",
        heroConfigMissingChip: "配置缺失",
        healthPresetPrefix: "预设",
        configSummaryGoal: "目标",
        configSummaryMetric: "指标",
        configSummaryVerify: "Verify",
        configSummaryGuard: "Guard",
        valueNone: "无",
        resultsIterationHeader: "迭代",
        resultsStatusHeader: "状态",
        resultsMetricHeader: "指标",
        resultsGuardHeader: "Guard",
        chartAriaLabel: "指标历史图表",
        chartBest: "最好",
        chartWorst: "最差",
        timelineIteration: "迭代",
        timelineStatus: "状态",
        timelineMetric: "指标",
        timelineGuard: "Guard",
        timelineStdout: "stdout",
        timelineStderr: "stderr",
        statusRunning: "运行中",
        statusStopping: "停止中",
        statusDone: "完成",
        statusFailed: "失败",
        simpleIdle: "还没有开始运行。",
        simpleTaskIdle: "当前没有在跑的任务。",
        simpleStarted: "已经开始运行。",
        simpleStopped: "已经发出停止请求。",
        simpleStartFailed: "启动失败。",
        simpleStopFailed: "停止失败。",
        simpleApplyReady: "最佳结果已经准备好，可以带回当前项目。",
        simpleApplyDone: "最佳结果已经带回当前项目。",
        simpleApplyFailed: "自动带回失败。",
        simpleNeedGoal: "先写下你想做什么。",
        simpleWaiting: "等待开始",
        simpleRunningUntil: "运行到",
        simpleRounds: "轮数",
        simpleTaskPrefix: "任务",
        taskStartedSuffix: "已开始..."
      }
    };
    const starterGoals = {
      en: {
        python: [
          {
            label: "Fix a bug safely",
            goal: "Find the most likely user-facing bug in scope, fix it, and keep the current test suite green."
          },
          {
            label: "Simplify a complex module",
            goal: "Refactor the most complex Python module in scope so it is easier to read, while preserving behavior and tightening tests if needed."
          },
          {
            label: "Make onboarding clearer",
            goal: "Improve the first-run experience for a new user, simplify confusing copy, and keep the project easy to understand."
          },
          {
            label: "Strengthen test coverage",
            goal: "Add focused tests around the riskiest untested path in scope and make any minimal code changes needed to keep them passing."
          }
        ],
        node: [
          {
            label: "Clarify the main flow",
            goal: "Make the main user flow easier to understand, simplify the wording, and preserve the current behavior."
          },
          {
            label: "Polish the interface",
            goal: "Improve the most visible UI in scope so it feels cleaner, more intentional, and easier for a first-time user."
          },
          {
            label: "Tighten reliability",
            goal: "Fix the most likely broken or flaky path in scope and keep the build and tests passing."
          },
          {
            label: "Trim frontend complexity",
            goal: "Reduce unnecessary UI complexity in scope, keep the experience simple, and avoid regressions."
          }
        ],
        generic: [
          {
            label: "Fix one important issue",
            goal: "Find the highest-impact issue inside the allowed scope, fix it cleanly, and verify the project still works."
          },
          {
            label: "Make this easier to use",
            goal: "Improve the most confusing part of the product for a first-time user and keep the implementation simple."
          },
          {
            label: "Improve maintainability",
            goal: "Simplify the most complex code path in scope without changing behavior and add verification if needed."
          },
          {
            label: "Add missing safety checks",
            goal: "Add focused validation or tests around the riskiest workflow in scope so the project is safer to change."
          }
        ]
      },
      zh: {
        python: [
          {
            label: "安全修一个 bug",
            goal: "找出当前范围里最可能影响用户的 bug，修复它，并保持现有测试全部通过。"
          },
          {
            label: "简化复杂模块",
            goal: "重构范围里最复杂的 Python 模块，让代码更容易读懂，同时保持行为不变，必要时补强测试。"
          },
          {
            label: "让新手更容易上手",
            goal: "优化第一次使用时最容易让人困惑的地方，把文案写得更直白，并让项目更容易理解。"
          },
          {
            label: "补强测试覆盖",
            goal: "围绕当前范围里风险最高但测试不足的路径补上测试，并做最小代码修改让它稳定通过。"
          }
        ],
        node: [
          {
            label: "讲清主流程",
            goal: "把最主要的用户流程做得更容易看懂，简化文案，同时保持现有行为不变。"
          },
          {
            label: "把界面打磨高级一点",
            goal: "优化范围里最显眼的界面，让它更干净、更有设计感，也更适合第一次使用的人。"
          },
          {
            label: "提高稳定性",
            goal: "修复当前范围里最可能出错或不稳定的路径，并保持构建和测试通过。"
          },
          {
            label: "减少前端复杂度",
            goal: "删掉范围里不必要的界面复杂度，让体验更简单，同时避免回归。"
          }
        ],
        generic: [
          {
            label: "先修一个重要问题",
            goal: "在允许范围里找出影响最大的一个问题，干净地修好它，并验证项目还能正常工作。"
          },
          {
            label: "让它更容易使用",
            goal: "优化产品里最容易让新手困惑的部分，让第一次使用的人也能更快看懂。"
          },
          {
            label: "提高可维护性",
            goal: "简化范围里最复杂的一段代码，在不改行为的前提下让后续维护更容易，必要时补验证。"
          },
          {
            label: "补上安全护栏",
            goal: "给当前范围里风险最高的流程补上必要校验或测试，让后续改动更安全。"
          }
        ]
      }
    };
    let lang = "en";
    let mode = "beginner";
    let selectedTaskId = null;
    let selectedLogPath = "";

    function setMode(next) {
      mode = next;
      document.querySelectorAll("[data-mode]").forEach(button => {
        button.classList.toggle("active", button.dataset.mode === next);
      });
      document.querySelectorAll(".advanced-only").forEach(node => {
        node.classList.toggle("hidden-by-mode", next === "beginner");
      });
    }

    function setLang(next) {
      lang = next;
      document.querySelectorAll("[data-lang]").forEach(button => {
        button.classList.toggle("active", button.dataset.lang === next);
      });
      const text = copy[lang];
      for (const [key, value] of Object.entries(text)) {
        const el = document.getElementById(key);
        if (el) el.textContent = value;
      }
      const saveButton = document.getElementById("saveConfigBtn");
      if (saveButton) saveButton.textContent = text.saveConfig;
      const simpleStartButton = document.getElementById("simpleStartBtn");
      if (simpleStartButton) simpleStartButton.textContent = text.simpleStartBtn;
      const simpleStopButton = document.getElementById("simpleStopBtn");
      if (simpleStopButton) simpleStopButton.textContent = text.simpleStopBtn;
      const simpleApplyButton = document.getElementById("simpleApplyBtn");
      if (simpleApplyButton) simpleApplyButton.textContent = text.simpleApplyBtn;
      document.querySelectorAll("[data-mode]").forEach(button => {
        const key = button.id;
        if (text[key]) button.textContent = text[key];
      });
      if (window._state) {
        renderState(window._state);
      } else {
        renderSimpleStarters("generic");
      }
      renderSimplePreview(window._state || {});
      renderTaskList(window._tasks || []);
      renderSelectedTask(window._tasks || []);
      renderSimpleProgress(window._tasks || []);
      if (!selectedLogPath) {
        document.getElementById("logViewer").textContent = copy[lang].noLog;
      }
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      })[char]);
    }

    function pill(ok, label) {
      const kind = ok === true ? "ok" : ok === false ? "bad" : "warn";
      return `<span class="pill ${kind}">${escapeHtml(label)}</span>`;
    }

    function statusText(status) {
      const map = {
        running: copy[lang].statusRunning,
        stopping: copy[lang].statusStopping,
        done: copy[lang].statusDone,
        failed: copy[lang].statusFailed
      };
      return map[status] || status || "-";
    }

    function taskStatusKind(status) {
      if (status === "done") return true;
      if (status === "running" || status === "stopping") return null;
      return false;
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Request failed: ${response.status}`);
      }
      return payload;
    }

    function buildPayload(action) {
      return {
        action,
        configPath: document.getElementById("configPath").value || "autoresearch.toml",
        iterations: Number(document.getElementById("iterations").value || "5"),
        writeNightly: true,
        force: true
      };
    }

    async function runAction(action) {
      try {
        await api("/api/actions", {
          method: "POST",
          body: JSON.stringify(buildPayload(action))
        });
        await refreshTasks(true);
      } catch (error) {
        document.getElementById("logViewer").textContent = simpleStartErrorMessage(error.message);
      }
    }

    function simpleStartErrorMessage(message) {
      if (!message) return copy[lang].simpleStartFailed;
      if (message === "simple goal is required") return copy[lang].simpleNeedGoal;
      if (message === "missing_codex") return copy[lang].simpleBlockedCodex;
      if (message === "missing_git") return copy[lang].simpleBlockedGit;
      if (message === "task_already_running") return copy[lang].simpleAlreadyRunning;
      return message;
    }

    function showRunMessage(message) {
      const text = simpleStartErrorMessage(message);
      document.getElementById("simpleRunStatus").textContent = text;
      document.getElementById("logViewer").textContent = text;
    }

    function resolvedSimpleGoal(state = window._state || {}) {
      const simplePlan = state.simplePlan || {};
      const config = state.config || {};
      const goalInput = document.getElementById("simpleGoalInput");
      const typedGoal = goalInput ? String(goalInput.value || "").trim() : "";
      const placeholderGoal = goalInput ? String(goalInput.placeholder || "").trim() : "";
      return typedGoal
        || placeholderGoal
        || String(simplePlan.defaultGoal || "").trim()
        || String(config.goal || "").trim();
    }

    function simplePresetNarrative(preset) {
      if (preset === "python") return copy[lang].simpleExplainPresetPython;
      if (preset === "node") return copy[lang].simpleExplainPresetNode;
      return copy[lang].simpleExplainPresetGeneric;
    }

    function simplePayload() {
      return {
        action: "simple_start",
        configPath: document.getElementById("configPath").value || "autoresearch.toml",
        goal: resolvedSimpleGoal(),
        iterations: Number(document.getElementById("simpleIterationsInput").value || "5"),
        stopAt: document.getElementById("simpleStopAtInput").value || null
      };
    }

    async function runSimpleStart() {
      const status = document.getElementById("simpleRunStatus");
      const payload = simplePayload();
      if (!String(payload.goal || "").trim()) {
        status.textContent = copy[lang].simpleNeedGoal;
        return;
      }
      try {
        await api("/api/actions", {
          method: "POST",
          body: JSON.stringify(payload)
        });
        status.textContent = copy[lang].simpleStarted;
        await refreshState();
        await refreshTasks(true);
      } catch (error) {
        showRunMessage(error.message);
      }
    }

    async function stopCurrentRun() {
      const status = document.getElementById("simpleRunStatus");
      try {
        const response = await api("/api/actions", {
          method: "POST",
          body: JSON.stringify({ action: "stop" })
        });
        status.textContent = response.stopped ? copy[lang].simpleStopped : copy[lang].simpleStopFailed;
        await refreshTasks();
      } catch (error) {
        status.textContent = copy[lang].simpleStopFailed;
      }
    }

    async function applyBestResult() {
      const status = document.getElementById("simpleRunStatus");
      const task = (window._tasks || []).find(item => item.id === selectedTaskId) || (window._tasks || [])[0];
      if (!task) {
        status.textContent = copy[lang].simpleApplyFailed;
        return;
      }
      try {
        await api("/api/actions", {
          method: "POST",
          body: JSON.stringify({ action: "apply_best", taskId: task.id })
        });
        status.textContent = copy[lang].simpleApplyDone;
        await refreshTasks();
      } catch (error) {
        status.textContent = `${copy[lang].simpleApplyFailed} ${error.message || ""}`.trim();
      }
    }

    function fillSimpleGoal(goal) {
      const input = document.getElementById("simpleGoalInput");
      input.value = goal;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      document.getElementById("simpleRunStatus").textContent = "";
      renderSimplePreview(window._state || {});
    }

    function renderSimpleStartState(state) {
      const simpleStart = state.simpleStart || { canStart: true, blocker: null };
      const simplePlan = state.simplePlan || {};
      const tasks = window._tasks || [];
      const hasRunningTask = tasks.some(task => task.status === "running" || task.status === "stopping");
      const startButton = document.getElementById("simpleStartBtn");
      const stopButton = document.getElementById("simpleStopBtn");
      const note = document.getElementById("simpleReadiness");
      let message = copy[lang].simpleReadinessChecking;
      if (simpleStart.blocker) {
        message = simpleStartErrorMessage(simpleStart.blocker);
      } else if (simplePlan.usesSafeCopy) {
        message = copy[lang].simpleReadySafe;
      } else {
        message = copy[lang].simpleReadyCurrent;
      }
      if (note) note.textContent = message;
      if (startButton) startButton.disabled = Boolean(simpleStart.blocker || hasRunningTask);
      if (stopButton) stopButton.disabled = !hasRunningTask;
    }

    function renderSimplePreview(state) {
      const simplePlan = state.simplePlan || {};
      const iterationsInput = document.getElementById("simpleIterationsInput");
      const stopAtInput = document.getElementById("simpleStopAtInput");
      const helper = document.getElementById("simpleHelper");
      const goal = resolvedSimpleGoal(state);
      const iterations = Math.max(1, Number(iterationsInput.value || "5") || 5);
      const stopAt = String(stopAtInput.value || "").trim();
      const schedule = stopAt
        ? `${copy[lang].simplePreviewUntilPrefix}: ${stopAt}`
        : `${copy[lang].simplePreviewRoundsPrefix}: ${iterations}`;
      const workspace = simplePlan.usesSafeCopy
        ? copy[lang].simplePreviewRunSafe
        : copy[lang].simplePreviewRunCurrent;
      const finish = simplePlan.usesSafeCopy
        ? copy[lang].simplePreviewFinishSafe
        : copy[lang].simplePreviewFinishCurrent;
      if (!helper) return;
      helper.textContent = goal
        ? `${schedule}. ${workspace} ${finish}`
        : copy[lang].simplePreviewEmpty;
    }

    function renderSimpleStarters(preset) {
      const host = document.getElementById("simpleStarterList");
      const languageGoals = starterGoals[lang] || starterGoals.en;
      const items = languageGoals[preset] || languageGoals.generic || [];
      if (host) {
        host.innerHTML = items.map((item, index) => `
        <button class="starter-btn" type="button" data-starter-index="${index}">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${escapeHtml(item.goal)}</span>
        </button>
      `).join("");
        document.querySelectorAll("[data-starter-index]").forEach(node => {
          node.onclick = () => {
            const item = items[Number(node.dataset.starterIndex)];
            if (item) fillSimpleGoal(item.goal);
          };
        });
      }
      const input = document.getElementById("simpleGoalInput");
      if (!input.value.trim() && items[0]) {
        input.placeholder = items[0].goal;
      }
    }

    function taskLabel(action) {
      const map = {
        simple_start: copy[lang].simpleStartBtn,
        doctor_fix: copy[lang].btnDoctor,
        onboard: copy[lang].btnOnboard,
        nightly: copy[lang].btnNightly,
        start: copy[lang].btnStart,
        demo: copy[lang].btnDemo
      };
      return map[action] || action;
    }

    async function saveConfig() {
      const payload = {
        configPath: document.getElementById("configPath").value || "autoresearch.toml",
        goal: document.getElementById("goalInput").value,
        metric: document.getElementById("metricInput").value,
        direction: document.getElementById("directionInput").value,
        verify: document.getElementById("verifyInput").value,
        guard: document.getElementById("guardInput").value,
        scope: document.getElementById("scopeInput").value,
        iterations: Number(document.getElementById("defaultIterationsInput").value || "5"),
        minDelta: Number(document.getElementById("minDeltaInput").value || "0"),
      };
      const note = document.getElementById("saveStatus");
      try {
        await api("/api/config", {
          method: "POST",
          body: JSON.stringify(payload)
        });
        note.textContent = copy[lang].saveDone;
        await refreshState();
      } catch (error) {
        note.textContent = copy[lang].saveFailed;
      }
    }

    async function loadPreset(preset) {
      const response = await api(`/api/preset/${preset}`);
      const values = response.values || {};
      document.getElementById("goalInput").value = values.goal || "";
      document.getElementById("metricInput").value = values.metric || "";
      document.getElementById("directionInput").value = values.direction || "higher";
      document.getElementById("verifyInput").value = values.verify || "";
      document.getElementById("guardInput").value = values.guard || "";
      document.getElementById("scopeInput").value = values.scope || "";
      document.getElementById("defaultIterationsInput").value = values.iterations || 5;
      document.getElementById("minDeltaInput").value = values.minDelta || 0;
      document.getElementById("saveStatus").textContent = "";
    }

    async function loadLog(path) {
      selectedLogPath = path;
      const payload = await api(`/api/log?path=${encodeURIComponent(path)}`);
      document.getElementById("logMeta").textContent = payload.path || path;
      document.getElementById("logViewer").textContent = payload.exists ? (payload.content || "") : copy[lang].noLog;
    }

    function renderState(state) {
      document.getElementById("repoName").textContent = state.repoName;
      document.getElementById("presetName").textContent = state.suggestion.preset;
      document.getElementById("configStatus").textContent = state.configExists ? copy[lang].configReady : copy[lang].configMissing;
      document.getElementById("runName").textContent = state.latestRun || copy[lang].runNone;
      const simplePlan = state.simplePlan || {};
      document.getElementById("heroChips").innerHTML = [
        pill(state.gitInstalled, state.gitInstalled ? copy[lang].heroGitReady : copy[lang].heroGitMissing),
        pill(state.codexInstalled, state.codexInstalled ? copy[lang].heroCodexReady : copy[lang].heroCodexMissing),
        pill(state.configExists, state.configExists ? copy[lang].heroConfigReadyChip : copy[lang].heroConfigMissingChip),
        `<span class="chip">${copy[lang].copyNext}: ${escapeHtml(state.suggestion.next_step)}</span>`
      ].join("");
      document.getElementById("healthChips").innerHTML = [
        `<span class="chip">${escapeHtml(state.cwd)}</span>`,
        `<span class="chip">${copy[lang].healthPresetPrefix}: ${escapeHtml(state.suggestion.preset)}</span>`
      ].join("");
      document.getElementById("useCaseText").textContent = state.suggestion.use_case;
      document.getElementById("metricHint").textContent = state.suggestion.metric_hint;
      document.getElementById("guardHint").textContent = state.suggestion.guard_hint;
      renderSimpleStartState(state);
      renderSimpleStarters(simplePlan.preset || state.suggestion.preset || "generic");
      const config = state.config;
      document.getElementById("configBody").innerHTML = config ? `
        <div class="small"><strong>${copy[lang].configSummaryGoal}</strong><div>${escapeHtml(config.goal)}</div></div>
        <div class="small" style="margin-top:10px;"><strong>${copy[lang].configSummaryMetric}</strong><div>${escapeHtml(config.metric)} (${escapeHtml(config.direction)})</div></div>
        <div class="small" style="margin-top:10px;"><strong>${copy[lang].configSummaryVerify}</strong><div>${escapeHtml(config.verify)}</div></div>
        <div class="small" style="margin-top:10px;"><strong>${copy[lang].configSummaryGuard}</strong><div>${escapeHtml(config.guard || copy[lang].valueNone)}</div></div>
      ` : copy[lang].noConfig;
      document.getElementById("goalInput").value = config ? config.goal : "";
      document.getElementById("metricInput").value = config ? config.metric : "";
      document.getElementById("directionInput").value = config ? config.direction : "higher";
      document.getElementById("verifyInput").value = config ? config.verify : "";
      document.getElementById("guardInput").value = config ? config.guard : "";
      document.getElementById("scopeInput").value = config ? (config.scope || []).join(", ") : "";
      document.getElementById("defaultIterationsInput").value = config && config.iterations ? config.iterations : 5;
      document.getElementById("minDeltaInput").value = config && typeof config.min_delta !== "undefined" ? config.min_delta : 0;
      renderSimplePreview(state);
      const rows = state.results || [];
      document.getElementById("resultsBody").innerHTML = rows.length ? `
        <table>
          <thead><tr><th>${copy[lang].resultsIterationHeader}</th><th>${copy[lang].resultsStatusHeader}</th><th>${copy[lang].resultsMetricHeader}</th><th>${copy[lang].resultsGuardHeader}</th></tr></thead>
          <tbody>${rows.map(row => `<tr><td>${escapeHtml(row.iteration || "-")}</td><td>${escapeHtml(statusText(row.status))}</td><td>${escapeHtml(row.metric || "-")}</td><td>${escapeHtml(row.guard || "-")}</td></tr>`).join("")}</tbody>
        </table>
      ` : copy[lang].noResults;
      renderChart(state.history || [], config ? config.direction : "higher");
      renderTimeline(state.timeline || [], state.history || []);
    }

    function renderChart(history, direction) {
      const host = document.getElementById("chartBody");
      if (!history.length) {
        host.innerHTML = `<div class="empty">${copy[lang].noResults}</div>`;
        return;
      }
      const values = history.map(item => Number(item.metric || 0));
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(max - min, 1);
      const best = direction === "lower" ? min : max;
      const worst = direction === "lower" ? max : min;
      const points = values.map((value, index) => {
        const x = 30 + (index * (520 / Math.max(values.length - 1, 1)));
        const normalized = direction === "lower" ? (worst - value) / span : (value - worst) / span;
        const y = 180 - normalized * 140;
        return `${x},${y}`;
      }).join(" ");
      const circles = values.map((value, index) => {
        const x = 30 + (index * (520 / Math.max(values.length - 1, 1)));
        const normalized = direction === "lower" ? (worst - value) / span : (value - worst) / span;
        const y = 180 - normalized * 140;
        return `<circle cx="${x}" cy="${y}" r="5" fill="#d55c3f"></circle>`;
      }).join("");
      host.innerHTML = `
        <svg class="chart" viewBox="0 0 580 220" role="img" aria-label="${copy[lang].chartAriaLabel}">
          <rect x="0" y="0" width="580" height="220" rx="18" fill="rgba(255,255,255,0.38)"></rect>
          <line x1="30" y1="180" x2="550" y2="180" stroke="rgba(30,36,48,0.14)"></line>
          <line x1="30" y1="40" x2="30" y2="180" stroke="rgba(30,36,48,0.14)"></line>
          <polyline fill="none" stroke="#18656b" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" points="${points}"></polyline>
          ${circles}
          <text x="32" y="28" fill="#5f6978" font-size="12">${copy[lang].chartBest} ${best.toFixed(2)}</text>
          <text x="32" y="200" fill="#5f6978" font-size="12">${copy[lang].chartWorst} ${worst.toFixed(2)}</text>
        </svg>
      `;
    }

    function renderTimeline(timeline, history) {
      const host = document.getElementById("timelineBody");
      const historyCards = history.slice(-6).map(item => `
        <div class="timeline-item">
          <strong>${copy[lang].timelineIteration} ${escapeHtml(item.iteration || "-")}</strong>
          <div class="small">${copy[lang].timelineStatus}: ${escapeHtml(statusText(item.status))} | ${copy[lang].timelineMetric}: ${escapeHtml(item.metric || "-")} | ${copy[lang].timelineGuard}: ${escapeHtml(item.guard || "-")}</div>
          <div class="small">${escapeHtml(item.summary || "")}</div>
        </div>
      `);
      const runCards = timeline.slice(-6).reverse().map(item => `
        <div class="timeline-item">
          <strong>${escapeHtml(item.name)}</strong>
          <div class="small">${escapeHtml(item.updatedAt)}</div>
          <div class="small">${escapeHtml(item.stderr || item.stdout || "")}</div>
          <div class="small">
            ${item.stderr ? `<button class="linkish" type="button" data-log="${escapeHtml(item.stderr)}">${copy[lang].timelineStderr}</button>` : ""}
            ${item.stdout ? `<button class="linkish" type="button" data-log="${escapeHtml(item.stdout)}">${copy[lang].timelineStdout}</button>` : ""}
          </div>
        </div>
      `);
      const cards = [...historyCards, ...runCards];
      host.innerHTML = cards.length ? cards.join("") : `<div class="empty">${copy[lang].noTimeline}</div>`;
      document.querySelectorAll("[data-log]").forEach(node => {
        node.onclick = () => loadLog(node.dataset.log);
      });
    }

    function renderSelectedTask(tasks) {
      const terminal = document.getElementById("terminal");
      const task = tasks.find(item => item.id === selectedTaskId) || tasks[0];
      if (!task) {
        terminal.textContent = copy[lang].noTask;
        return;
      }
      selectedTaskId = task.id;
      terminal.textContent = task.output || `${taskLabel(task.label)} ${copy[lang].taskStartedSuffix}`;
      document.querySelectorAll(".task").forEach(node => {
        node.classList.toggle("active", node.dataset.id === selectedTaskId);
      });
    }

    function taskIterations(task) {
      if (!task || !Array.isArray(task.command)) {
        return document.getElementById("simpleIterationsInput").value || "5";
      }
      const index = task.command.indexOf("--iterations");
      if (index >= 0 && task.command[index + 1]) {
        return task.command[index + 1];
      }
      return document.getElementById("simpleIterationsInput").value || "5";
    }

    function renderSimpleProgress(tasks) {
      const summary = document.getElementById("simpleProgressSummary");
      const currentTask = document.getElementById("simpleCurrentTask");
      const terminal = document.getElementById("simpleTerminal");
      const applyButton = document.getElementById("simpleApplyBtn");
      const actionHint = document.getElementById("simpleActionHint");
      const task = tasks.find(item => item.status === "running" || item.status === "stopping") || tasks[0];
      if (!task) {
        summary.textContent = copy[lang].simpleIdle;
        currentTask.textContent = copy[lang].simpleTaskIdle;
        terminal.textContent = copy[lang].noTask;
        if (applyButton) applyButton.disabled = true;
        if (actionHint) actionHint.textContent = "";
        renderSimpleStartState(window._state || {});
        return;
      }
      const stopAt = task.stop_at ? ` · ${copy[lang].simpleRunningUntil} ${task.stop_at}` : "";
      const command = Array.isArray(task.command) ? task.command.join(" ") : "";
      summary.textContent = `${statusText(task.status)}${stopAt}`;
      currentTask.textContent = `${copy[lang].simpleTaskPrefix} ${task.id} · ${taskLabel(task.label)} · ${copy[lang].simpleRounds}: ${taskIterations(task)} · ${task.cwd || ""}`;
      terminal.textContent = task.output || command || `${taskLabel(task.label)} ${copy[lang].taskStartedSuffix}`;
      if (applyButton) {
        applyButton.disabled = !(task.patch_path && task.import_status !== "applied");
      }
      if (actionHint) {
        if (task.import_status === "ready" && task.patch_path) {
          actionHint.textContent = copy[lang].simpleApplyReady;
        } else if (task.import_status === "applied") {
          actionHint.textContent = copy[lang].simpleApplyDone;
        } else {
          actionHint.textContent = "";
        }
      }
      renderSimpleStartState(window._state || {});
    }

    async function refreshState() {
      const state = await api("/api/state");
      window._state = state;
      renderState(state);
    }

    function renderTaskList(tasks) {
      document.getElementById("taskList").innerHTML = tasks.map(task => `
        <button class="task ${task.id === selectedTaskId ? "active" : ""}" data-id="${task.id}" type="button">
          <div><strong>${escapeHtml(taskLabel(task.label))}</strong><div class="small">${escapeHtml(task.command.join(" "))}</div></div>
          <div>${pill(taskStatusKind(task.status), statusText(task.status))}</div>
        </button>
      `).join("");
      document.querySelectorAll(".task").forEach(node => {
        node.onclick = () => {
          selectedTaskId = node.dataset.id;
          renderSelectedTask(tasks);
        };
      });
    }

    async function refreshTasks(selectLatest = false, payloadOverride = null) {
      const payload = payloadOverride || await api("/api/tasks");
      const tasks = payload.tasks || [];
      window._tasks = tasks;
      if (selectLatest && tasks[0]) selectedTaskId = tasks[0].id;
      renderTaskList(tasks);
      renderSelectedTask(tasks);
      renderSimpleProgress(tasks);
    }

    document.querySelectorAll("[data-lang]").forEach(button => {
      button.onclick = () => setLang(button.dataset.lang);
    });
    document.querySelectorAll("[data-mode]").forEach(button => {
      button.onclick = () => setMode(button.dataset.mode);
    });
    document.querySelectorAll("[data-action]").forEach(button => {
      button.onclick = () => runAction(button.dataset.action);
    });
    document.getElementById("simpleStartBtn").onclick = () => runSimpleStart();
    document.getElementById("simpleStopBtn").onclick = () => stopCurrentRun();
    document.getElementById("simpleApplyBtn").onclick = () => applyBestResult();
    document.getElementById("simpleGoalInput").addEventListener("input", () => renderSimplePreview(window._state || {}));
    document.getElementById("simpleIterationsInput").addEventListener("input", () => renderSimplePreview(window._state || {}));
    document.getElementById("simpleStopAtInput").addEventListener("input", () => renderSimplePreview(window._state || {}));
    document.getElementById("saveConfigBtn").onclick = () => saveConfig();
    document.getElementById("presetPythonBtn").onclick = () => loadPreset("python");
    document.getElementById("presetNodeBtn").onclick = () => loadPreset("node");
    document.getElementById("presetGenericBtn").onclick = () => loadPreset("generic");
    const events = new EventSource("/api/tasks/stream");
    events.onmessage = (event) => {
      try {
        refreshTasks(false, JSON.parse(event.data));
      } catch (error) {}
    };
    setLang("en");
    setMode("beginner");
    refreshState();
    refreshTasks();
    document.getElementById("logViewer").textContent = copy[lang].noLog;
    setInterval(refreshState, 3000);
  </script>
</body>
</html>"""
