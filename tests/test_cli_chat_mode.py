"""Tests for session mode switching in the chat REPL.

Only `_apply_mode` and `_handle_slash`'s /mode branch are meaningfully
testable without a real terminal — the Shift-Tab keybinding and live
prompt rendering aren't (see CLAUDE.md's documented pty/tty testing
limitation for this REPL).
"""

from pathlib import Path

import pytest

from lydia.agent.tools import ToolContext, build_registry
from lydia.cli import chat as chat_mod
from lydia.cli.chat import VALID_MODES, ChatSession, _apply_mode, _handle_slash
from lydia.config.settings import LydiaConfig
from lydia.llm.client import OllamaError


class _FakeClient:
    def list_models(self):
        return []


def make_session(tmp_path: Path, mode: str = "ask") -> ChatSession:
    config = LydiaConfig(mode=mode)
    return ChatSession(config, _FakeClient(), "fake-model", summary=None, project_root=tmp_path)


def test_apply_mode_accepts_valid_modes(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    for mode in VALID_MODES:
        assert _apply_mode(session, mode) is True
        assert session.config.mode == mode


def test_apply_mode_rejects_unknown_mode(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    assert _apply_mode(session, "yolo") is False
    assert session.config.mode == "ask"  # unchanged


def test_mode_slash_command_shows_current_mode(tmp_path: Path, capsys) -> None:
    session = make_session(tmp_path, mode="plan")
    _handle_slash("/mode", session)
    assert "plan" in capsys.readouterr().out


def test_mode_slash_command_switches_mode(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    _handle_slash("/mode auto", session)
    assert session.config.mode == "auto"


def test_mode_slash_command_rejects_typo(tmp_path: Path, capsys) -> None:
    session = make_session(tmp_path)
    _handle_slash("/mode atuo", session)
    assert session.config.mode == "ask"  # unchanged
    assert "Unknown mode" in capsys.readouterr().out


def test_send_rebuilds_system_prompt_for_current_mode(tmp_path: Path) -> None:
    session = make_session(tmp_path, mode="ask")
    assert "plan mode" not in session.system_prompt
    session.config.mode = "plan"
    session.system_prompt = session._build_system_prompt()
    assert "plan mode" in session.system_prompt


def test_session_todos_persist_across_turns_via_shared_reference(tmp_path: Path) -> None:
    # Mirrors exactly what ChatSession.send() does: build a ToolContext with
    # todos=self.todos (the same list object), so a handler's mutation this
    # turn is visible on session.todos without any extra plumbing.
    session = make_session(tmp_path)
    update_todos = next(t for t in build_registry() if t.name == "update_todos")

    ctx_turn_one = ToolContext(root=tmp_path, config=session.config, confirm=lambda req: True, todos=session.todos)
    update_todos.handler({"todos": [{"content": "step 1", "status": "pending"}]}, ctx_turn_one)
    assert len(session.todos) == 1

    ctx_turn_two = ToolContext(root=tmp_path, config=session.config, confirm=lambda req: True, todos=session.todos)
    update_todos.handler({"todos": [{"content": "step 1", "status": "completed"}]}, ctx_turn_two)
    assert session.todos[0].status == "completed"


def test_send_pops_user_message_on_ollama_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**kwargs):
        raise OllamaError("connection refused")

    monkeypatch.setattr(chat_mod, "run_agent_turn", _raise)
    session = make_session(tmp_path)
    session.send("hello")
    # No paired reply, so the failed turn must leave no trace: a later turn's
    # user message must not land right after this one (see run_agent_turn's
    # own del messages[start:] rollback in agent/loop.py, which this pairs with).
    assert session.messages == []


def test_send_pops_user_message_on_keyboard_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(chat_mod, "run_agent_turn", _raise)
    session = make_session(tmp_path)
    session.send("hello")
    assert session.messages == []


def test_send_after_a_failed_turn_does_not_leave_two_consecutive_user_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def _fake_turn(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            raise OllamaError("connection refused")
        kwargs["messages"].append(chat_mod.Message(role="assistant", content="hi there"))
        return "hi there", {}

    monkeypatch.setattr(chat_mod, "run_agent_turn", _fake_turn)
    session = make_session(tmp_path)

    session.send("first, this one fails")
    session.send("second, this one succeeds")

    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant"]  # not ["user", "user", "assistant"]
