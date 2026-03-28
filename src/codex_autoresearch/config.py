from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(slots=True)
class ResearchConfig:
    goal: str
    metric: str
    direction: str
    verify: str
    scope: list[str] = field(default_factory=list)
    guard: str | None = None
    iterations: int | None = None
    min_delta: float = 0.0
    branch_prefix: str = "autoresearch"
    codex_command: str = "codex exec"
    prompt_file: str = ".autoresearch/prompt.md"
    log_tsv: str = ".autoresearch/results.tsv"
    scratch_dir: str = ".autoresearch"
    auto_stage_all: bool = True
    codex_timeout_seconds: int = 1800
    verify_timeout_seconds: int = 300
    guard_timeout_seconds: int = 300

    @classmethod
    def load(cls, path: str | Path = "autoresearch.toml") -> "ResearchConfig":
        config_path = Path(path)
        data = tomllib.loads(config_path.read_text())
        research = data.get("research", {})
        runtime = data.get("runtime", {})
        files = data.get("files", {})
        git = data.get("git", {})
        return cls(
            goal=research["goal"],
            metric=research["metric"],
            direction=research.get("direction", "higher"),
            verify=research["verify"],
            scope=research.get("scope", []),
            guard=research.get("guard"),
            iterations=research.get("iterations"),
            min_delta=float(research.get("min_delta", 0.0)),
            branch_prefix=git.get("branch_prefix", "autoresearch"),
            codex_command=runtime.get("codex_command", "codex exec"),
            prompt_file=files.get("prompt_file", ".autoresearch/prompt.md"),
            log_tsv=files.get("log_tsv", ".autoresearch/results.tsv"),
            scratch_dir=files.get("scratch_dir", ".autoresearch"),
            auto_stage_all=bool(runtime.get("auto_stage_all", True)),
            codex_timeout_seconds=int(runtime.get("codex_timeout_seconds", 1800)),
            verify_timeout_seconds=int(runtime.get("verify_timeout_seconds", 300)),
            guard_timeout_seconds=int(runtime.get("guard_timeout_seconds", 300)),
        )

    def metric_direction_label(self) -> str:
        if self.direction not in {"higher", "lower"}:
            raise ValueError("direction must be 'higher' or 'lower'")
        return "higher is better" if self.direction == "higher" else "lower is better"


GENERIC_TEMPLATE = """[research]
goal = "Increase a mechanical metric with Codex"
metric = "example score"
direction = "higher"
verify = "./scripts/verify.sh"
scope = ["src/**", "tests/**"]
guard = ""
iterations = 5
min_delta = 0.0

[runtime]
codex_command = "codex exec"
auto_stage_all = true
codex_timeout_seconds = 1800
verify_timeout_seconds = 300
guard_timeout_seconds = 300

[git]
branch_prefix = "autoresearch"

[files]
prompt_file = ".autoresearch/prompt.md"
log_tsv = ".autoresearch/results.tsv"
scratch_dir = ".autoresearch"
"""


PYTHON_TEMPLATE = """[research]
goal = "Increase pytest coverage"
metric = "coverage percent"
direction = "higher"
verify = "pytest --cov=src 2>&1 | grep TOTAL"
scope = ["src/**", "tests/**"]
guard = "pytest"
iterations = 10
min_delta = 0.1

[runtime]
codex_command = "codex exec"
auto_stage_all = true
codex_timeout_seconds = 1800
verify_timeout_seconds = 300
guard_timeout_seconds = 300

[git]
branch_prefix = "autoresearch"

[files]
prompt_file = ".autoresearch/prompt.md"
log_tsv = ".autoresearch/results.tsv"
scratch_dir = ".autoresearch"
"""


NODE_TEMPLATE = """[research]
goal = "Reduce bundle size without breaking tests"
metric = "bundle size kb"
direction = "lower"
verify = "npm run build 2>&1 | grep 'First Load JS'"
scope = ["src/**"]
guard = "npm test"
iterations = 10
min_delta = 1.0

[runtime]
codex_command = "codex exec"
auto_stage_all = true
codex_timeout_seconds = 1800
verify_timeout_seconds = 300
guard_timeout_seconds = 300

[git]
branch_prefix = "autoresearch"

[files]
prompt_file = ".autoresearch/prompt.md"
log_tsv = ".autoresearch/results.tsv"
scratch_dir = ".autoresearch"
"""


def detect_preset(cwd: Path) -> str:
    if (cwd / "package.json").exists():
        return "node"
    if (cwd / "pyproject.toml").exists() or (cwd / "pytest.ini").exists() or (cwd / "tests").exists():
        return "python"
    return "generic"


def template_for_preset(preset: str) -> str:
    if preset == "auto":
        raise ValueError("'auto' must be resolved before selecting a template")
    if preset == "python":
        return PYTHON_TEMPLATE
    if preset == "node":
        return NODE_TEMPLATE
    if preset == "generic":
        return GENERIC_TEMPLATE
    raise ValueError(f"unknown preset: {preset}")
