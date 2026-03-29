from __future__ import annotations

import argparse
import builtins
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .config import ResearchConfig, detect_preset, template_for_preset
from .gittools import ensure_gitignore_has
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
    start_parser.add_argument("--run", action="store_true", help="when used with --demo, run the demo immediately")

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
    doctor_parser.add_argument("--fix", action="store_true")

    watch_parser = sub.add_parser("watch", help="watch the latest run logs or results")
    watch_parser.add_argument("--config", default="autoresearch.toml")
    watch_parser.add_argument("--stream", choices=["stderr", "stdout", "results"], default="stderr")
    watch_parser.add_argument("--follow", action="store_true")
    watch_parser.add_argument("--interval", type=float, default=1.0)
    watch_parser.add_argument("--lines", type=int, default=40)

    quickstart_parser = sub.add_parser("quickstart", help="guided first-run setup")
    quickstart_parser.add_argument("--demo-dir", default=".autoresearch-demo")

    onboard_parser = sub.add_parser("onboard", help="repo-first setup guide with optional file generation")
    onboard_parser.add_argument("--config", default="autoresearch.toml")
    onboard_parser.add_argument("--write-nightly", action="store_true")
    onboard_parser.add_argument("--workflow-path", default=".github/workflows/autoresearch-nightly.yml")
    onboard_parser.add_argument("--iterations", type=int, default=5)
    onboard_parser.add_argument("--force", action="store_true")

    nightly_parser = sub.add_parser("nightly", help="generate a GitHub Actions workflow for scheduled runs")
    nightly_parser.add_argument("--config", default="autoresearch.toml")
    nightly_parser.add_argument("--workflow-path", default=".github/workflows/autoresearch-nightly.yml")
    nightly_parser.add_argument("--iterations", type=int, default=5)
    nightly_parser.add_argument("--python-version", default="3.11")
    nightly_parser.add_argument("--branch", default="main")
    nightly_parser.add_argument("--force", action="store_true")

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
    run_demo: bool,
) -> int:
    if demo:
        return cmd_start_demo(demo_dir, run_demo=run_demo, iterations=iterations)

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

    print("[autore] checking repo setup and auto-fixing obvious gaps")
    doctor_result = cmd_doctor(config_path, fix=True)
    if doctor_result != 0:
        return doctor_result

    return cmd_run(
        config_path,
        iterations_override=iterations,
        branch=branch,
        skip_branch=skip_branch,
        resume=resume,
    )


def cmd_start_demo(demo_dir: str, *, run_demo: bool, iterations: int) -> int:
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
    if not run_demo:
        print(f"[autore] next: cd {target}")
        print("[autore] then run: autore start --resume --skip-branch")
        return 0

    print(f"[autore] running demo in {target}")
    previous = Path.cwd()
    try:
        os.chdir(target)
        result = cmd_run(
            "autoresearch.toml",
            iterations_override=iterations,
            branch=None,
            skip_branch=True,
            resume=True,
        )
        if result == 0:
            _print_demo_summary(target)
        return result
    finally:
        os.chdir(previous)


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


def cmd_doctor(config_path: str, fix: bool = False) -> int:
    issues: list[str] = []
    cwd = Path.cwd()
    fixed: list[str] = []

    if not (cwd / ".git").exists():
        if fix:
            subprocess.run(["git", "init", "-b", "main"], cwd=cwd, check=True, text=True, capture_output=True)
            fixed.append("initialized git repository")
        else:
            issues.append("git repo missing: run 'git init'")
    if shutil.which("codex") is None:
        issues.append("Codex CLI missing: install 'codex'")
    if not Path(config_path).exists():
        if fix:
            result = cmd_init(force=False, preset="auto")
            if result == 0:
                fixed.append(f"created {config_path}")
        else:
            issues.append("autoresearch.toml missing: run 'autore init'")
    if fix:
        ensure_gitignore_has(cwd, [".autoresearch/"])
        fixed.append("ensured .autoresearch/ is ignored")

    if fixed:
        print("Autoresearch doctor applied fixes:")
        for item in fixed:
            print(f"- {item}")

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
    suggestion = suggest_repo_defaults(cwd, config=config)
    print(f"- suggested preset: {suggestion['preset']}")
    print(f"- suggested use case: {suggestion['use_case']}")
    print(f"- next step: {suggestion['next_step']}")
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


def cmd_quickstart(demo_dir: str) -> int:
    print("Codex Autoresearch quickstart")
    use_demo = _ask_yes_no("Run the built-in demo first?", default=True)
    if use_demo:
        run_now = _ask_yes_no("Run the demo immediately after creating it?", default=True)
        iterations = _ask_int("How many demo iterations?", default=1)
        return cmd_start(
            config_path="autoresearch.toml",
            preset="auto",
            iterations=iterations,
            resume=False,
            skip_branch=True,
            branch=None,
            demo=True,
            demo_dir=demo_dir,
            run_demo=run_now,
        )

    suggestion = suggest_repo_defaults(Path.cwd())
    print(f"Recommended preset: {suggestion['preset']}")
    print(f"Suggested metric pattern: {suggestion['metric_hint']}")
    print(f"Suggested guard: {suggestion['guard_hint']}")
    print(f"Suggested next command: {suggestion['next_step']}")
    iterations = _ask_int("How many iterations for this repo?", default=5)
    resume = _ask_yes_no("Resume an existing autoresearch branch?", default=False)
    return cmd_start(
        config_path="autoresearch.toml",
        preset=suggestion["preset"],
        iterations=iterations,
        resume=resume,
        skip_branch=resume,
        branch=None,
        demo=False,
        demo_dir=demo_dir,
        run_demo=False,
    )


def cmd_onboard(
    config_path: str,
    workflow_path: str,
    iterations: int,
    write_nightly: bool,
    force: bool,
) -> int:
    print("Codex Autoresearch onboarding")
    doctor_result = cmd_doctor(config_path, fix=True)
    if doctor_result != 0:
        return doctor_result

    config = ResearchConfig.load(config_path)
    suggestion = suggest_repo_defaults(Path.cwd(), config=config)
    print("")
    print("This repo is ready.")
    print(f"- detected preset: {suggestion['preset']}")
    print(f"- best fit: {suggestion['use_case']}")
    print(f"- metric hint: {suggestion['metric_hint']}")
    print(f"- guard hint: {suggestion['guard_hint']}")
    print(f"- recommended run: autore start --iterations {iterations}")

    if write_nightly:
        nightly_result = cmd_nightly(
            config_path=config_path,
            workflow_path=workflow_path,
            iterations=iterations,
            python_version="3.11",
            branch="main",
            force=force,
        )
        if nightly_result != 0:
            return nightly_result

    print("")
    print("Copy next:")
    print(f"1. autore start --iterations {iterations}")
    print("2. autore watch --follow")
    if write_nightly:
        print(f"3. git add {workflow_path} && git commit -m \"chore: add autoresearch nightly workflow\"")
    return 0


def cmd_nightly(
    config_path: str,
    workflow_path: str,
    iterations: int,
    python_version: str,
    branch: str,
    force: bool,
) -> int:
    config = ResearchConfig.load(config_path)
    target = Path(workflow_path)
    if target.exists() and not force:
        print(f"Workflow already exists: {target}. Use --force to overwrite.", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_nightly_workflow(config_path, config, iterations, python_version, branch))
    print(f"Wrote nightly workflow: {target}")
    print("- schedule: every day at 01:00 UTC")
    print(f"- iterations per run: {iterations}")
    print("- artifact upload: enabled")
    print("- next: git add .github/workflows && git commit")
    return 0


def _ask_yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = builtins.input(f"{prompt} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _ask_int(prompt: str, *, default: int) -> int:
    answer = builtins.input(f"{prompt} [{default}] ").strip()
    if not answer:
        return default
    return int(answer)


def suggest_repo_defaults(cwd: Path, config: ResearchConfig | None = None) -> dict[str, str]:
    preset = detect_preset(cwd)
    if preset == "generic" and config is not None:
        preset = _infer_preset_from_config(config)
    if preset == "python":
        return {
            "preset": "python",
            "metric_hint": "pytest coverage or collected tests",
            "guard_hint": "pytest",
            "use_case": "test coverage, bug-fix loops, type-safe refactors",
            "next_step": "autore run --iterations 5",
        }
    if preset == "node":
        return {
            "preset": "node",
            "metric_hint": "bundle size, test count, or build output metric",
            "guard_hint": "npm test",
            "use_case": "bundle reduction, frontend perf, test expansion",
            "next_step": "autore run --iterations 5",
        }
    return {
        "preset": "generic",
        "metric_hint": "any shell command that prints one number",
        "guard_hint": "optional project smoke test",
        "use_case": "custom repos with a mechanical verify command",
        "next_step": "autore quickstart",
    }


def _infer_preset_from_config(config: ResearchConfig) -> str:
    text = " ".join(
        [
            config.goal,
            config.metric,
            config.verify,
            config.guard or "",
            " ".join(config.scope),
        ]
    ).lower()
    if any(token in text for token in ("pytest", "pyproject", "python", "mypy", "ruff")):
        return "python"
    if any(token in text for token in ("npm", "node", "pnpm", "yarn", "next build", "vitest", "jest")):
        return "node"
    return "generic"


def render_nightly_workflow(
    config_path: str,
    config: ResearchConfig,
    iterations: int,
    python_version: str,
    branch: str,
) -> str:
    lines = [
        "name: autoresearch-nightly",
        "",
        "on:",
        "  workflow_dispatch:",
        "  schedule:",
        "    - cron: '0 1 * * *'",
        "",
        "jobs:",
        "  autoresearch:",
        "    runs-on: ubuntu-latest",
        "    permissions:",
        "      contents: read",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "        with:",
        f"          ref: {branch}",
        "      - uses: actions/setup-python@v5",
        "        with:",
        f"          python-version: '{python_version}'",
        "      - name: Install Codex Autoresearch",
        "        run: |",
        "          python -m venv .venv",
        "          . .venv/bin/activate",
        "          python -m pip install --upgrade pip",
        "          pip install -e .",
        "      - name: Prepare repo",
        "        run: |",
        "          . .venv/bin/activate",
        f"          autore doctor --config {config_path} --fix",
    ]
    if config.guard:
        lines.extend([
            "      - name: Preflight guard",
            "        run: |",
            "          . .venv/bin/activate",
            f"          {config.guard}",
        ])
    lines.extend([
        "      - name: Run bounded autoresearch loop",
        "        run: |",
        "          . .venv/bin/activate",
        f"          autore run --config {config_path} --resume --iterations {iterations} --skip-branch",
        "      - name: Upload logs",
        "        if: always()",
        "        uses: actions/upload-artifact@v4",
        "        with:",
        "          name: autoresearch-results",
        "          path: |",
        f"            {config.log_tsv}",
        f"            {config.scratch_dir}/runs/",
    ])
    return "\n".join(lines) + "\n"


def _print_demo_summary(target: Path) -> None:
    score_path = target / "score.txt"
    results_path = target / ".autoresearch" / "results.tsv"
    print("[autore] demo summary:")
    if score_path.exists():
        print(f"- score.txt: {score_path.read_text().strip()}")
    if results_path.exists():
        lines = [line for line in results_path.read_text().splitlines() if line.strip()]
        if len(lines) > 1:
            print(f"- latest result: {lines[-1]}")
        print(f"- results log: {results_path}")


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
            args.run,
        )
    if args.command == "init":
        return cmd_init(args.force, args.preset)
    if args.command == "run":
        return cmd_run(args.config, args.iterations, args.branch, args.skip_branch, args.resume)
    if args.command == "status":
        return cmd_status(args.config)
    if args.command == "doctor":
        return cmd_doctor(args.config, args.fix)
    if args.command == "watch":
        return cmd_watch(args.config, args.stream, args.follow, args.interval, args.lines)
    if args.command == "quickstart":
        return cmd_quickstart(args.demo_dir)
    if args.command == "onboard":
        return cmd_onboard(args.config, args.workflow_path, args.iterations, args.write_nightly, args.force)
    if args.command == "nightly":
        return cmd_nightly(args.config, args.workflow_path, args.iterations, args.python_version, args.branch, args.force)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
