"""Terminal rendering helpers built on Rich."""

from __future__ import annotations

from collections.abc import Iterator

import pyfiglet
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text

from lydia import __version__
from lydia.agent.loop import StreamResult
from lydia.agent.tools import ConfirmRequest, ToolResult
from lydia.llm.types import ChatChunk, ToolCall

console = Console()

ACCENT = "medium_purple1"

# Blue -> violet -> pink, sampled across the logo's width.
_GRADIENT_STOPS = ((0x7D, 0xD3, 0xFC), (0xA7, 0x8B, 0xFA), (0xF4, 0x72, 0xB6))


def _gradient_color(fraction: float) -> str:
    """Interpolate across _GRADIENT_STOPS at *fraction* in [0, 1]."""
    fraction = max(0.0, min(1.0, fraction))
    segment_count = len(_GRADIENT_STOPS) - 1
    segment = min(int(fraction * segment_count), segment_count - 1)
    local = fraction * segment_count - segment
    start, end = _GRADIENT_STOPS[segment], _GRADIENT_STOPS[segment + 1]
    r, g, b = (round(s + (e - s) * local) for s, e in zip(start, end))
    return f"#{r:02x}{g:02x}{b:02x}"


_ICON_ROWS: tuple[str, ...] = (
    "   ██   ",
    "  ████  ",
    " ██████ ",
    " ██████ ",
    "  ████  ",
    "   ██   ",
)


def _with_icon(lines: list[str]) -> list[str]:
    """Frame each wordmark row with the small block-diamond mark on both sides, same heavy-block style as the font."""
    icon_width = len(_ICON_ROWS[0])
    blank_icon_row = " " * icon_width
    result = []
    for i, line in enumerate(lines):
        icon = _ICON_ROWS[i] if i < len(_ICON_ROWS) else blank_icon_row
        result.append(f"{icon}  {line}  {icon}")
    return result


def render_logo() -> Text | None:
    """Gradient LYDIA CLI wordmark bookended by a small icon on both sides, or None if the terminal is too narrow."""
    art = pyfiglet.figlet_format("LYDIA CLI", font="ansi_shadow").rstrip("\n")
    lines = art.split("\n")
    for candidate in (_with_icon(lines), lines):
        width = max((len(line) for line in candidate), default=0)
        if width and console.width >= width:
            logo = Text()
            for line in candidate:
                for x, char in enumerate(line):
                    logo.append(char, style=_gradient_color(x / max(width - 1, 1)) if char != " " else "")
                logo.append("\n")
            return logo
    return None


def print_banner(model: str, project_kind: str | None = None) -> None:
    logo = render_logo()
    if logo is not None:
        console.print(logo)
    else:
        console.print(Text("LYDIA CLI", style=f"bold {ACCENT}"))
    subtitle = Text.assemble(
        ("local AI coding agent  ", "dim"),
        (f"v{__version__}", "dim italic"),
    )
    body = Text.assemble(
        ("model  ", "dim"),
        (model, "bold"),
    )
    if project_kind:
        body.append_text(Text.assemble(("\nproject  ", "dim"), (project_kind, "")))
    console.print(Panel(body, subtitle=subtitle, border_style=ACCENT, expand=False))
    console.print("[dim]Type your request, or /help for commands. Ctrl-D to exit.[/dim]\n")


def print_error(message: str) -> None:
    console.print(f"[bold red]error:[/bold red] {message}")


def print_info(message: str) -> None:
    console.print(f"[{ACCENT}]•[/{ACCENT}] {message}")


def print_warning(message: str) -> None:
    console.print(f"[bold yellow]warning:[/bold yellow] {message}")


def stream_response(chunks: Iterator[ChatChunk]) -> tuple[str, dict]:
    """Render a streaming reply as live-updating Markdown.

    While a thinking model reasons, the last few lines of its thinking are
    shown dimmed; they collapse away once the actual answer starts.
    Returns the full response text and the generation stats from the
    final chunk.
    """
    buffer: list[str] = []
    thinking: list[str] = []
    stats: dict = {}
    with Live(console=console, refresh_per_second=12, vertical_overflow="visible") as live:
        for chunk in chunks:
            if chunk.thinking and not buffer:
                thinking.append(chunk.thinking)
                live.update(_thinking_preview("".join(thinking)))
            if chunk.content:
                buffer.append(chunk.content)
                live.update(Markdown("".join(buffer)))
            if chunk.done:
                stats = chunk.stats
                if not buffer:  # model produced only thinking — show something
                    live.update(Markdown("".join(thinking)))
    return "".join(buffer) or "".join(thinking), stats


def _thinking_preview(text: str, max_lines: int = 4) -> Text:
    tail = [line for line in text.splitlines() if line.strip()][-max_lines:]
    preview = Text("thinking…\n", style=f"italic {ACCENT}")
    preview.append("\n".join(tail), style="dim")
    return preview


def stream_agent_response(chunks: Iterator[ChatChunk]) -> StreamResult:
    """Like stream_response, but also captures a tool call if the model makes one."""
    buffer: list[str] = []
    thinking: list[str] = []
    tool_calls: list[ToolCall] = []
    stats: dict = {}
    with Live(console=console, refresh_per_second=12, vertical_overflow="visible") as live:
        for chunk in chunks:
            if chunk.tool_calls:
                tool_calls = chunk.tool_calls
            if chunk.thinking and not buffer:
                thinking.append(chunk.thinking)
                live.update(_thinking_preview("".join(thinking)))
            if chunk.content:
                buffer.append(chunk.content)
                live.update(Markdown("".join(buffer)))
            if chunk.done:
                stats = chunk.stats
                if not buffer and not tool_calls:
                    live.update(Markdown("".join(thinking)))
    content = "".join(buffer) or ("".join(thinking) if not tool_calls else "")
    return StreamResult(content=content, tool_calls=tool_calls, stats=stats)


def format_tool_call(call: ToolCall) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
    return f"{call.name}({args})"


def print_tool_call(call: ToolCall) -> None:
    console.print(f"[{ACCENT}]›[/{ACCENT}] [bold]{format_tool_call(call)}[/bold]")


def print_tool_result(call: ToolCall, result: ToolResult) -> None:
    if result.summary == "declined":
        console.print("  [yellow]skipped — you said no[/yellow]")
        return
    style = "green" if result.ok else "red"
    console.print(f"  [{style}]{result.display()}[/{style}]")


def confirm(request: ConfirmRequest) -> bool:
    """Show what a tool wants to do and ask the user to approve it."""
    border = "red" if request.danger else ACCENT
    body = _render_confirm_detail(request.detail)
    console.print(Panel(body, title=request.title, border_style=border, expand=False))
    try:
        return Confirm.ask("Proceed?", default=not request.danger)
    except (KeyboardInterrupt, EOFError):
        console.print("[dim]cancelled[/dim]")
        return False


def auto_confirm(request: ConfirmRequest) -> bool:
    """Non-interactive confirm: approve everything except flagged-dangerous actions.

    For scripting/CI (`lydia ask --yes`) where there's no one to ask.
    Mirrors the same "always confirm dangerous things" invariant the
    interactive path enforces — with no human present, dangerous defaults
    to declined rather than silently approved.
    """
    approved = not request.danger
    verdict = "auto-approved" if approved else "auto-declined (flagged dangerous)"
    console.print(f"[dim]{verdict}: {request.title}[/dim]")
    return approved


def _render_confirm_detail(detail: str):
    stripped = detail.strip()
    looks_like_diff = stripped.startswith("---") or "\n@@ " in stripped or stripped.startswith("@@ ")
    if looks_like_diff and stripped != "(no changes — file content is identical)":
        return Syntax(detail, "diff", background_color="default", word_wrap=True)
    return Text(detail)


def format_stats(stats: dict) -> str | None:
    """Human-readable one-liner like '412 tokens · 9.3s · 44 tok/s'."""
    truncated = stats.get("done_reason") == "length"
    eval_count = stats.get("eval_count")
    total_ns = stats.get("total_duration")
    if not eval_count or not total_ns:
        return "reply cut off by the token limit" if truncated else None
    seconds = total_ns / 1e9
    rate = eval_count / seconds if seconds else 0
    line = f"{eval_count} tokens · {seconds:.1f}s · {rate:.0f} tok/s"
    return f"{line} · cut off by token limit" if truncated else line
