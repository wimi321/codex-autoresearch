from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import csv
import shlex
import subprocess

from .config import ResearchConfig
from .gittools import GitError, branch_exists, checkout_new_branch, create_experiment_commit, ensure_gitignore_has, require_repo_clean, revert_last_commit
from .metrics import extract_last_number, is_improvement
from .prompting import build_iteration_prompt, write_prompt


@dataclass(slots=True)
class IterationResult:
    iteration: int
    commit: str
    metric: float
    delta: float
    guard: str
    status: str
    summary: str


class ResearchRunner:
    def __init__(self, cwd: Path, config: ResearchConfig) -> None:
        self.cwd = cwd
        self.config = config
        self.scratch_dir = cwd / config.scratch_dir
        self.prompt_path = cwd / config.prompt_file
        self.log_path = cwd / config.log_tsv
        self.session_dir = self.scratch_dir / "runs"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def ensure_setup(self, branch_name: str | None = None) -> None:
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        ensure_gitignore_has(self.cwd, [self.config.scratch_dir.rstrip("/") + "/"])
        require_repo_clean(self.cwd, allowed_paths={self.config.log_tsv, self.config.prompt_file, ".gitignore"})
        self._ensure_log_header()
        if branch_name:
            if branch_exists(self.cwd, branch_name):
                raise GitError(f"branch already exists: {branch_name}")
            checkout_new_branch(self.cwd, branch_name)

    def establish_baseline(self) -> float:
        metric = self._run_verify()
        self._append_row(IterationResult(
            iteration=0,
            commit="baseline",
            metric=metric,
            delta=0.0,
            guard="-",
            status="baseline",
            summary="initial baseline",
        ))
        return metric

    def run(self, iterations: int, baseline: float) -> float:
        best_metric = baseline
        for iteration in range(1, iterations + 1):
            prompt = build_iteration_prompt(self.config, iteration, best_metric)
            write_prompt(self.prompt_path, prompt)
            codex_output = self._run_codex(iteration)
            commit = create_experiment_commit(self.cwd, f"experiment: iteration {iteration}", self.config.auto_stage_all)
            metric = self._run_verify()
            guard_status = self._run_guard()
            delta = metric - best_metric if self.config.direction == "higher" else best_metric - metric
            status = "discard"
            if guard_status == "fail":
                summary = f"guard failed after Codex change. {codex_output.strip()}"
                revert_last_commit(self.cwd)
            elif is_improvement(metric, best_metric, self.config.direction, self.config.min_delta):
                status = "keep"
                best_metric = metric
                summary = codex_output.strip()
            else:
                summary = codex_output.strip()
                revert_last_commit(self.cwd)
            self._append_row(IterationResult(
                iteration=iteration,
                commit=commit,
                metric=metric,
                delta=delta,
                guard=guard_status,
                status=status,
                summary=summary,
            ))
        return best_metric

    def _run_codex(self, iteration: int) -> str:
        run_dir = self.session_dir / f"iteration-{iteration:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_codex_command()
        result = subprocess.run(cmd, cwd=self.cwd, text=True, capture_output=True)
        (run_dir / "codex.stdout.log").write_text(result.stdout)
        (run_dir / "codex.stderr.log").write_text(result.stderr)
        if result.returncode != 0:
            raise RuntimeError(f"codex exec failed: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout.strip() or "Codex applied a change"

    def _build_codex_command(self) -> list[str]:
        base = shlex.split(self.config.codex_command)
        if not base:
            raise RuntimeError("codex_command cannot be empty")
        if base[0] == "codex":
            if len(base) > 1 and base[1] == "exec":
                return ["codex", "-a", "never", "exec", "-s", "workspace-write", str(self.prompt_path)]
            return ["codex", "-a", "never", *base[1:], "exec", "-s", "workspace-write", str(self.prompt_path)]
        return [*base, str(self.prompt_path)]

    def _run_verify(self) -> float:
        result = subprocess.run(self.config.verify, cwd=self.cwd, shell=True, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"verify command failed:\n{result.stdout}\n{result.stderr}")
        return extract_last_number(result.stdout)

    def _run_guard(self) -> str:
        if not self.config.guard:
            return "-"
        result = subprocess.run(self.config.guard, cwd=self.cwd, shell=True, text=True, capture_output=True)
        return "pass" if result.returncode == 0 else "fail"

    def _ensure_log_header(self) -> None:
        if self.log_path.exists():
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("iteration\tcommit\tmetric\tdelta\tguard\tstatus\tsummary\n")

    def _append_row(self, row: IterationResult) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow([
                row.iteration,
                row.commit,
                f"{row.metric:.6f}",
                f"{row.delta:.6f}",
                row.guard,
                row.status,
                row.summary.replace("\n", " ")[:500],
            ])


def default_branch_name(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"{prefix}/{stamp}"
