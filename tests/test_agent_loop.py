"""Tests for the agent loop's tool-calling orchestration, using a fake client."""

from pathlib import Path

import pytest

from lydia.agent.loop import MAX_TOOL_ITERATIONS, default_stream_fn, run_agent_turn
from lydia.agent.tools import ToolContext, ToolResult, ToolSpec, build_registry
from lydia.config.settings import LydiaConfig
from lydia.llm.types import ChatChunk, Message, ToolCall


class FakeClient:
    """Returns a scripted sequence of responses, one per call to chat_stream."""

    def __init__(self, responses: list[list[ChatChunk]]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.responses[len(self.calls) - 1])


def make_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(root=tmp_path, config=LydiaConfig(), confirm=lambda req: True)


def test_no_tool_call_returns_text_directly(tmp_path: Path) -> None:
    client = FakeClient([[ChatChunk(content="Hello there.", done=True)]])
    messages: list[Message] = [Message(role="user", content="hi")]
    reply, stats = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=build_registry(),
        ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
    )
    assert reply == "Hello there."
    assert len(client.calls) == 1
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "Hello there."


def test_tool_call_then_final_answer(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    client = FakeClient([
        [ChatChunk(tool_calls=[ToolCall(name="read_file", arguments={"path": "a.py"})], done=True)],
        [ChatChunk(content="The file sets x to 1.", done=True)],
    ])
    messages: list[Message] = [Message(role="user", content="what's in a.py?")]
    calls_seen = []
    results_seen = []
    reply, _ = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=build_registry(),
        ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
        on_tool_call=calls_seen.append,
        on_tool_result=lambda call, result: results_seen.append(result),
    )
    assert reply == "The file sets x to 1."
    assert len(client.calls) == 2
    assert calls_seen[0].name == "read_file"
    assert results_seen[0].ok
    assert "x = 1" in results_seen[0].content
    # tool exchange recorded: user, assistant(tool_call), tool, assistant(final)
    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]


def test_declined_write_is_reported_back_to_model(tmp_path: Path) -> None:
    client = FakeClient([
        [ChatChunk(tool_calls=[ToolCall(name="write_file", arguments={"path": "x.py", "content": "1"})], done=True)],
        [ChatChunk(content="Okay, I won't write it.", done=True)],
    ])
    messages: list[Message] = [Message(role="user", content="write x.py")]
    ctx = ToolContext(root=tmp_path, config=LydiaConfig(), confirm=lambda req: False)
    reply, _ = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=build_registry(),
        ctx=ctx, stream_fn=default_stream_fn,
    )
    assert reply == "Okay, I won't write it."
    assert not (tmp_path / "x.py").exists()
    assert "declined" in messages[2].content.lower()
    assert "not" in messages[2].content.lower()


def test_unknown_tool_reports_error_without_crashing(tmp_path: Path) -> None:
    client = FakeClient([
        [ChatChunk(tool_calls=[ToolCall(name="not_a_real_tool", arguments={})], done=True)],
        [ChatChunk(content="Never mind.", done=True)],
    ])
    messages: list[Message] = [Message(role="user", content="do the thing")]
    reply, _ = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=build_registry(),
        ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
    )
    assert reply == "Never mind."
    assert "Unknown tool" in messages[2].content


def test_handler_exception_becomes_tool_error_not_crash(tmp_path: Path) -> None:
    def boom(args: dict, ctx: ToolContext) -> ToolResult:
        raise RuntimeError("kaboom")

    broken_registry = [ToolSpec("broken", "always fails", {"type": "object", "properties": {}}, "safe", boom)]
    client = FakeClient([
        [ChatChunk(tool_calls=[ToolCall(name="broken", arguments={})], done=True)],
        [ChatChunk(content="It failed.", done=True)],
    ])
    messages: list[Message] = [Message(role="user", content="try it")]
    reply, _ = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=broken_registry,
        ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
    )
    assert reply == "It failed."
    assert "kaboom" in messages[2].content


def test_stops_after_max_iterations(tmp_path: Path) -> None:
    loop_forever = [ChatChunk(tool_calls=[ToolCall(name="git_status", arguments={})], done=True)]
    client = FakeClient([loop_forever] * (MAX_TOOL_ITERATIONS + 2))
    messages: list[Message] = [Message(role="user", content="loop")]
    reply, _ = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=build_registry(),
        ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
    )
    assert "stopped" in reply.lower()
    assert len(client.calls) == MAX_TOOL_ITERATIONS


def test_missing_required_argument_is_reported_to_model(tmp_path: Path) -> None:
    client = FakeClient([
        [ChatChunk(tool_calls=[ToolCall(name="read_file", arguments={})], done=True)],
        [ChatChunk(content="I need a path.", done=True)],
    ])
    messages: list[Message] = [Message(role="user", content="read it")]
    reply, _ = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=build_registry(),
        ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
    )
    assert reply == "I need a path."
    assert "Missing required argument 'path' for read_file" in messages[2].content


def test_non_object_arguments_are_reported_to_model(tmp_path: Path) -> None:
    client = FakeClient([
        [ChatChunk(tool_calls=[ToolCall(name="read_file", arguments="a.py")], done=True)],
        [ChatChunk(content="Let me retry.", done=True)],
    ])
    messages: list[Message] = [Message(role="user", content="read it")]
    reply, _ = run_agent_turn(
        client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
        system_prompt="sys", messages=messages, registry=build_registry(),
        ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
    )
    assert reply == "Let me retry."
    assert "must be a JSON object" in messages[2].content
    assert "'a.py'" in messages[2].content


def test_interrupted_turn_leaves_no_half_finished_exchange(tmp_path: Path) -> None:
    """Ctrl-C at the confirmation prompt happens inside the tool handler;
    the assistant's tool call must not stay in history without its result."""
    def interrupted(args: dict, ctx: ToolContext) -> ToolResult:
        raise KeyboardInterrupt

    registry = [ToolSpec("slow", "hangs", {"type": "object", "properties": {}}, "safe", interrupted)]
    client = FakeClient([[ChatChunk(tool_calls=[ToolCall(name="slow", arguments={})], done=True)]])
    messages: list[Message] = [Message(role="user", content="go")]
    with pytest.raises(KeyboardInterrupt):
        run_agent_turn(
            client=client, model="m", temperature=0.7, num_ctx=8192, think=None,
            system_prompt="sys", messages=messages, registry=registry,
            ctx=make_ctx(tmp_path), stream_fn=default_stream_fn,
        )
    assert [m.role for m in messages] == ["user"]
