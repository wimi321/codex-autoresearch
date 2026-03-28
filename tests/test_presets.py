from pathlib import Path

from codex_autoresearch.config import detect_preset, template_for_preset


def test_detect_preset_prefers_node_when_package_json_exists(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    assert detect_preset(tmp_path) == "node"


def test_detect_preset_uses_python_when_tests_directory_exists(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    assert detect_preset(tmp_path) == "python"


def test_detect_preset_falls_back_to_generic(tmp_path: Path) -> None:
    assert detect_preset(tmp_path) == "generic"


def test_template_for_preset_returns_expected_templates() -> None:
    assert "pytest --cov=src" in template_for_preset("python")
    assert "npm run build" in template_for_preset("node")
    assert "./scripts/verify.sh" in template_for_preset("generic")
