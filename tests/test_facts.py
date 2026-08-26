"""Tests for the persistent project-facts store."""

from pathlib import Path

import pytest

from lydia.agent.facts import MAX_FACTS, forget, load_facts, memory_path, remember


def test_load_facts_empty_when_no_file(tmp_path: Path) -> None:
    assert load_facts(tmp_path) == []


def test_remember_persists_across_loads(tmp_path: Path) -> None:
    remember(tmp_path, "uses PostgreSQL")
    facts = load_facts(tmp_path)
    assert len(facts) == 1
    assert facts[0].text == "uses PostgreSQL"
    assert facts[0].created_at  # timestamp populated


def test_remember_appends_in_order(tmp_path: Path) -> None:
    remember(tmp_path, "first")
    remember(tmp_path, "second")
    facts = load_facts(tmp_path)
    assert [f.text for f in facts] == ["first", "second"]


def test_remember_strips_whitespace(tmp_path: Path) -> None:
    remember(tmp_path, "  padded fact  ")
    assert load_facts(tmp_path)[0].text == "padded fact"


def test_remember_rejects_empty_fact(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        remember(tmp_path, "   ")


def test_remember_caps_at_max_facts(tmp_path: Path) -> None:
    for i in range(MAX_FACTS + 10):
        remember(tmp_path, f"fact {i}")
    facts = load_facts(tmp_path)
    assert len(facts) == MAX_FACTS
    assert facts[0].text == "fact 10"  # oldest 10 dropped
    assert facts[-1].text == f"fact {MAX_FACTS + 9}"


def test_forget_removes_by_one_based_index(tmp_path: Path) -> None:
    remember(tmp_path, "first")
    remember(tmp_path, "second")
    remember(tmp_path, "third")
    removed = forget(tmp_path, 2)
    assert removed.text == "second"
    assert [f.text for f in load_facts(tmp_path)] == ["first", "third"]


def test_forget_invalid_index_raises(tmp_path: Path) -> None:
    remember(tmp_path, "only one")
    with pytest.raises(ValueError):
        forget(tmp_path, 5)
    with pytest.raises(ValueError):
        forget(tmp_path, 0)


def test_load_facts_survives_corrupt_file(tmp_path: Path) -> None:
    path = memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert load_facts(tmp_path) == []


def test_load_facts_ignores_malformed_entries(tmp_path: Path) -> None:
    path = memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('[{"text": "good", "created_at": "t"}, {"missing_text": true}]')
    facts = load_facts(tmp_path)
    assert len(facts) == 1
    assert facts[0].text == "good"


@pytest.mark.parametrize("body, expected_texts", [
    ('[{"text": "hand added"}, "junk", 3]', ["hand added"]),  # no timestamp, non-object entries
    ('{"text": "not a list"}', []),  # wrong top-level shape
])
def test_load_facts_tolerates_hand_edited_files(tmp_path: Path, body: str, expected_texts: list[str]) -> None:
    path = memory_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(body)
    assert [f.text for f in load_facts(tmp_path)] == expected_texts
