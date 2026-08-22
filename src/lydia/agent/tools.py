"""The tool registry: what Lydia can do, and how dangerous each action is.

Each ToolSpec pairs a JSON-schema definition (sent to the model so it knows
what it can call) with a handler that performs the real work by delegating
to `lydia.tools.*`. Handlers may raise `ToolError`; the agent loop turns
that into a message the model can read and react to.

Risk levels:
    safe    — runs immediately, no confirmation (reads, git status/diff/add)
    confirm — shown to the user for a yes/no before running (file writes/
              edits/deletes, git commit, git push) — unless config.mode is
              "auto" and the request isn't flagged dangerous, see
              _confirm_or_auto.
    command — arbitrary shell; policy decided by config.mode combined with
              lydia.tools.terminal.classify_command

Session modes (config.mode, see config/settings.py):
    ask   — confirm every confirm/command-tier action (today's default).
    auto  — skip confirmation for non-dangerous actions; still confirm
            anything flagged dangerous (delete_file, git_push, a
            classify_command-flagged-dangerous shell command).
    plan  — research only: filter_for_mode strips every mutating tool
            (MUTATING_TOOL_NAMES) out of the registry entirely, so the
            model can't call them at all, regardless of confirmation.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from lydia.agent import facts
from lydia.config.settings import LydiaConfig
from lydia.connectors import ConnectorError
from lydia.llm.protocol import ModelClient
from lydia.tools import filesystem, git
from lydia.tools.filesystem import ToolError
from lydia.tools.terminal import classify_command, run_command

Risk = Literal["safe", "confirm", "command"]

MAX_TOOL_OUTPUT_CHARS = 6000


@dataclass
class ConfirmRequest:
    """Something a tool wants to do that needs the user's yes/no."""

    title: str
    detail: str
    danger: bool = False


@dataclass
class Todo:
    """One item in the session's ephemeral task checklist (see update_todos).

    Unlike agent/facts.py's Fact, this is never persisted to disk — it's a
    live scratchpad for one run, mirroring Claude Code's own TodoWrite tool.
    """

    content: str
    status: Literal["pending", "in_progress", "completed"]


@dataclass
class ToolContext:
    root: Path
    config: LydiaConfig
    confirm: Callable[[ConfirmRequest], bool]
    client: ModelClient | None = None  # reused for tools that need model access (e.g. search_semantic)
    # Threaded in from the same list object across turns by whoever owns the
    # session (e.g. cli/chat.py::ChatSession) so mutations here are visible
    # next turn; non-interactive callers just get a throwaway list per call.
    todos: list[Todo] = field(default_factory=list)


@dataclass
class ToolResult:
    ok: bool
    content: str  # fed back to the model verbatim
    summary: str = ""  # short line for the console; falls back to content

    def display(self) -> str:
        return self.summary or _truncate(self.content, 200)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: Risk
    handler: Callable[[dict[str, Any], ToolContext], ToolResult]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"


def _declined(what: str) -> ToolResult:
    return ToolResult(
        ok=False,
        content=(
            f"DECLINED. The user said no to {what}. Nothing was changed on disk or in git. "
            "Do not tell the user this action succeeded or was applied — it was not. "
            "Explain that it was declined and ask how they'd like to proceed."
        ),
        summary="declined",
    )


def _confirm_or_auto(ctx: ToolContext, request: ConfirmRequest) -> bool:
    """Skip the confirmation prompt in auto mode, unless the request is flagged dangerous."""
    if ctx.config.mode == "auto" and not request.danger:
        return True
    return ctx.confirm(request)


# -- filesystem ---------------------------------------------------------


def _read_file(args: dict, ctx: ToolContext) -> ToolResult:
    content = filesystem.read_file(ctx.root, args["path"], args.get("start_line", 1), args.get("end_line"))
    return ToolResult(ok=True, content=_truncate(content), summary=f"read {args['path']}")


def _list_dir(args: dict, ctx: ToolContext) -> ToolResult:
    content = filesystem.list_dir(ctx.root, args.get("path", "."))
    return ToolResult(ok=True, content=content, summary=f"listed {args.get('path', '.')}")


def _search_code(args: dict, ctx: ToolContext) -> ToolResult:
    content = filesystem.search_code(ctx.root, args["pattern"], args.get("path", "."))
    return ToolResult(ok=True, content=_truncate(content), summary=f"searched for '{args['pattern']}'")


def _find_files(args: dict, ctx: ToolContext) -> ToolResult:
    content = filesystem.find_files(ctx.root, args["pattern"], args.get("path", "."))
    return ToolResult(ok=True, content=_truncate(content), summary=f"found files matching '{args['pattern']}'")


def _search_semantic(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.context import retriever

    if not retriever.is_indexed(ctx.root):
        return ToolResult(
            ok=False,
            content="No semantic index for this project yet. Tell the user to run "
            "`lydia index` first, or use search_code for literal substring search instead.",
            summary="not indexed",
        )
    if ctx.client is None:
        return ToolResult(ok=False, content="No Ollama connection available for semantic search.", summary="error")
    if ctx.config.provider != "ollama":
        # The index's embedding vectors are tied to whatever model built
        # them (indexer.py::EMBED_MODEL, currently always Ollama's
        # nomic-embed-text — see build_semantic_index's own guard in
        # cli/main.py) — nothing tracks dimensionality/model per-index, so
        # querying it through a different provider's embed() wouldn't
        # just fail cleanly, it could silently compare incompatible
        # vectors. Refuse rather than guess.
        return ToolResult(
            ok=False,
            content=f"Semantic search needs the ollama provider (currently: {ctx.config.provider}). "
            "Use search_code for literal search instead.",
            summary="unsupported with this provider",
        )

    query = args["query"]
    results = retriever.search(ctx.root, ctx.client, query, top_k=args.get("top_k", 8))
    if not results:
        return ToolResult(ok=True, content="No relevant chunks found.", summary="no matches")
    body = "\n\n".join(
        f"{r.path}:{r.start_line}-{r.end_line} (relevance {r.score:.2f})\n{r.text}" for r in results
    )
    return ToolResult(ok=True, content=_truncate(body), summary=f"semantic search: '{query}' ({len(results)} results)")


def _write_file(args: dict, ctx: ToolContext) -> ToolResult:
    path, content = args["path"], args["content"]
    proposal = filesystem.propose_write(ctx.root, path, content)
    verb = "Create" if proposal.is_new_file else "Update"
    approved = _confirm_or_auto(ctx, ConfirmRequest(title=f"{verb} {path}", detail=proposal.diff))
    if not approved:
        return _declined(f"writing {path}")
    message = filesystem.apply_write(ctx.root, proposal)
    return ToolResult(ok=True, content=message, summary=message)


def _edit_file(args: dict, ctx: ToolContext) -> ToolResult:
    path = args["path"]
    proposal = filesystem.propose_edit(
        ctx.root, path, args["old_string"], args["new_string"], args.get("replace_all", False),
    )
    approved = _confirm_or_auto(ctx, ConfirmRequest(title=f"Edit {path}", detail=proposal.diff))
    if not approved:
        return _declined(f"editing {path}")
    message = filesystem.apply_write(ctx.root, proposal)
    return ToolResult(ok=True, content=message, summary=message)


def _multi_edit_file(args: dict, ctx: ToolContext) -> ToolResult:
    path, edits = args["path"], args["edits"]
    proposal = filesystem.propose_multi_edit(ctx.root, path, edits)
    approved = _confirm_or_auto(
        ctx, ConfirmRequest(title=f"Edit {path} ({len(edits)} changes)", detail=proposal.diff),
    )
    if not approved:
        return _declined(f"editing {path}")
    message = filesystem.apply_write(ctx.root, proposal)
    return ToolResult(ok=True, content=message, summary=message)


def _delete_file(args: dict, ctx: ToolContext) -> ToolResult:
    path = args["path"]
    approved = _confirm_or_auto(ctx, ConfirmRequest(
        title=f"Delete {path}",
        detail=f"This will permanently delete {path} (a backup is kept in .lydia/backups/).",
        danger=True,
    ))
    if not approved:
        return _declined(f"deleting {path}")
    message = filesystem.apply_delete(ctx.root, path)
    return ToolResult(ok=True, content=message, summary=message)


# -- terminal -------------------------------------------------------------


def _run_command(args: dict, ctx: ToolContext) -> ToolResult:
    command = args["command"]
    risk = classify_command(command)
    needs_confirm = ctx.config.mode == "ask" or risk == "dangerous"
    if needs_confirm:
        approved = ctx.confirm(ConfirmRequest(
            title=f"Run: {command}",
            detail=command,
            danger=(risk == "dangerous"),
        ))
        if not approved:
            return _declined(f"running `{command}`")
    result = run_command(command, ctx.root)
    body = f"exit code: {result.returncode}\n"
    if result.stdout.strip():
        body += f"stdout:\n{result.stdout.strip()}\n"
    if result.stderr.strip():
        body += f"stderr:\n{result.stderr.strip()}\n"
    summary = f"ran `{command}` (exit {result.returncode})"
    return ToolResult(ok=result.success, content=_truncate(body), summary=summary)


# -- git --------------------------------------------------------------------


def _git_status(args: dict, ctx: ToolContext) -> ToolResult:
    return ToolResult(ok=True, content=git.status(ctx.root), summary="checked git status")


def _git_diff(args: dict, ctx: ToolContext) -> ToolResult:
    content = git.diff(ctx.root, staged=args.get("staged", False), path=args.get("path"))
    return ToolResult(ok=True, content=_truncate(content), summary="checked git diff")


def _git_add(args: dict, ctx: ToolContext) -> ToolResult:
    message = git.add(ctx.root, args["paths"])
    return ToolResult(ok=True, content=message, summary=message)


def _git_commit(args: dict, ctx: ToolContext) -> ToolResult:
    message = args["message"]
    approved = _confirm_or_auto(ctx, ConfirmRequest(title="Commit", detail=message))
    if not approved:
        return _declined("creating this commit")
    result = git.commit(ctx.root, message)
    return ToolResult(ok=True, content=result, summary=result)


def _git_push(args: dict, ctx: ToolContext) -> ToolResult:
    remote, branch = args.get("remote", "origin"), args.get("branch")
    label = f"{remote}/{branch}" if branch else remote
    approved = _confirm_or_auto(ctx, ConfirmRequest(
        title=f"Push to {label}",
        detail=f"This pushes local commits to {label}, a shared/remote location.",
        danger=True,
    ))
    if not approved:
        return _declined(f"pushing to {label}")
    result = git.push(ctx.root, remote, branch)
    return ToolResult(ok=True, content=result, summary=result)


# -- memory -----------------------------------------------------------------


def _remember(args: dict, ctx: ToolContext) -> ToolResult:
    fact = facts.remember(ctx.root, args["fact"])
    message = f"Remembered: {fact.text}"
    return ToolResult(ok=True, content=message, summary=message)


# -- task checklist (ephemeral, not persisted) -------------------------------

_TODO_GLYPHS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


def _render_todos(todos: list[Todo]) -> str:
    if not todos:
        return "(no todos)"
    return "\n".join(f"{_TODO_GLYPHS[t.status]} {t.content}" for t in todos)


def _update_todos(args: dict, ctx: ToolContext) -> ToolResult:
    items = args["todos"]
    parsed: list[Todo] = []
    for item in items:
        status = item.get("status")
        if status not in _TODO_GLYPHS:
            raise ToolError(f"Invalid todo status '{status}'. Use one of: pending, in_progress, completed.")
        parsed.append(Todo(content=item["content"], status=status))
    ctx.todos.clear()
    ctx.todos.extend(parsed)
    rendered = _render_todos(ctx.todos)
    return ToolResult(ok=True, content=rendered, summary=rendered)


# -- personal assistant ------------------------------------------------------
# Connector modules (Gmail/Outlook/Canvas/yfinance/feedparser) are imported
# lazily inside each handler, same pattern as _search_semantic above, so
# `lydia ask`/coding-only sessions never pay for those imports.


def _check_email(args: dict, ctx: ToolContext) -> ToolResult:
    account = args.get("account")
    if account == "personal":
        from lydia.config import secrets
        from lydia.connectors.email_gmail import format_emails, get_recent_emails

        credentials_json = secrets.get_secret(secrets.GMAIL_REFRESH_TOKEN)
        if not credentials_json:
            return ToolResult(
                ok=False,
                content="Not logged in to Gmail. Tell the user to run `lydia auth login gmail`.",
                summary="not logged in",
            )
        try:
            summaries = get_recent_emails(credentials_json)
        except ConnectorError as exc:
            return ToolResult(ok=False, content=str(exc), summary="error")
        return ToolResult(
            ok=True, content=format_emails(summaries),
            summary=f"checked personal email ({len(summaries)} messages)",
        )
    if account == "school":
        from lydia.connectors.auth import outlook_oauth
        from lydia.connectors.email_outlook import format_emails, get_recent_emails

        try:
            token = outlook_oauth.get_access_token()
            summaries = get_recent_emails(token)
        except (outlook_oauth.OutlookAuthError, ConnectorError) as exc:
            return ToolResult(ok=False, content=str(exc), summary="error")
        return ToolResult(
            ok=True, content=format_emails(summaries),
            summary=f"checked school email ({len(summaries)} messages)",
        )
    return ToolResult(ok=False, content=f"Unknown account '{account}'. Use 'personal' or 'school'.", summary="error")


def _check_canvas(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.config import secrets
    from lydia.connectors.canvas import format_assignments, get_upcoming_assignments

    base_url = ctx.config.canvas_base_url
    token = secrets.get_secret(secrets.CANVAS_TOKEN)
    if not base_url or not token:
        return ToolResult(
            ok=False,
            content="Canvas isn't set up. Tell the user to run `lydia auth login canvas`.",
            summary="not configured",
        )
    try:
        assignments = get_upcoming_assignments(base_url, token)
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc), summary="error")
    return ToolResult(
        ok=True, content=format_assignments(assignments),
        summary=f"checked Canvas ({len(assignments)} upcoming assignments)",
    )


def _check_stocks(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.connectors.stocks import format_market_summary, get_market_summary

    try:
        snapshots = get_market_summary()
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc), summary="error")
    return ToolResult(ok=True, content=format_market_summary(snapshots), summary="checked stock market")


def _check_news(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.connectors.news import format_news, get_ai_news

    try:
        items = get_ai_news()
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc), summary="error")
    return ToolResult(ok=True, content=format_news(items), summary=f"checked AI news ({len(items)} headlines)")


def _check_weather(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.connectors import weather

    location = args.get("location") or ctx.config.weather_location
    try:
        return ToolResult(ok=True, content=weather.get_weather(location=location))
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc))


def _check_calendar(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.connectors import calendar_mac

    try:
        return ToolResult(ok=True, content=calendar_mac.get_events(days=args.get("days", 2)))
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc))


def _send_notification(args: dict, ctx: ToolContext) -> ToolResult:
    from lydia.config import secrets
    from lydia.connectors import ntfy

    topic = secrets.get_secret(secrets.NTFY_TOPIC)
    if not topic:
        return ToolResult(
            ok=False,
            content="Phone notifications aren't set up. Tell the user to run `lydia auth login ntfy`.",
            summary="not configured",
        )
    try:
        ntfy.send_push(topic, args.get("title", "Lydia"), args["message"])
    except ConnectorError as exc:
        return ToolResult(ok=False, content=str(exc), summary="error")
    return ToolResult(ok=True, content="Notification sent to the user's phone.",
                      summary="sent push notification")


def _open_item(args: dict, ctx: ToolContext) -> ToolResult:
    """Open a macOS app by name, or a file/folder by path, via `open`."""
    target = str(args.get("target", "")).strip()
    if not target:
        return ToolResult(ok=False, content="Nothing to open: 'target' is required.")
    from pathlib import Path as _Path

    looks_like_path = "/" in target or target.startswith("~")
    if looks_like_path:
        path = _Path(target).expanduser()
        if not path.exists():
            return ToolResult(ok=False, content=f"No such file or folder: {target}")
        cmd = ["open", str(path)]
    else:
        cmd = ["open", "-a", target]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return ToolResult(ok=False, content=f"Could not open {target}: {(result.stderr or '').strip()}")
    return ToolResult(ok=True, content=f"Opened {target}.")


# Tools that change something on disk or in git. Plan mode strips exactly
# these out of the registry, regardless of risk tier — git_add is "safe"
# risk (no confirmation needed) but still mutates the index, so risk tier
# alone isn't the right signal for "should this even be offered in plan mode."
MUTATING_TOOL_NAMES = {
    "write_file", "edit_file", "multi_edit_file", "delete_file",
    "run_command", "git_add", "git_commit", "git_push",
}


def filter_for_mode(registry: list[ToolSpec], mode: str) -> list[ToolSpec]:
    """The tools actually offered to the model this turn, given the session mode."""
    if mode == "plan":
        return [spec for spec in registry if spec.name not in MUTATING_TOOL_NAMES]
    return registry


def build_registry() -> list[ToolSpec]:
    return [
        ToolSpec(
            "read_file", "Read a text file from the project, with line numbers.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "start_line": {"type": "integer", "description": "1-based first line (optional)"},
                    "end_line": {"type": "integer", "description": "1-based last line (optional)"},
                },
                "required": ["path"],
            },
            "safe", _read_file,
        ),
        ToolSpec(
            "list_dir", "List the files and subdirectories directly inside a directory.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory relative to the project root, default '.'"},
                },
            },
            "safe", _list_dir,
        ),
        ToolSpec(
            "search_code", "Search project files for a substring and return matching file:line:text.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Text to search for"},
                    "path": {"type": "string", "description": "Restrict search to this file or directory, default '.'"},
                },
                "required": ["pattern"],
            },
            "safe", _search_code,
        ),
        ToolSpec(
            "find_files",
            "Find files by name/path pattern (e.g. '*.py', 'test_*.py'), not by content — "
            "use search_code for content. Pattern matching isn't path-segment-aware, so "
            "'*.py' matches nested paths too (e.g. src/foo.py).",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Filename/path glob pattern, e.g. '*.py'"},
                    "path": {"type": "string", "description": "Restrict search to this directory, default '.'"},
                },
                "required": ["pattern"],
            },
            "safe", _find_files,
        ),
        ToolSpec(
            "search_semantic",
            "Search the project by meaning rather than exact text, using an embedding index "
            "(requires `lydia index` to have been run first). Best for 'where is X handled' "
            "style questions when you don't know the exact wording to grep for. Use search_code "
            "instead when you know the literal string.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language description of what you're looking for"},
                    "top_k": {"type": "integer", "description": "Number of results to return, default 8"},
                },
                "required": ["query"],
            },
            "safe", _search_semantic,
        ),
        ToolSpec(
            "write_file",
            "Create a new file or overwrite an existing one with full new content. "
            "Prefer edit_file for a targeted change to an existing file — use write_file "
            "for new files or full-file rewrites. Always shows a diff and asks the user "
            "to approve before writing (unless mode is auto).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "content": {"type": "string", "description": "The complete new file content"},
                },
                "required": ["path", "content"],
            },
            "confirm", _write_file,
        ),
        ToolSpec(
            "edit_file",
            "Replace an exact snippet of text in an existing file with new text — the "
            "preferred way to make a targeted change without rewriting the whole file. "
            "old_string must match the file's current content exactly and must be unique "
            "within the file unless replace_all is set. Always shows a diff and asks the "
            "user to approve before writing (unless mode is auto).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "old_string": {"type": "string", "description": "The exact existing text to replace"},
                    "new_string": {"type": "string", "description": "The text to replace it with"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence of old_string instead of requiring exactly one",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            "confirm", _edit_file,
        ),
        ToolSpec(
            "multi_edit_file",
            "Apply several find/replace edits to one existing file in a single call — "
            "preferred over multiple edit_file calls when making several distinct changes "
            "to the same file, since it shows one diff and asks for one approval instead "
            "of several. Edits are applied in order, each against the result of the one "
            "before it, so a later edit can target text introduced by an earlier one.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "edits": {
                        "type": "array",
                        "description": "Edits to apply in order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_string": {"type": "string", "description": "The exact existing text to replace"},
                                "new_string": {"type": "string", "description": "The text to replace it with"},
                                "replace_all": {
                                    "type": "boolean",
                                    "description": "Replace every occurrence of old_string instead of requiring exactly one",
                                },
                            },
                            "required": ["old_string", "new_string"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
            "confirm", _multi_edit_file,
        ),
        ToolSpec(
            "delete_file", "Permanently delete a file (a backup is kept). Asks the user to approve first.",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the project root"}},
                "required": ["path"],
            },
            "confirm", _delete_file,
        ),
        ToolSpec(
            "run_command",
            "Run a shell command in the project root and return its output. "
            "Destructive commands are always confirmed with the user first.",
            {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The shell command to run"}},
                "required": ["command"],
            },
            "command", _run_command,
        ),
        ToolSpec(
            "git_status", "Show the working tree status (changed/staged/untracked files).",
            {"type": "object", "properties": {}}, "safe", _git_status,
        ),
        ToolSpec(
            "git_diff", "Show unstaged (or staged) changes as a unified diff.",
            {
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "description": "Show staged changes instead of unstaged"},
                    "path": {"type": "string", "description": "Restrict the diff to this path"},
                },
            },
            "safe", _git_diff,
        ),
        ToolSpec(
            "git_add", "Stage files for commit.",
            {
                "type": "object",
                "properties": {"paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to stage"}},
                "required": ["paths"],
            },
            "safe", _git_add,
        ),
        ToolSpec(
            "git_commit", "Commit currently staged changes. Always confirmed with the user first.",
            {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Commit message"}},
                "required": ["message"],
            },
            "confirm", _git_commit,
        ),
        ToolSpec(
            "git_push", "Push committed changes to a remote. Always confirmed with the user first.",
            {
                "type": "object",
                "properties": {
                    "remote": {"type": "string", "description": "Remote name, default 'origin'"},
                    "branch": {"type": "string", "description": "Branch name, default the current branch"},
                },
            },
            "confirm", _git_push,
        ),
        ToolSpec(
            "remember",
            "Save a short, durable fact about this project so it's remembered in future "
            "sessions (tech stack, conventions, decisions). Not for one-off task details.",
            {
                "type": "object",
                "properties": {"fact": {"type": "string", "description": "The fact, written as a standalone statement"}},
                "required": ["fact"],
            },
            "safe", _remember,
        ),
        ToolSpec(
            "update_todos",
            "Show a visible, updatable checklist for a multi-step task (roughly 3+ "
            "distinct steps) so progress is visible as you work — especially useful "
            "in auto mode, where there's no per-step confirmation prompt. Not for "
            "trivial one-shot requests. Always send the FULL current list, not just "
            "what changed — this call replaces the whole checklist.",
            {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "The task, as a short standalone statement"},
                                "status": {
                                    "type": "string", "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
            "safe", _update_todos,
        ),
        ToolSpec(
            "check_email",
            "Check recent email in the user's personal Gmail or school Outlook inbox.",
            {
                "type": "object",
                "properties": {
                    "account": {
                        "type": "string", "enum": ["personal", "school"],
                        "description": "Which inbox to check: 'personal' (Gmail) or 'school' (Outlook)",
                    },
                },
                "required": ["account"],
            },
            "safe", _check_email,
        ),
        ToolSpec(
            "check_canvas", "Check upcoming Canvas assignments across the user's active courses.",
            {"type": "object", "properties": {}}, "safe", _check_canvas,
        ),
        ToolSpec(
            "check_stocks", "Get a general stock market snapshot (S&P 500, Nasdaq, Dow) — not a personal portfolio.",
            {"type": "object", "properties": {}}, "safe", _check_stocks,
        ),
        ToolSpec(
            "check_news", "Get recent AI news headlines from a curated set of sources.",
            {"type": "object", "properties": {}}, "safe", _check_news,
        ),
        ToolSpec(
            "notify", "Send a push notification to the user's phone. Use for genuinely "
            "important, time-sensitive information — not routine replies.",
            {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The notification body"},
                    "title": {"type": "string", "description": "Short title (optional)"},
                },
                "required": ["message"],
            },
            "safe", _send_notification,
        ),
        ToolSpec(
            "check_weather", "Current weather and 2-day forecast for a place (or the user's location).",
            {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City or place name (optional; defaults to the user's location)"}},
                "required": [],
            },
            "safe", _check_weather,
        ),
        ToolSpec(
            "check_calendar", "Upcoming events from the user's macOS Calendar.",
            {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "How many days ahead to look (1-14, default 2)"}},
                "required": [],
            },
            "safe", _check_calendar,
        ),
        ToolSpec(
            "open_app", "Open a macOS application by name, or a file/folder by path.",
            {
                "type": "object",
                "properties": {"target": {"type": "string", "description": "App name (e.g. 'Spotify') or a file/folder path"}},
                "required": ["target"],
            },
            "safe", _open_item,
        ),
    ]
