from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import csv
import shlex
import subprocess
import threading
import time

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

    def ensure_setup(self, branch_name: str | None = None, *, allow_resume: bool = False) -> None:
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        ensure_gitignore_has(self.cwd, [self.config.scratch_dir.rstrip("/") + "/"])
        require_repo_clean(self.cwd, allowed_paths={self.config.log_tsv, self.config.prompt_file, ".gitignore"})
        self._ensure_log_header()
        if branch_name:
            if branch_exists(self.cwd, branch_name):
                raise GitError(f"branch already exists: {branch_name}")
            checkout_new_branch(self.cwd, branch_name)
        elif not allow_resume and self.log_path.exists() and self.log_path.read_text().count("\n") > 1:
            raise GitError("results log already exists; use --resume to continue the current branch")

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

    def resume_state(self) -> tuple[int, float] | None:
        if not self.log_path.exists():
            return None
        lines = [line.strip() for line in self.log_path.read_text().splitlines() if line.strip()]
        if len(lines) <= 1:
            return None
        rows = [line.split("\t") for line in lines[1:]]
        last_iteration = max(int(row[0]) for row in rows)
        kept_rows = [row for row in rows if row[5] in {"baseline", "keep"}]
        if not kept_rows:
            return None
        best_metric = float(kept_rows[-1][2])
        return last_iteration, best_metric

    def run(self, iterations: int, baseline: float, *, start_iteration: int = 1) -> float:
        best_metric = baseline
        stop_iteration = start_iteration + iterations - 1
        for iteration in range(start_iteration, stop_iteration + 1):
            print(f"[autore] iteration {iteration}/{stop_iteration}: preparing prompt")
            prompt = build_iteration_prompt(self.config, iteration, best_metric)
            write_prompt(self.prompt_path, prompt)
            print(f"[autore] iteration {iteration}/{stop_iteration}: running Codex")
            codex_output = self._run_codex(iteration)
            commit = create_experiment_commit(self.cwd, f"experiment: iteration {iteration}", self.config.auto_stage_all)
            print(f"[autore] iteration {iteration}/{stop_iteration}: verifying metric")
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
            print(
                f"[autore] iteration {iteration}/{stop_iteration}: {status} "
                f"(metric={metric:.6f}, delta={delta:.6f}, guard={guard_status})"
            )
        return best_metric

    def _run_codex(self, iteration: int) -> str:
        run_dir = self.session_dir / f"iteration-{iteration:04d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_codex_command()
        print(f"[autore] logs: {run_dir}")
        result = self._run_process_with_logs(
            cmd,
            cwd=self.cwd,
            stdout_path=run_dir / "codex.stdout.log",
            stderr_path=run_dir / "codex.stderr.log",
            timeout_seconds=self.config.codex_timeout_seconds,
        )
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
        result = subprocess.run(
            self.config.verify,
            cwd=self.cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=self.config.verify_timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(f"verify command failed:\n{result.stdout}\n{result.stderr}")
        return extract_last_number(result.stdout)

    def _run_guard(self) -> str:
        if not self.config.guard:
            return "-"
        result = subprocess.run(
            self.config.guard,
            cwd=self.cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=self.config.guard_timeout_seconds,
        )
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

    def latest_run_dir(self) -> Path | None:
        if not self.session_dir.exists():
            return None
        candidates = [path for path in self.session_dir.iterdir() if path.is_dir()]
        if not candidates:
            return None
        return sorted(candidates)[-1]

    def watch_file(
        self,
        path: Path,
        *,
        follow: bool,
        interval_seconds: float,
        lines: int,
    ) -> int:
        if follow:
            return self._follow_file(path, interval_seconds=interval_seconds, lines=lines)
        if not path.exists():
            print(f"No log file found at {path}")
            return 1
        print(self._tail_text(path.read_text(), lines), end="")
        return 0

    def _run_process_with_logs(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def pump(stream, destination: Path, chunks: list[str]) -> None:
            with destination.open("w") as handle:
                if stream is None:
                    return
                for line in iter(stream.readline, ""):
                    handle.write(line)
                    handle.flush()
                    chunks.append(line)
                stream.close()

        stdout_thread = threading.Thread(
            target=pump,
            args=(process.stdout, stdout_path, stdout_chunks),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=pump,
            args=(process.stderr, stderr_path, stderr_chunks),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout_thread.join()
            stderr_thread.join()
            raise RuntimeError(
                f"codex exec timed out after {timeout_seconds}s; inspect logs under {stdout_path.parent}"
            ) from exc

        stdout_thread.join()
        stderr_thread.join()
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
        )

    def _follow_file(self, path: Path, *, interval_seconds: float, lines: int) -> int:
        print(f"[autore] watching {path}")
        previous = ""
        missing_announced = False
        try:
            while True:
                if path.exists():
                    text = path.read_text()
                    if text != previous:
                        if not previous:
                            print(self._tail_text(text, lines), end="")
                        else:
                            print(text[len(previous):], end="")
                        previous = text
                    missing_announced = False
                elif not missing_announced:
                    print(f"[autore] waiting for {path} ...")
                    missing_announced = True
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n[autore] watch stopped")
            return 0

    @staticmethod
    def _tail_text(text: str, lines: int) -> str:
        chunks = text.splitlines(keepends=True)
        return "".join(chunks[-lines:]) if lines > 0 else text


def default_branch_name(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"{prefix}/{stamp}"
