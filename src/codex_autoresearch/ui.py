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
        "codexInstalled": shutil.which("codex") is not None,
        "suggestion": suggestion,
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
    status: str
    output: str
    started_at: str
    ended_at: str | None = None


class TaskStore:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._next_id = 1

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(task) for task in sorted(self._tasks.values(), key=lambda item: item.id, reverse=True)]

    def snapshot_json(self) -> str:
        return json.dumps({"tasks": self.list()}, sort_keys=True)

    def start(self, label: str, command: list[str]) -> dict[str, Any]:
        with self._lock:
            task_id = f"task-{self._next_id:03d}"
            self._next_id += 1
            task = Task(
                id=task_id,
                label=label,
                command=command,
                status="running",
                output="",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            self._tasks[task_id] = task
        thread = threading.Thread(target=self._run_task, args=(task_id,), daemon=True)
        thread.start()
        return asdict(task)

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            command = list(task.command)

        env = os.environ.copy()
        src_path = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        process = subprocess.Popen(
            [sys.executable, "-m", "codex_autoresearch.cli", *command],
            cwd=self.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        chunks: list[str] = []
        for line in process.stdout:
            chunks.append(line)
            with self._lock:
                self._tasks[task_id].output = "".join(chunks)[-50000:]
        returncode = process.wait()
        with self._lock:
            self._tasks[task_id].status = "done" if returncode == 0 else "failed"
            self._tasks[task_id].ended_at = datetime.now(timezone.utc).isoformat()
            self._tasks[task_id].output = "".join(chunks)[-50000:]


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
      --bg: #f4efe6;
      --panel: rgba(255, 250, 243, 0.82);
      --panel-strong: rgba(255, 248, 238, 0.96);
      --ink: #1e2430;
      --muted: #5f6978;
      --line: rgba(30, 36, 48, 0.1);
      --accent: #d55c3f;
      --accent-2: #18656b;
      --gold: #c58b2a;
      --shadow: 0 24px 70px rgba(58, 42, 24, 0.12);
      --radius: 26px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(213, 92, 63, 0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(24, 101, 107, 0.18), transparent 30%),
        linear-gradient(180deg, #f8f3ea 0%, #f2ecdf 100%);
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
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      background-image:
        linear-gradient(rgba(30,36,48,0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(30,36,48,0.03) 1px, transparent 1px);
      background-size: 34px 34px;
      pointer-events: none;
      mask-image: linear-gradient(180deg, rgba(0,0,0,0.7), transparent);
    }
    .shell {
      width: min(1260px, calc(100vw - 32px));
      margin: 24px auto 48px;
      position: relative;
      z-index: 1;
    }
    .hero, .panel {
      background: var(--panel);
      backdrop-filter: blur(18px);
      border: 1px solid rgba(255,255,255,0.65);
      box-shadow: var(--shadow);
      border-radius: var(--radius);
    }
    .hero {
      padding: 28px;
      overflow: hidden;
      position: relative;
      animation: rise 0.55s ease;
    }
    .hero::after {
      content: "";
      position: absolute;
      width: 240px;
      height: 240px;
      right: -60px;
      top: -80px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(197,139,42,0.42), rgba(197,139,42,0));
    }
    .eyebrow {
      display: inline-flex;
      gap: 10px;
      align-items: center;
      padding: 8px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,0.65);
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent-2);
    }
    h1, h2 {
      font-family: "Iowan Old Style", "Palatino Linotype", serif;
      margin: 0;
      letter-spacing: -0.03em;
      text-wrap: balance;
    }
    h1 {
      font-size: clamp(36px, 6vw, 64px);
      line-height: 0.95;
      margin-top: 18px;
      max-width: 9ch;
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
      font-size: 17px;
      line-height: 1.6;
      max-width: 52ch;
    }
    .lang-toggle {
      display: inline-flex;
      gap: 8px;
      padding: 6px;
      background: rgba(255,255,255,0.72);
      border-radius: 999px;
      border: 1px solid rgba(30,36,48,0.08);
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
      background: rgba(255,255,255,0.72);
      border-radius: 999px;
      border: 1px solid rgba(30,36,48,0.08);
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
      font-weight: 700;
    }
    .mode-toggle button.active {
      background: var(--accent-2);
      color: white;
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
      font-weight: 700;
    }
    .lang-toggle button.active {
      background: var(--ink);
      color: white;
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
      padding: 22px;
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
    .guide-card {
      padding: 18px;
      border-radius: 20px;
      background: rgba(255,255,255,0.76);
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
      margin-top: 14px;
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
      border-radius: 18px;
      padding: 14px 16px;
      font-size: 15px;
      font-weight: 700;
      text-align: left;
      box-shadow: 0 12px 24px rgba(30,36,48,0.08);
      touch-action: manipulation;
    }
    .action {
      background: linear-gradient(135deg, var(--ink), #38475f);
      color: white;
    }
    .ghost {
      background: rgba(255,255,255,0.8);
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
      background: #181c24;
      color: #ecf3ff;
      border-radius: 20px;
      padding: 16px;
      min-height: 280px;
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
      .actions, .form-grid, .stats, .config-grid, .guide-grid { grid-template-columns: 1fr; }
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
        <div class="stats">
          <div class="stat"><label id="repoLabel">Repository</label><strong id="repoName">-</strong></div>
          <div class="stat"><label id="presetLabel">Preset</label><strong id="presetName">-</strong></div>
          <div class="stat"><label id="configLabel">Config</label><strong id="configStatus">-</strong></div>
          <div class="stat"><label id="runLabel">Latest Run</label><strong id="runName">-</strong></div>
        </div>
      </div>
    </section>
    <section class="grid">
      <div class="panel span-12">
        <h2 id="guideTitle">Start here if you are new</h2>
        <div class="guide-grid">
          <div class="guide-card">
            <div class="small" id="guideIntro">You do not need to understand everything. Just follow these three steps.</div>
            <div class="step-list">
              <div class="step-item"><span class="step-num">1</span><div><strong id="guideStep1Title">Fill a preset</strong><div class="small" id="guideStep1Body">Pick Python, Node, or Generic in the config editor.</div></div></div>
              <div class="step-item"><span class="step-num">2</span><div><strong id="guideStep2Title">Save the config</strong><div class="small" id="guideStep2Body">Check goal, metric, and verify command, then save.</div></div></div>
              <div class="step-item"><span class="step-num">3</span><div><strong id="guideStep3Title">Click the big start button</strong><div class="small" id="guideStep3Body">Use Repair Setup Gaps first if this repo is new.</div></div></div>
            </div>
          </div>
          <div class="guide-card">
            <div class="small" id="nextActionLabel">Recommended next click</div>
            <h2 id="nextActionTitle" style="margin-top:8px;">Repair Setup Gaps</h2>
            <div class="small" id="nextActionBody">This is usually the safest first click in a new repository.</div>
            <div class="toolbar">
              <button class="action" id="nextActionBtn" type="button">Do the next step</button>
            </div>
            <div class="small" id="glossaryTitle" style="margin-top:12px;"><strong>Simple glossary</strong></div>
            <div class="small" id="glossaryBody">Metric = the number you want to improve. Verify = the command that prints that number. Guard = a safety check like tests.</div>
          </div>
        </div>
      </div>
      <div class="panel span-7">
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
      <div class="panel span-6">
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
        title: "Run your repo like a measured Codex lab.",
        summary: "A local control room for setup, nightly workflows, and bounded Codex runs. No YAML hunting. No command memorizing.",
        repoLabel: "Repository",
        presetLabel: "Preset",
        configLabel: "Config",
        runLabel: "Latest Run",
        modeBeginnerBtn: "Simple",
        modeAdvancedBtn: "Advanced",
        guideTitle: "Start here if you are new",
        guideIntro: "You only need to do three things: choose a project type, save, and click start.",
        guideStep1Title: "Choose what kind of repo this is",
        guideStep1Body: "Most people should click Python or Node. If you are not sure, click I am not sure.",
        guideStep2Title: "Save",
        guideStep2Body: "You usually do not need to edit anything before the first save.",
        guideStep3Title: "Click the recommended button",
        guideStep3Body: "The page will tell you the safest next click.",
        nextActionLabel: "Recommended next click",
        nextActionTitle: "Repair Setup Gaps",
        nextActionBody: "This is usually the safest first click in a new repository.",
        nextActionBtn: "Do the next step",
        glossaryTitle: "Simple glossary",
        glossaryBody: "Metric = the number you want to improve. Verify = the command that prints that number. Guard = a safety check like tests.",
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
        saveConfig: "Save config",
        saveDone: "Config saved.",
        saveFailed: "Could not save config.",
        copyNext: "Suggested next command"
      },
      zh: {
        eyebrow: "Codex Autoresearch 控制台",
        title: "把仓库当成一间可量化的 Codex 实验室来跑。",
        summary: "这是一个本地操作台，负责 setup、nightly workflow 和有边界的 Codex 运行。少写 YAML，少背命令。",
        repoLabel: "仓库",
        presetLabel: "预设",
        configLabel: "配置",
        runLabel: "最近运行",
        modeBeginnerBtn: "简单模式",
        modeAdvancedBtn: "高级模式",
        guideTitle: "第一次用就从这里开始",
        guideIntro: "你只需要做三件事：选项目类型、保存、再点开始。",
        guideStep1Title: "先选你的项目类型",
        guideStep1Body: "大多数人点 Python 或 Node 就行。拿不准就点“我也不确定”。",
        guideStep2Title: "先保存",
        guideStep2Body: "第一次通常不用改太多，先保存就可以。",
        guideStep3Title: "再点页面推荐按钮",
        guideStep3Body: "页面会告诉你当前最安全的下一步。",
        nextActionLabel: "建议你下一步先点",
        nextActionTitle: "修复准备缺项",
        nextActionBody: "对一个新仓库来说，这通常是最安全的第一步。",
        nextActionBtn: "执行下一步",
        glossaryTitle: "白话解释",
        glossaryBody: "Metric 就是你想变好的数字。Verify 就是输出这个数字的命令。Guard 就是像测试这样的安全检查。",
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
        saveConfig: "保存配置",
        saveDone: "配置已保存。",
        saveFailed: "配置保存失败。",
        copyNext: "建议下一步命令"
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
      const nextButton = document.getElementById("nextActionBtn");
      if (nextButton) nextButton.textContent = text.nextActionBtn;
      document.querySelectorAll("[data-mode]").forEach(button => {
        const key = button.id;
        if (text[key]) button.textContent = text[key];
      });
      renderSelectedTask(window._tasks || []);
    }

    function pill(ok, label) {
      const kind = ok === true ? "ok" : ok === false ? "bad" : "warn";
      return `<span class="pill ${kind}">${label}</span>`;
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      return response.json();
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
      await api("/api/actions", {
        method: "POST",
        body: JSON.stringify(buildPayload(action))
      });
      await refreshTasks(true);
    }

    function computeRecommendedAction(state) {
      if (!state.configExists) return "doctor_fix";
      if (!state.gitExists) return "doctor_fix";
      if (!state.results || !state.results.length) return "start";
      return "start";
    }

    function actionLabel(action) {
      const map = {
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
      document.getElementById("runName").textContent = state.latestRun || "none";
      document.getElementById("heroChips").innerHTML = [
        pill(state.gitExists, state.gitExists ? "git ready" : "git missing"),
        pill(state.codexInstalled, state.codexInstalled ? "codex ready" : "codex missing"),
        pill(state.configExists, state.configExists ? "config ready" : "config missing"),
        `<span class="chip">${copy[lang].copyNext}: ${state.suggestion.next_step}</span>`
      ].join("");
      document.getElementById("healthChips").innerHTML = [
        `<span class="chip">${state.cwd}</span>`,
        `<span class="chip">preset: ${state.suggestion.preset}</span>`
      ].join("");
      document.getElementById("useCaseText").textContent = state.suggestion.use_case;
      document.getElementById("metricHint").textContent = state.suggestion.metric_hint;
      document.getElementById("guardHint").textContent = state.suggestion.guard_hint;
      const nextAction = computeRecommendedAction(state);
      document.getElementById("nextActionTitle").textContent = actionLabel(nextAction);
      document.getElementById("nextActionBody").textContent = nextAction === "doctor_fix"
        ? (lang === "zh" ? "先让工具自动修环境和初始化缺项，然后再继续。" : "Let the tool fix missing setup first, then continue.")
        : (lang === "zh" ? "环境已经差不多了，现在可以开始第一轮运行。" : "The repo looks ready enough. You can start the first bounded run now.");
      document.getElementById("nextActionBtn").onclick = () => runAction(nextAction);
      const config = state.config;
      document.getElementById("configBody").innerHTML = config ? `
        <div class="small"><strong>Goal</strong><div>${config.goal}</div></div>
        <div class="small" style="margin-top:10px;"><strong>Metric</strong><div>${config.metric} (${config.direction})</div></div>
        <div class="small" style="margin-top:10px;"><strong>Verify</strong><div>${config.verify}</div></div>
        <div class="small" style="margin-top:10px;"><strong>Guard</strong><div>${config.guard || "none"}</div></div>
      ` : copy[lang].noConfig;
      document.getElementById("goalInput").value = config ? config.goal : "";
      document.getElementById("metricInput").value = config ? config.metric : "";
      document.getElementById("directionInput").value = config ? config.direction : "higher";
      document.getElementById("verifyInput").value = config ? config.verify : "";
      document.getElementById("guardInput").value = config ? config.guard : "";
      document.getElementById("scopeInput").value = config ? (config.scope || []).join(", ") : "";
      document.getElementById("defaultIterationsInput").value = config && config.iterations ? config.iterations : 5;
      document.getElementById("minDeltaInput").value = config && typeof config.min_delta !== "undefined" ? config.min_delta : 0;
      const rows = state.results || [];
      document.getElementById("resultsBody").innerHTML = rows.length ? `
        <table>
          <thead><tr><th>Iteration</th><th>Status</th><th>Metric</th><th>Guard</th></tr></thead>
          <tbody>${rows.map(row => `<tr><td>${row.iteration || "-"}</td><td>${row.status || "-"}</td><td>${row.metric || "-"}</td><td>${row.guard || "-"}</td></tr>`).join("")}</tbody>
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
        <svg class="chart" viewBox="0 0 580 220" role="img" aria-label="Metric history chart">
          <rect x="0" y="0" width="580" height="220" rx="18" fill="rgba(255,255,255,0.38)"></rect>
          <line x1="30" y1="180" x2="550" y2="180" stroke="rgba(30,36,48,0.14)"></line>
          <line x1="30" y1="40" x2="30" y2="180" stroke="rgba(30,36,48,0.14)"></line>
          <polyline fill="none" stroke="#18656b" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" points="${points}"></polyline>
          ${circles}
          <text x="32" y="28" fill="#5f6978" font-size="12">best ${best.toFixed(2)}</text>
          <text x="32" y="200" fill="#5f6978" font-size="12">worst ${worst.toFixed(2)}</text>
        </svg>
      `;
    }

    function renderTimeline(timeline, history) {
      const host = document.getElementById("timelineBody");
      const historyCards = history.slice(-6).map(item => `
        <div class="timeline-item">
          <strong>Iteration ${item.iteration || "-"}</strong>
          <div class="small">status: ${item.status || "-"} | metric: ${item.metric || "-"} | guard: ${item.guard || "-"}</div>
          <div class="small">${item.summary || ""}</div>
        </div>
      `);
      const runCards = timeline.slice(-6).reverse().map(item => `
        <div class="timeline-item">
          <strong>${item.name}</strong>
          <div class="small">${item.updatedAt}</div>
          <div class="small">${item.stderr || item.stdout || ""}</div>
          <div class="small">
            ${item.stderr ? `<button class="linkish" type="button" data-log="${item.stderr}">stderr</button>` : ""}
            ${item.stdout ? `<button class="linkish" type="button" data-log="${item.stdout}">stdout</button>` : ""}
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
      terminal.textContent = task.output || `${task.label} started...`;
      document.querySelectorAll(".task").forEach(node => {
        node.classList.toggle("active", node.dataset.id === selectedTaskId);
      });
    }

    async function refreshState() {
      const state = await api("/api/state");
      window._state = state;
      renderState(state);
    }

    async function refreshTasks(selectLatest = false, payloadOverride = null) {
      const payload = payloadOverride || await api("/api/tasks");
      const tasks = payload.tasks || [];
      window._tasks = tasks;
      if (selectLatest && tasks[0]) selectedTaskId = tasks[0].id;
      document.getElementById("taskList").innerHTML = tasks.map(task => `
        <button class="task ${task.id === selectedTaskId ? "active" : ""}" data-id="${task.id}" type="button">
          <div><strong>${task.label}</strong><div class="small">${task.command.join(" ")}</div></div>
          <div>${pill(task.status === "done", task.status)}</div>
        </button>
      `).join("");
      document.querySelectorAll(".task").forEach(node => {
        node.onclick = () => {
          selectedTaskId = node.dataset.id;
          renderSelectedTask(tasks);
        };
      });
      renderSelectedTask(tasks);
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
