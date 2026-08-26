"""The agent loop: plan -> call tools -> observe -> respond.

This module has no UI dependency. Rendering (streaming text, showing
diffs, asking y/n) is injected as plain callables so the loop can be
driven by the Rich-based REPL or by a test double identically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from lydia.agent.tools import ToolContext, ToolResult, ToolSpec
from lydia.llm.protocol import ModelClient
from lydia.llm.types import ChatChunk, Message, ToolCall
from lydia.tools.filesystem import ToolError
from lydia.tools.paths import PathEscapesProjectError

MAX_TOOL_ITERATIONS = 25

StreamFn = Callable[[Iterator[ChatChunk]], "StreamResult"]
ToolCallHook = Callable[[ToolCall], None]
ToolResultHook = Callable[[ToolCall, ToolResult], None]


@dataclass
class StreamResult:
    content: str
    tool_calls: list[ToolCall]
    stats: dict


def default_stream_fn(chunks: Iterator[ChatChunk]) -> StreamResult:
    """Drain the stream with no rendering; used by non-interactive callers."""
    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    stats: dict = {}
    for chunk in chunks:
        if chunk.content:
            content_parts.append(chunk.content)
        if chunk.tool_calls:
            tool_calls = chunk.tool_calls
        if chunk.done:
            stats = chunk.stats
    return StreamResult(content="".join(content_parts), tool_calls=tool_calls, stats=stats)


def execute_tool(spec: ToolSpec | None, call: ToolCall, ctx: ToolContext) -> ToolResult:
    if spec is None:
        return ToolResult(ok=False, content=f"Unknown tool '{call.name}'.", summary="unknown tool")
    if not isinstance(call.arguments, dict):
        # The model (or a backend that sends arguments as text) produced
        # something other than a JSON object; say so rather than failing on
        # a TypeError deep inside the handler.
        return ToolResult(
            ok=False,
            content=f"Arguments for {call.name} must be a JSON object, got: {str(call.arguments)[:200]!r}",
            summary="bad arguments",
        )
    try:
        return spec.handler(call.arguments, ctx)
    except KeyError as exc:
        # Handlers index args directly, so a KeyError here is the model
        # leaving out a required argument (the JSON schema is advisory to it).
        return ToolResult(ok=False, content=f"Missing required argument {exc} for {call.name}.", summary="error")
    except (ToolError, PathEscapesProjectError) as exc:
        return ToolResult(ok=False, content=str(exc), summary="error")
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed silently
        return ToolResult(ok=False, content=f"Unexpected error in {call.name}: {exc}", summary="error")


def run_agent_turn(
    *,
    client: ModelClient,
    model: str,
    temperature: float,
    num_ctx: int,
    think: bool | None,
    keep_alive: str | None = None,
    system_prompt: str,
    messages: list[Message],
    registry: list[ToolSpec],
    ctx: ToolContext,
    stream_fn: StreamFn = default_stream_fn,
    on_tool_call: ToolCallHook | None = None,
    on_tool_result: ToolResultHook | None = None,
) -> tuple[str, dict]:
    """Run one user turn to completion, including any tool calls.

    Mutates *messages* in place (appending the assistant/tool exchange) so
    the caller's conversation history stays authoritative. Returns the
    final assistant text and the last generation's stats.
    """
    schemas = [spec.schema() for spec in registry]
    by_name = {spec.name: spec for spec in registry}
    start = len(messages)

    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            request = [Message(role="system", content=system_prompt), *messages]
            result = stream_fn(client.chat_stream(
                model=model, messages=request, temperature=temperature,
                num_ctx=num_ctx, think=think, tools=schemas, keep_alive=keep_alive,
            ))
            if not result.tool_calls:
                messages.append(Message(role="assistant", content=result.content))
                return result.content, result.stats

            messages.append(Message(role="assistant", content=result.content, tool_calls=result.tool_calls))
            for call in result.tool_calls:
                if on_tool_call:
                    on_tool_call(call)
                tool_result = execute_tool(by_name.get(call.name), call, ctx)
                if on_tool_result:
                    on_tool_result(call, tool_result)
                messages.append(Message(role="tool", content=tool_result.content))
    except BaseException:
        # Ctrl-C at a confirmation prompt (or anything else escaping mid-turn)
        # would leave an assistant tool call with no matching tool result in
        # the caller's history, and some providers then reject every later
        # turn of the session. Drop the half-finished exchange instead.
        del messages[start:]
        raise

    stop_message = (
        "I stopped after several tool calls without reaching an answer. "
        "Try breaking the request into a smaller step."
    )
    messages.append(Message(role="assistant", content=stop_message))
    return stop_message, {}
