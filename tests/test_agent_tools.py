"""Tests for the tool registry's confirmation and mode behaviour."""

from pathlib import Path

import pytest

from lydia.agent.tools import MUTATING_TOOL_NAMES, ToolContext, build_registry, filter_for_mode
from lydia.config.settings import LydiaConfig
from lydia.tools.filesystem import ToolError


def get(name: str):
    return next(t for t in build_registry() if t.name == name)


def ctx(tmp_path: Path, confirm_result: bool = True, mode: str = "ask") -> ToolContext:
    config = LydiaConfig(mode=mode)
    return ToolContext(root=tmp_path, config=config, confirm=lambda req: confirm_result)


def test_read_file_is_safe_and_needs_no_confirm(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    result = get("read_file").handler({"path": "a.py"}, ctx(tmp_path, confirm_result=False))
    assert result.ok
    assert "x = 1" in result.content


def test_write_file_declined_does_not_touch_disk(tmp_path: Path) -> None:
    result = get("write_file").handler(
        {"path": "new.py", "content": "print(1)\n"}, ctx(tmp_path, confirm_result=False)
    )
    assert not result.ok
    assert not (tmp_path / "new.py").exists()


def test_write_file_approved_writes_to_disk(tmp_path: Path) -> None:
    result = get("write_file").handler(
        {"path": "new.py", "content": "print(1)\n"}, ctx(tmp_path, confirm_result=True)
    )
    assert result.ok
    assert (tmp_path / "new.py").read_text() == "print(1)\n"


def test_delete_file_declined_keeps_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x\n")
    get("delete_file").handler({"path": "a.py"}, ctx(tmp_path, confirm_result=False))
    assert (tmp_path / "a.py").exists()


def test_run_command_auto_mode_runs_safe_without_confirm(tmp_path: Path) -> None:
    asked = []
    context = ToolContext(
        root=tmp_path,
        config=LydiaConfig(mode="auto"),
        confirm=lambda req: asked.append(req) or True,
    )
    result = get("run_command").handler({"command": "echo hi"}, context)
    assert result.ok
    assert asked == []


def test_run_command_auto_mode_still_confirms_dangerous(tmp_path: Path) -> None:
    asked = []
    context = ToolContext(
        root=tmp_path,
        config=LydiaConfig(mode="auto"),
        confirm=lambda req: asked.append(req) or True,
    )
    get("run_command").handler({"command": "rm -rf ./build"}, context)
    assert len(asked) == 1
    assert asked[0].danger is True


def test_run_command_ask_mode_confirms_even_safe_commands(tmp_path: Path) -> None:
    asked = []
    context = ToolContext(
        root=tmp_path,
        config=LydiaConfig(mode="ask"),
        confirm=lambda req: asked.append(req) or True,
    )
    get("run_command").handler({"command": "echo hi"}, context)
    assert len(asked) == 1


def test_search_semantic_reports_not_indexed(tmp_path: Path) -> None:
    result = get("search_semantic").handler({"query": "auth logic"}, ctx(tmp_path))
    assert not result.ok
    assert "lydia index" in result.content


def test_search_semantic_returns_results_when_indexed(tmp_path: Path) -> None:
    from lydia.context.indexer import Chunk
    from lydia.database import sqlite as db

    conn = db.connect(tmp_path)
    db.insert_chunks(conn, [Chunk(path="auth.py", start_line=1, end_line=5, text="def login(): ...", content_hash="h")], [[1.0, 0.0]])
    conn.commit()
    conn.close()

    class FakeClient:
        def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in inputs]

    context = ToolContext(root=tmp_path, config=LydiaConfig(), confirm=lambda req: True, client=FakeClient())
    result = get("search_semantic").handler({"query": "login handling"}, context)
    assert result.ok
    assert "auth.py" in result.content


def test_search_semantic_refuses_when_provider_is_not_ollama(tmp_path: Path) -> None:
    """The index's vectors are tied to whichever model embedded them
    (always Ollama's today, see indexer.py::EMBED_MODEL) — nothing tracks
    that per-index, so querying it through a different provider's embed()
    could silently compare incompatible vectors rather than failing
    cleanly. Must refuse outright instead."""
    from lydia.context.indexer import Chunk
    from lydia.database import sqlite as db

    conn = db.connect(tmp_path)
    db.insert_chunks(conn, [Chunk(path="auth.py", start_line=1, end_line=5, text="def login(): ...", content_hash="h")], [[1.0, 0.0]])
    conn.commit()
    conn.close()

    class FakeClient:
        def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
            raise AssertionError("must not attempt to embed at all for a non-ollama provider")

    context = ToolContext(
        root=tmp_path, config=LydiaConfig(provider="gemini"), confirm=lambda req: True, client=FakeClient()
    )
    result = get("search_semantic").handler({"query": "login handling"}, context)
    assert not result.ok
    assert "ollama provider" in result.content
    assert "search_code" in result.content


def test_remember_tool_is_safe_and_persists(tmp_path: Path) -> None:
    from lydia.agent.facts import load_facts

    result = get("remember").handler({"fact": "uses PostgreSQL"}, ctx(tmp_path, confirm_result=False))
    assert result.ok
    assert "uses PostgreSQL" in result.content
    assert [f.text for f in load_facts(tmp_path)] == ["uses PostgreSQL"]


def test_git_commit_requires_confirmation(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hi\n")
    get("git_add").handler({"paths": ["a.txt"]}, ctx(tmp_path))

    declined = get("git_commit").handler({"message": "add a"}, ctx(tmp_path, confirm_result=False))
    assert not declined.ok

    approved = get("git_commit").handler({"message": "add a"}, ctx(tmp_path, confirm_result=True))
    assert approved.ok


# -- edit_file ----------------------------------------------------------


def test_edit_file_declined_does_not_touch_disk(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    result = get("edit_file").handler(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx(tmp_path, confirm_result=False)
    )
    assert not result.ok
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_edit_file_approved_writes_to_disk(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    result = get("edit_file").handler(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx(tmp_path, confirm_result=True)
    )
    assert result.ok
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


def test_edit_file_not_found_reports_error(tmp_path: Path) -> None:
    # propose_edit raises ToolError directly; like read_file/write_file,
    # _edit_file doesn't catch it itself — that's execute_tool's job in the
    # real agent loop (see agent/loop.py::execute_tool).
    (tmp_path / "a.py").write_text("hello\n")
    with pytest.raises(ToolError, match="not found"):
        get("edit_file").handler({"path": "a.py", "old_string": "goodbye", "new_string": "hi"}, ctx(tmp_path))


def test_edit_file_not_unique_without_replace_all_reports_error(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="unique"):
        get("edit_file").handler({"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx(tmp_path))


def test_edit_file_replace_all(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    result = get("edit_file").handler(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True},
        ctx(tmp_path, confirm_result=True),
    )
    assert result.ok
    assert (tmp_path / "a.py").read_text() == "x = 2\nx = 2\n"


# -- multi_edit_file ------------------------------------------------------


def test_multi_edit_file_declined_does_not_touch_disk(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    result = get("multi_edit_file").handler(
        {"path": "a.py", "edits": [{"old_string": "x = 1", "new_string": "x = 2"}]},
        ctx(tmp_path, confirm_result=False),
    )
    assert not result.ok
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_multi_edit_file_approved_applies_edits_in_order(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    result = get("multi_edit_file").handler(
        {
            "path": "a.py",
            "edits": [
                {"old_string": "x = 1", "new_string": "x = 2"},
                {"old_string": "x = 2", "new_string": "x = 3"},
            ],
        },
        ctx(tmp_path, confirm_result=True),
    )
    assert result.ok
    assert (tmp_path / "a.py").read_text() == "x = 3\n"


def test_multi_edit_file_bad_edit_reports_error(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    with pytest.raises(ToolError, match="edit #1"):
        get("multi_edit_file").handler(
            {"path": "a.py", "edits": [{"old_string": "nope", "new_string": "y"}]}, ctx(tmp_path),
        )


def test_multi_edit_file_auto_mode_skips_confirm(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    asked = []
    context = ToolContext(root=tmp_path, config=LydiaConfig(mode="auto"), confirm=lambda req: asked.append(req) or True)
    result = get("multi_edit_file").handler(
        {"path": "a.py", "edits": [{"old_string": "x = 1", "new_string": "x = 2"}]}, context,
    )
    assert result.ok
    assert asked == []


# -- auto mode: skip confirm unless dangerous ----------------------------


def test_write_file_auto_mode_skips_confirm(tmp_path: Path) -> None:
    asked = []
    context = ToolContext(root=tmp_path, config=LydiaConfig(mode="auto"), confirm=lambda req: asked.append(req) or True)
    result = get("write_file").handler({"path": "new.py", "content": "x\n"}, context)
    assert result.ok
    assert asked == []


def test_edit_file_auto_mode_skips_confirm(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    asked = []
    context = ToolContext(root=tmp_path, config=LydiaConfig(mode="auto"), confirm=lambda req: asked.append(req) or True)
    result = get("edit_file").handler({"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, context)
    assert result.ok
    assert asked == []


def test_delete_file_auto_mode_still_confirms(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x\n")
    asked = []
    context = ToolContext(root=tmp_path, config=LydiaConfig(mode="auto"), confirm=lambda req: asked.append(req) or True)
    get("delete_file").handler({"path": "a.py"}, context)
    assert len(asked) == 1
    assert asked[0].danger is True


def test_git_commit_auto_mode_skips_confirm(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hi\n")
    get("git_add").handler({"paths": ["a.txt"]}, ctx(tmp_path))

    asked = []
    context = ToolContext(root=tmp_path, config=LydiaConfig(mode="auto"), confirm=lambda req: asked.append(req) or True)
    result = get("git_commit").handler({"message": "add a"}, context)
    assert result.ok
    assert asked == []


def test_git_push_auto_mode_still_confirms(tmp_path: Path) -> None:
    # Decline so the handler never actually tries git.push (no remote exists
    # in this tmp_path repo) — the point of this test is only that the
    # confirmation was asked for at all, with danger=True, despite auto mode.
    asked = []
    context = ToolContext(root=tmp_path, config=LydiaConfig(mode="auto"), confirm=lambda req: asked.append(req) or False)
    get("git_push").handler({}, context)
    assert len(asked) == 1
    assert asked[0].danger is True


# -- plan mode: mutating tools not offered at all ------------------------


def test_filter_for_mode_excludes_mutating_tools_in_plan_mode() -> None:
    registry = build_registry()
    filtered_names = {spec.name for spec in filter_for_mode(registry, "plan")}
    assert filtered_names.isdisjoint(MUTATING_TOOL_NAMES)
    assert "read_file" in filtered_names
    assert "git_status" in filtered_names
    assert "check_stocks" in filtered_names


def test_filter_for_mode_ask_and_auto_keep_everything() -> None:
    registry = build_registry()
    all_names = {spec.name for spec in registry}
    assert {spec.name for spec in filter_for_mode(registry, "ask")} == all_names
    assert {spec.name for spec in filter_for_mode(registry, "auto")} == all_names


def test_update_todos_stays_available_in_plan_mode() -> None:
    filtered_names = {spec.name for spec in filter_for_mode(build_registry(), "plan")}
    assert "update_todos" in filtered_names


# -- update_todos ---------------------------------------------------------


def test_update_todos_replaces_full_list(tmp_path: Path) -> None:
    result = get("update_todos").handler(
        {"todos": [{"content": "step 1", "status": "pending"}, {"content": "step 2", "status": "pending"}]},
        ctx(tmp_path),
    )
    assert result.ok
    assert "step 1" in result.content
    assert "step 2" in result.content


def test_update_todos_second_call_replaces_not_appends(tmp_path: Path) -> None:
    context = ctx(tmp_path)
    get("update_todos").handler({"todos": [{"content": "step 1", "status": "pending"}]}, context)
    get("update_todos").handler({"todos": [{"content": "step 1", "status": "completed"}]}, context)
    assert len(context.todos) == 1
    assert context.todos[0].status == "completed"


def test_update_todos_invalid_status_raises(tmp_path: Path) -> None:
    with pytest.raises(ToolError):
        get("update_todos").handler(
            {"todos": [{"content": "step 1", "status": "not-a-status"}]}, ctx(tmp_path),
        )


def test_find_files_handler(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    result = get("find_files").handler({"pattern": "*.py"}, ctx(tmp_path))
    assert result.ok
    assert "a.py" in result.content


def test_update_todos_default_is_empty_and_ephemeral(tmp_path: Path) -> None:
    # Two independent ToolContexts (as non-interactive callers would create)
    # never see each other's todos — only a shared list reference does.
    first = ctx(tmp_path)
    get("update_todos").handler({"todos": [{"content": "x", "status": "pending"}]}, first)
    second = ctx(tmp_path)
    assert second.todos == []


# -- notify tool --------------------------------------------------------


def test_notify_tool_without_topic_reports_not_configured(monkeypatch, tmp_path: Path) -> None:
    from lydia.agent import tools as agent_tools
    from lydia.config import secrets

    monkeypatch.setattr(secrets, "get_secret", lambda key: None)
    result = agent_tools._send_notification({"message": "hi"}, ctx(tmp_path))
    assert result.ok is False
    assert "lydia auth login ntfy" in result.content


def test_notify_tool_sends_push(monkeypatch, tmp_path: Path) -> None:
    from lydia.agent import tools as agent_tools
    from lydia.config import secrets

    sent = {}
    monkeypatch.setattr(secrets, "get_secret", lambda key: "topic-x")
    monkeypatch.setattr(
        "lydia.connectors.ntfy.send_push",
        lambda topic, title, message, priority="default", transport=None: sent.update(
            {"topic": topic, "title": title, "message": message}),
    )
    result = agent_tools._send_notification({"message": "hi", "title": "T"}, ctx(tmp_path))
    assert result.ok is True
    assert sent == {"topic": "topic-x", "title": "T", "message": "hi"}


# -- weather tool --------------------------------------------------------


def test_check_weather_uses_config_location(tmp_path, monkeypatch):
    from lydia.agent import tools as agent_tools
    from lydia.connectors import weather as weather_mod

    seen = {}
    def mock_get_weather(location=None, transport=None):
        seen["loc"] = location
        return "Sunny, 90F"

    monkeypatch.setattr(weather_mod, "get_weather", mock_get_weather)
    context = ctx(tmp_path)
    context.config.weather_location = "Mountain Home"
    result = agent_tools._check_weather({}, context)
    assert result.ok and "Sunny" in result.content
    assert seen["loc"] == "Mountain Home"


# -- calendar tool -------------------------------------------------------


def test_check_calendar_tool(tmp_path, monkeypatch):
    from lydia.agent import tools as agent_tools
    from lydia.connectors import calendar_mac

    monkeypatch.setattr(calendar_mac, "get_events", lambda days=2, runner=None: f"{days} days: Dentist Tuesday")
    result = agent_tools._check_calendar({"days": 5}, ctx(tmp_path))
    assert result.ok and "Dentist" in result.content and "5 days" in result.content


# -- open_app tool -------------------------------------------------------


def test_open_app_launches_named_app(tmp_path, monkeypatch):
    import subprocess
    from lydia.agent import tools

    calls = []
    monkeypatch.setattr(tools.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    result = tools._open_item({"target": "Spotify"}, ctx(tmp_path))
    assert result.ok and calls == [["open", "-a", "Spotify"]]


def test_open_app_opens_existing_path(tmp_path, monkeypatch):
    import subprocess
    from lydia.agent import tools

    doc = tmp_path / "resume.pdf"
    doc.write_text("x")
    calls = []
    monkeypatch.setattr(tools.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    result = tools._open_item({"target": str(doc)}, ctx(tmp_path))
    assert result.ok and calls == [["open", str(doc)]]


def test_open_app_missing_path_fails(tmp_path):
    from lydia.agent import tools

    result = tools._open_item({"target": str(tmp_path / "nope.txt")}, ctx(tmp_path))
    assert not result.ok


def test_open_app_failure_reports(tmp_path, monkeypatch):
    import subprocess
    from lydia.agent import tools

    monkeypatch.setattr(tools.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Unable to find application"))
    result = tools._open_item({"target": "NotARealApp"}, ctx(tmp_path))
    assert not result.ok and "NotARealApp" in result.content
