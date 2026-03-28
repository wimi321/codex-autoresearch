from codex_autoresearch.metrics import extract_last_number, is_improvement


def test_extract_last_number_uses_last_numeric_token() -> None:
    output = "score: 71.2\nother: 88.9\nfinal score 91.4\n"
    assert extract_last_number(output) == 91.4


def test_is_improvement_higher_direction() -> None:
    assert is_improvement(10.5, 10.0, "higher", 0.1)
    assert not is_improvement(10.05, 10.0, "higher", 0.1)


def test_is_improvement_lower_direction() -> None:
    assert is_improvement(8.5, 9.0, "lower", 0.1)
    assert not is_improvement(8.95, 9.0, "lower", 0.1)


def test_extract_last_number_raises_without_numeric_output() -> None:
    try:
        extract_last_number("no score here")
    except ValueError as exc:
        assert "no numeric metric" in str(exc)
    else:
        raise AssertionError("expected ValueError when no metric is present")
