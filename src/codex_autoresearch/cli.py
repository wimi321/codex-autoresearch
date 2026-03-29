from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from .config import ResearchConfig, detect_preset, template_for_preset
from .runner import ResearchRunner, default_branch_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autore", description="Codex-native autonomous research loops")
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start", help="one-command happy path: init, doctor, run")
    start_parser.add_argument("--config", default="autoresearch.toml")
    start_parser.add_argument("--preset", choices=["auto", "python", "node", "generic"], default="auto")
    start_parser.add_argument("--iterations", type=int, default=5)
    start_parser.add_argument("--resume", action="store_true")
    start_parser.add_argument("--skip-branch", action="store_true")
    start_parser.add_argument("--branch")
    start_parser.add_argument("--demo", action="store_true")
    start_parser.add_argument("--demo-dir", default=".autoresearch-demo")

    init_parser = sub.add_parser("init", help="write a starter autoresearch.toml")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--preset", choices=["auto", "python", "node", "generic"], default="auto")

    run_parser = sub.add_parser("run", help="run a bounded autoresearch loop")
    run_parser.add_argument("--config", default="autoresearch.toml")
    run_parser.add_argument("--iterations", type=int)
    run_parser.add_argument("--branch")
    run_parser.add_argument("--skip-branch", action="store_true")
    run_parser.add_argument("--resume", action="store_true")

    status_parser = sub.add_parser("status", help="show the latest results log")
    status_parser.add_argument("--config", default="autoresearch.toml")

    doctor_parser = sub.add_parser("doctor", help="check whether this repo is ready to run")
    doctor_parser.add_argument("--config", default="autoresearch.toml")

    watch_parser = sub.add_parser("watch", help="watch the latest run logs or results")
    watch_parser.add_argument("--config", default="autoresearch.toml")
    watch_parser.add_argument("--stream", choices=["stderr", "stdout", "results"], default="stderr")
    watch_parser.add_argument("--follow", action="store_true")
    watch_parser.add_argument("--interval", type=float, default=1.0)
    watch_parser.add_argument("--lines", type=int, default=40)

    return parser


def cmd_init(force: bool, preset: str) -> int:
    path = Path("autoresearch.toml")
    if path.exists() and not force:
        print("autoresearch.toml already exists. Use --force to overwrite.", file=sys.stderr)
        return 1
    resolved = detect_preset(Path.cwd()) if preset == "auto" else preset
    path.write_text(template_for_preset(resolved))
    print(f"Wrote autoresearch.toml using '{resolved}' preset")
    return 0


def cmd_start(
    config_path: str,
    preset: str,
    iterations: int,
    resume: bool,
    skip_branch: bool,
    branch: str | None,
    demo: bool,
    demo_dir: str,
) -> int:
    if demo:
        return cmd_start_demo(demo_dir)

    config = Path(config_path)
    if not config.exists():
        print("[autore] no config found, generating one for this repo")
        original = Path("autoresearch.toml")
        target = config.name
        if target != original.name:
            Path(target).write_text(template_for_preset(detect_preset(Path.cwd()) if preset == "auto" else preset))
            print(f"Wrote {target}")
        else:
            result = cmd_init(force=False, preset=preset)
            if result != 0:
                return result

    doctor_result = cmd_doctor(config_path)
    if doctor_result != 0:
        return doctor_result

    return cmd_run(
        config_path,
        iterations_override=iterations,
        branch=branch,
        skip_branch=skip_branch,
        resume=resume,
    )


def cmd_start_demo(demo_dir: str) -> int:
    source = Path(__file__).resolve().parents[2] / "examples" / "demo-repo"
    target = Path(demo_dir).resolve()
    if target.exists():
        print(f"Demo directory already exists: {target}", file=sys.stderr)
        return 1

    shutil.copytree(source, target)
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, text=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "demo"], cwd=target, check=True, text=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=target, check=True, text=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True, text=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init demo"], cwd=target, check=True, text=True, capture_output=True)

    print(f"[autore] demo created at {target}")
    print(f"[autore] next: cd {target}")
    print("[autore] then run: autore start --resume --skip-branch")
    return 0


def cmd_run(
    config_path: str,
    iterations_override: int | None,
    branch: str | None,
    skip_branch: bool,
    resume: bool,
) -> int:
    config = ResearchConfig.load(config_path)
    runner = ResearchRunner(Path.cwd(), config)
    target_iterations = iterations_override or config.iterations
    if not target_iterations:
        print("Iterations must be set in config or passed with --iterations.", file=sys.stderr)
        return 1
    branch_name = None if skip_branch or resume else (branch or default_branch_name(config.branch_prefix))
    print(f"[autore] goal: {config.goal}")
    print(f"[autore] metric: {config.metric} ({config.metric_direction_label()})")
    print(f"[autore] verify: {config.verify}")
    print(f"[autore] guard: {config.guard or 'none'}")
    runner.ensure_setup(branch_name=branch_name, allow_resume=resume)

    state = runner.resume_state() if resume else None
    if state is None:
        baseline = runner.establish_baseline()
        start_iteration = 1
    else:
        last_iteration, baseline = state
        start_iteration = last_iteration + 1
        print(f"[autore] resuming from iteration {start_iteration} with best metric {baseline:.6f}")

    best = runner.run(target_iterations, baseline, start_iteration=start_iteration)
    print(f"Baseline: {baseline:.6f}")
    print(f"Best: {best:.6f}")
    print(f"Results log: {config.log_tsv}")
    return 0


def cmd_status(config_path: str) -> int:
    config = ResearchConfig.load(config_path)
    path = Path(config.log_tsv)
    if not path.exists():
        print(f"No results log found at {path}", file=sys.stderr)
        return 1
    print(path.read_text().rstrip())
    return 0


def cmd_doctor(config_path: str) -> int:
    issues: list[str] = []
    cwd = Path.cwd()

    if not (cwd / ".git").exists():
        issues.append("git repo missing: run 'git init'")
    if shutil.which("codex") is None:
        issues.append("Codex CLI missing: install 'codex'")
    if not Path(config_path).exists():
        issues.append("autoresearch.toml missing: run 'autore init'")

    if issues:
        print("Autoresearch doctor found issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    config = ResearchConfig.load(config_path)
    print("Autoresearch doctor is happy:")
    print(f"- config: {config_path}")
    print(f"- goal: {config.goal}")
    print(f"- metric: {config.metric} ({config.metric_direction_label()})")
    print(f"- verify: {config.verify}")
    print(f"- guard: {config.guard or 'none'}")
    return 0


def cmd_watch(config_path: str, stream: str, follow: bool, interval: float, lines: int) -> int:
    config = ResearchConfig.load(config_path)
    runner = ResearchRunner(Path.cwd(), config)

    if stream == "results":
        path = runner.log_path
    else:
        latest = runner.latest_run_dir()
        if latest is None:
            print("No run directory found yet. Start `autore run` first.", file=sys.stderr)
            return 1
        name = "codex.stderr.log" if stream == "stderr" else "codex.stdout.log"
        path = latest / name

    return runner.watch_file(path, follow=follow, interval_seconds=interval, lines=lines)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "start":
        return cmd_start(
            args.config,
            args.preset,
            args.iterations,
            args.resume,
            args.skip_branch,
            args.branch,
            args.demo,
            args.demo_dir,
        )
    if args.command == "init":
        return cmd_init(args.force, args.preset)
    if args.command == "run":
        return cmd_run(args.config, args.iterations, args.branch, args.skip_branch, args.resume)
    if args.command == "status":
        return cmd_status(args.config)
    if args.command == "doctor":
        return cmd_doctor(args.config)
    if args.command == "watch":
        return cmd_watch(args.config, args.stream, args.follow, args.interval, args.lines)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
