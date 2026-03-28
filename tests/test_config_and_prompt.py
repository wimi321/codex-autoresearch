from pathlib import Path

from codex_autoresearch.config import ResearchConfig
from codex_autoresearch.prompting import build_iteration_prompt


def test_config_loads_optional_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "autoresearch.toml"
    config_path.write_text(
        """
[research]
goal = "Increase coverage"
metric = "coverage percent"
direction = "higher"
verify = "pytest --cov"
scope = ["src/**", "tests/**"]
guard = "pytest"
iterations = 7
min_delta = 0.5

[runtime]
codex_command = "codex exec"
auto_stage_all = true

[git]
branch_prefix = "research"

[files]
prompt_file = ".autoresearch/prompt.md"
log_tsv = ".autoresearch/results.tsv"
scratch_dir = ".autoresearch"
""".strip()
    )

    config = ResearchConfig.load(config_path)

    assert config.goal == "Increase coverage"
    assert config.scope == ["src/**", "tests/**"]
    assert config.iterations == 7
    assert config.metric_direction_label() == "higher is better"


def test_build_iteration_prompt_contains_goal_metric_and_rules() -> None:
    config = ResearchConfig(
        goal="Reduce bundle size",
        metric="bundle size kb",
        direction="lower",
        verify="npm run build",
        scope=["src/**"],
        guard="npm test",
        iterations=5,
        min_delta=1.0,
    )

    prompt = build_iteration_prompt(config, iteration=3, baseline_metric=180.0)

    assert "Reduce bundle size" in prompt
    assert "Current best: 180.000000" in prompt
    assert "- src/**" in prompt
    assert "Do not run git commit yourself." in prompt
