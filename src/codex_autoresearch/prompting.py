from __future__ import annotations

from pathlib import Path

from .config import ResearchConfig


def build_iteration_prompt(config: ResearchConfig, iteration: int, baseline_metric: float) -> str:
    scope = "\n".join(f"- {item}" for item in config.scope) if config.scope else "- Entire repository"
    guard = config.guard or "None"
    return f"""# Codex Autoresearch Iteration

You are running one iteration of a Codex-native autonomous research loop.

## Goal
- {config.goal}

## Metric
- Name: {config.metric}
- Direction: {config.metric_direction_label()}
- Current best: {baseline_metric:.6f}
- Minimum delta to count as improvement: {config.min_delta:.6f}

## Scope
{scope}

## Guard
- {guard}

## Hard Rules
1. Read relevant files before editing.
2. Make exactly one focused, reversible change.
3. Stay inside the declared scope.
4. Do not edit generated logs under `.autoresearch/`.
5. Leave the repo in a runnable state for the verify command.
6. Keep diffs simple and explainable.

## Task
Complete iteration {iteration}.
- Inspect the current repository state.
- Choose the highest-leverage single change for the goal.
- Apply the change directly in the working tree.
- Do not run git commit yourself.
- When done, print a compact summary with:
  - changed files
  - hypothesis
  - expected metric effect
"""


def write_prompt(path: Path, prompt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt)
