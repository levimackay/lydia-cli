"""Tests for the require_extra() friendly-error wrapper (see
cli/optional_deps.py for why it exists — pyproject.toml splits heavy,
rarely-needed dependencies into [assistant]/[voice] extras)."""

import pytest
import typer

from lydia.cli.optional_deps import require_extra


def test_missing_module_raises_typer_exit_with_a_helpful_message(capsys) -> None:
    with pytest.raises(typer.Exit):
        with require_extra("assistant", "Gmail login"):
            import this_module_does_not_exist_anywhere  # noqa: F401

    output = capsys.readouterr().out
    assert "assistant" in output
    assert "Gmail login" in output
    assert "pip install" in output
    assert "lydia-cli[assistant]" in output


def test_successful_block_passes_through_untouched() -> None:
    with require_extra("voice", "Voice mode"):
        result = 1 + 1
    assert result == 2


def test_unrelated_import_error_is_not_swallowed() -> None:
    """A real ImportError from inside the feature's own code (e.g. a
    version mismatch, a circular import) is not "extra not installed" and
    shouldn't be misreported as such — only ModuleNotFoundError (a
    missing top-level package) gets the friendly rewrite."""
    with pytest.raises(ImportError):
        with require_extra("assistant", "Gmail login"):
            raise ImportError("cannot import name 'Foo' from partially initialized module")


def test_other_exceptions_are_not_caught() -> None:
    with pytest.raises(ValueError):
        with require_extra("voice", "Voice mode"):
            raise ValueError("something unrelated")
