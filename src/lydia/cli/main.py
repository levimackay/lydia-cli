"""Lydia command-line entry point.

    lydia                      interactive chat (default)
    lydia ask "question"       one-shot question, prints the answer
    lydia analyze              summarize the current project
    lydia index                build/refresh the semantic search index
    lydia restore list         list file backups
    lydia restore apply N      restore a file to a backed-up version
    lydia models               list installed Ollama models
    lydia init                 create .lydia/ in the current project
    lydia config show          print effective configuration
    lydia config set KEY VAL   set a config value (global or --project)
    lydia auth login PROVIDER  connect gmail, outlook, or canvas
    lydia auth status          show which sources are connected
    lydia auth logout PROVIDER disconnect a source
    lydia briefing run         generate today's personal briefing
    lydia briefing show        print the last generated briefing
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import typer
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table

from lydia import __version__
from lydia.agent import facts
from lydia.cli import ui
from lydia.cli.chat import resolve_model, run_chat
from lydia.cli.optional_deps import require_extra
from lydia.config.settings import (
    LydiaConfig,
    coerce_value,
    find_project_root,
    global_config_path,
    load_config,
    project_config_path,
    save_config_value,
)
from lydia.context.indexer import EMBED_MODEL, build_index
from lydia.context.scanner import scan_project
from lydia.llm.client import OllamaError
from lydia.llm.factory import build_client
from lydia.llm.protocol import ModelClient
from lydia.llm.types import Message
from lydia.tools.filesystem import apply_write, list_backups, restore_backup

app = typer.Typer(
    name="lydia",
    help="Lydia — a local AI coding agent powered by Ollama.",
    add_completion=False,
    no_args_is_help=False,
)
config_app = typer.Typer(help="View and change configuration.")
app.add_typer(config_app, name="config")
memory_app = typer.Typer(help="View and manage remembered project facts.")
app.add_typer(memory_app, name="memory")
restore_app = typer.Typer(help="List and restore file backups made by write_file/delete_file.")
app.add_typer(restore_app, name="restore")
auth_app = typer.Typer(help="Connect Lydia to Gmail, Outlook, and Canvas for the personal-assistant tools.")
app.add_typer(auth_app, name="auth")
briefing_app = typer.Typer(help="Generate and view Lydia's daily personal briefing.")
app.add_typer(briefing_app, name="briefing")
schedule_app = typer.Typer(help="Manage the daily scheduled briefing (macOS launchd).")
briefing_app.add_typer(schedule_app, name="schedule")
automations_app = typer.Typer(help="Create and manage plain-English automations.")
app.add_typer(automations_app, name="automations")
automations_schedule_app = typer.Typer(help="Manage the automations heartbeat (macOS launchd).")
automations_app.add_typer(automations_schedule_app, name="schedule")
listen_app = typer.Typer(
    invoke_without_command=True,
    help="Always-listening voice assistant (\"Hey Jarvis\").",
)
app.add_typer(listen_app, name="listen")


def _memory_root() -> Path:
    return find_project_root() or Path.cwd()


@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Print version and exit."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if version:
        ui.console.print(f"lydia {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        raise typer.Exit(run_chat(load_config()))


@app.command()
def ask(
    question: str = typer.Argument(..., help="A single question for Lydia."),
    model: str | None = typer.Option(None, "--model", "-m", help="Override the model."),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Give Lydia tool access (read/write/run/git) for this question, "
        "auto-approving anything that isn't flagged dangerous. For scripts/CI "
        "where there's no one to answer a y/n prompt.",
    ),
) -> None:
    """Ask one question and print the answer (useful for scripts)."""
    config = load_config()
    if model:
        config.model = model
    with build_client(config) as client:
        try:
            resolved = resolve_model(client, config)
            if yes:
                reply, _ = _ask_with_tools(client, resolved, config, question)
            else:
                reply, _ = ui.stream_response(
                    client.chat_stream(
                        model=resolved,
                        messages=[Message(role="user", content=question)],
                        temperature=config.temperature,
                        num_ctx=config.num_ctx,
                        think=config.think_flag,
                        keep_alive=config.keep_alive,
                    )
                )
        except OllamaError as exc:
            ui.print_error(str(exc))
            raise typer.Exit(1)
    if not reply.strip():
        raise typer.Exit(1)


def _ask_with_tools(client: ModelClient, model: str, config: LydiaConfig, question: str) -> tuple[str, dict]:
    """Run one question through the full agent loop (tools + auto-confirm)."""
    from lydia.agent.loop import run_agent_turn
    from lydia.agent.prompts import build_system_prompt
    from lydia.agent.tools import ToolContext, build_registry

    root = find_project_root() or Path.cwd()
    summary = scan_project(root)
    ctx = ToolContext(root=root, config=config, confirm=ui.auto_confirm, client=client)
    messages = [Message(role="user", content=question)]
    return run_agent_turn(
        client=client, model=model, temperature=config.temperature,
        num_ctx=config.num_ctx, think=config.think_flag, keep_alive=config.keep_alive,
        system_prompt=build_system_prompt(summary), messages=messages,
        registry=build_registry(), ctx=ctx, stream_fn=ui.stream_agent_response,
        on_tool_call=ui.print_tool_call, on_tool_result=ui.print_tool_result,
    )


@app.command()
def analyze(
    path: Path = typer.Argument(Path("."), help="Project directory to analyze."),
) -> None:
    """Scan a project and print a summary."""
    if not path.is_dir():
        ui.print_error(f"Not a directory: {path}")
        raise typer.Exit(1)
    summary = scan_project(path)

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", min_width=12)
    table.add_column()
    table.add_row("Project", f"[bold]{summary.project_kind}[/bold]")
    table.add_row("Root", str(summary.root))
    table.add_row("Files", str(summary.file_count))
    table.add_row("Code lines", f"{summary.total_lines:,}")
    languages = "  ".join(f"{name} [bold]{pct}%[/bold]" for name, pct in summary.languages.items())
    table.add_row("Languages", languages or "[dim]none detected[/dim]")
    if summary.manifest_files:
        table.add_row("Key files", "\n".join(summary.manifest_files[:8]))
    if summary.largest_source_files:
        largest = "\n".join(f"{p}  [dim]{n:,} lines[/dim]" for p, n in summary.largest_source_files[:5])
        table.add_row("Largest", largest)
    ui.console.print(table)


@app.command(name="index")
def build_semantic_index(
    path: Path = typer.Argument(Path("."), help="Project directory to index."),
    force: bool = typer.Option(False, "--force", "-f", help="Re-embed every file, ignoring the incremental cache."),
) -> None:
    """Build or refresh the semantic search index (used by the search_semantic tool)."""
    if not path.is_dir():
        ui.print_error(f"Not a directory: {path}")
        raise typer.Exit(1)
    root = path.resolve()
    config = load_config(project_root=root)
    if config.provider != "ollama":
        # See agent/tools.py::_search_semantic's matching guard — the index
        # format is tied to whichever model embeds it (EMBED_MODEL below,
        # currently always Ollama's), and nothing tracks that per-index, so
        # building (or querying) it through another provider isn't just
        # unsupported, it'd risk silently mixing incompatible vectors.
        ui.print_error(
            f"Semantic indexing needs the ollama provider (currently: {config.provider}). "
            "Run `lydia config set provider ollama` first, or skip this — search_code "
            "(literal search) works regardless of provider."
        )
        raise typer.Exit(1)
    with build_client(config) as client:
        if not client.is_alive():
            target = config.server_url or config.ollama_host
            ui.print_error(f"Cannot reach {target}.")
            raise typer.Exit(1)
        if not client.has_model(EMBED_MODEL):
            ui.print_error(f"Embedding model not installed. Run `ollama pull {EMBED_MODEL}` first.")
            raise typer.Exit(1)
        with ui.console.status("Indexing..."):
            stats = build_index(root, client, force=force)
    ui.print_info(
        f"Scanned {stats.files_scanned} file(s); embedded {stats.files_indexed} changed file(s) "
        f"({stats.chunks_indexed} chunks); removed {stats.files_removed} deleted file(s) from the index."
    )


@app.command()
def models() -> None:
    """List models installed in Ollama."""
    config = load_config()
    with build_client(config) as client:
        try:
            installed = client.list_models()
        except OllamaError as exc:
            ui.print_error(str(exc))
            raise typer.Exit(1)
    if not installed:
        ui.print_info("No models installed. Try `ollama pull qwen3.5`.")
        return
    table = Table(box=None)
    table.add_column("Model", style="bold")
    table.add_column("Size", style="dim")
    for m in installed:
        table.add_row(m.name, m.size_human)
    ui.console.print(table)


VERIFY_COMMAND_GUESSES = {
    "pyproject.toml": "pytest -q",
    "package.json": "npm test",
    "Cargo.toml": "cargo test",
    "go.mod": "go test ./...",
}


def _guess_verify_command(manifest_files: list[str]) -> str | None:
    """A verify_command suggestion if exactly one recognized manifest type is present."""
    matches = {
        VERIFY_COMMAND_GUESSES[Path(name).name]
        for name in manifest_files
        if Path(name).name in VERIFY_COMMAND_GUESSES
    }
    return matches.pop() if len(matches) == 1 else None


@app.command()
def init() -> None:
    """Create a .lydia/ directory in the current project."""
    root = Path.cwd()
    config_file = project_config_path(root)
    if config_file.exists():
        ui.print_info(f"Already initialized: {config_file}")
        return
    config_file.parent.mkdir(parents=True, exist_ok=True)
    guessed = _guess_verify_command(scan_project(root).manifest_files)
    config_data = {"verify_command": guessed} if guessed else {}
    config_file.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")
    gitignore = config_file.parent / ".gitignore"
    gitignore.write_text("history/\nbackups/\nindex.sqlite3\n", encoding="utf-8")
    ui.print_info(f"Initialized {config_file.parent}/ (history/, backups/, and index.sqlite3 are git-ignored)")
    if guessed:
        ui.print_info(f"Suggested verify_command: {guessed} — edit or clear it in {config_file} if that's not right.")
    else:
        ui.print_info(
            "No verify_command guessed. Set one with `lydia config set verify_command \"...\" --project` "
            "if you want Lydia to run tests/lint after making changes."
        )


@config_app.command("show")
def config_show() -> None:
    """Print the effective merged configuration."""
    config = load_config()
    root = find_project_root()
    ui.console.print(f"[dim]global:[/dim]  {global_config_path()}")
    if root:
        ui.console.print(f"[dim]project:[/dim] {project_config_path(root)}")
    unset_labels = {"model": "auto", "server_url": "not set (using local Ollama)", "api_key": "not set"}
    for key, value in vars(config).items():
        shown = value if value is not None else f"[dim]{unset_labels.get(key, 'not set')}[/dim]"
        ui.console.print(f"  {key} = {shown}")
    # Provider API keys live in the OS keychain (config/secrets.py), never
    # in this JSON config, so they're not in vars(config) above — shown
    # separately, presence only, never the value itself.
    from lydia.config import secrets as _secrets

    try:
        has_gemini_key = bool(_secrets.get_secret(_secrets.GEMINI_API_KEY))
    except _secrets.SecretsError:
        has_gemini_key = False
    ui.console.print(f"  gemini_api_key = [dim]{'set' if has_gemini_key else 'not set'}[/dim]")


SECRET_KEYS = {"api_key"}

# Keys that don't go into config.json at all — routed to the OS keychain
# instead (config/secrets.py). Stronger than SECRET_KEYS above (which
# still writes plain JSON, just blocks --project): these are third-party,
# personally-billed API keys, the same category as Gmail/Outlook/Canvas
# credentials, not a self-issued bearer token like `api_key`.
KEYCHAIN_CONFIG_KEYS = {"gemini_api_key": "GEMINI_API_KEY"}


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key, e.g. model"),
    value: str | None = typer.Argument(
        None, help="New value. For a keychain key (e.g. gemini_api_key), omit this to be prompted without echoing."
    ),
    project: bool = typer.Option(False, "--project", "-p", help="Write to the project config instead of global."),
) -> None:
    """Set a configuration value, e.g. `lydia config set model qwen3.5:9b`."""
    if key in KEYCHAIN_CONFIG_KEYS:
        if project:
            ui.print_error(
                f"'{key}' is a provider API key — it always goes to the OS keychain, machine-wide, "
                "never a project config. Drop --project."
            )
            raise typer.Exit(1)
        if value is None:
            value = typer.prompt(f"{key}", hide_input=True)
        from lydia.config import secrets as _secrets

        secret_const = getattr(_secrets, KEYCHAIN_CONFIG_KEYS[key])
        try:
            _secrets.set_secret(secret_const, value)
        except _secrets.SecretsError as exc:
            ui.print_error(str(exc))
            raise typer.Exit(1)
        ui.print_info(f"Set {key} (stored in the system keychain, not printed here).")
        return

    if value is None:
        ui.print_error(f"'{key}' needs a value: lydia config set {key} <value>")
        raise typer.Exit(1)
    if project and key in SECRET_KEYS:
        ui.print_error(
            f"'{key}' can't be set with --project — <project>/.lydia/config.json is meant to be "
            "committed to git, and would leak this secret to anyone with repo access. "
            f"Run `lydia config set {key} <value>` without --project (global config, ~/.lydia/config.json, "
            "never part of any repo) instead."
        )
        raise typer.Exit(1)
    if project:
        root = find_project_root()
        if root is None:
            ui.print_error("Not inside a project (no .lydia/ or .git found). Run `lydia init` first.")
            raise typer.Exit(1)
        path = project_config_path(root)
    else:
        path = global_config_path()
    try:
        save_config_value(key, coerce_value(key, value), path)
    except (KeyError, ValueError) as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    ui.print_info(f"Set {key} = {value} in {path}")


@memory_app.command("list")
def memory_list() -> None:
    """List remembered facts about the current project."""
    remembered = facts.load_facts(_memory_root())
    if not remembered:
        ui.print_info("No facts remembered yet. Use `lydia memory add <fact>` to add one.")
        return
    for i, fact in enumerate(remembered, start=1):
        ui.console.print(f"  {i}. {fact.text}  [dim]{fact.created_at}[/dim]")


@memory_app.command("add")
def memory_add(fact: str = typer.Argument(..., help="The fact to remember")) -> None:
    """Remember a fact about the current project."""
    saved = facts.remember(_memory_root(), fact)
    ui.print_info(f"Remembered: {saved.text}")


@memory_app.command("forget")
def memory_forget(index: int = typer.Argument(..., help="Fact number, as shown by `lydia memory list`")) -> None:
    """Forget a remembered fact by number."""
    try:
        removed = facts.forget(_memory_root(), index)
    except ValueError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    ui.print_info(f"Forgot: {removed.text}")


@restore_app.command("list")
def restore_list() -> None:
    """List available backups, newest first."""
    root = find_project_root() or Path.cwd()
    entries = list_backups(root)
    if not entries:
        ui.print_info("No backups yet — they're created automatically when Lydia writes or deletes a file.")
        return
    for i, entry in enumerate(entries, start=1):
        ui.console.print(f"  {i}. {entry.path}  [dim]{entry.stamp}[/dim]")


@restore_app.command("apply")
def restore_apply(index: int = typer.Argument(..., help="Backup number, as shown by `lydia restore list`")) -> None:
    """Restore a file to a previous backed-up version."""
    root = find_project_root() or Path.cwd()
    entries = list_backups(root)
    if not 1 <= index <= len(entries):
        ui.print_error(f"No backup #{index}. There are {len(entries)}; see `lydia restore list`.")
        raise typer.Exit(1)
    entry = entries[index - 1]
    proposal = restore_backup(root, entry)
    ui.console.print(Syntax(proposal.diff, "diff", background_color="default", word_wrap=True))
    if not typer.confirm(f"Restore {entry.path} to its {entry.stamp} version?"):
        ui.print_info("Cancelled.")
        return
    message = apply_write(root, proposal)
    ui.print_info(message)


AUTH_PROVIDERS = ("gmail", "outlook", "canvas", "ntfy")


@auth_app.command("login")
def auth_login(
    provider: str = typer.Argument(..., help="gmail | outlook | canvas | ntfy"),
    client_id: str | None = typer.Option(
        None, "--client-id", help="Outlook only: the Azure app's Application (client) ID."
    ),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Canvas only: e.g. https://school.instructure.com"
    ),
    token: str | None = typer.Option(
        None, "--token", help="Canvas only: a personal access token (prompted if omitted)."
    ),
) -> None:
    """Connect a personal-assistant data source (Gmail, Outlook, Canvas, or ntfy)."""
    if provider == "gmail":
        with require_extra("assistant", "Gmail login"):
            from lydia.connectors.auth import gmail_oauth
            ui.print_info("Opening a browser to sign in to Gmail...")
            try:
                gmail_oauth.login()
            except gmail_oauth.GmailAuthError as exc:
                ui.print_error(str(exc))
                raise typer.Exit(1)
            ui.print_info("Signed in to Gmail.")
    elif provider == "outlook":
        with require_extra("assistant", "Outlook login"):
            from lydia.connectors.auth import outlook_oauth
            if not client_id:
                client_id = typer.prompt("Azure app Application (client) ID")
            try:
                outlook_oauth.login(client_id, on_code=lambda msg: ui.console.print(msg))
            except outlook_oauth.OutlookAuthError as exc:
                ui.print_error(str(exc))
                raise typer.Exit(1)
            ui.print_info("Signed in to Outlook.")
    elif provider == "canvas":
        from lydia.config import secrets
        if not base_url:
            base_url = typer.prompt("Canvas base URL (e.g. https://school.instructure.com)")
        if not token:
            token = typer.prompt("Canvas personal access token", hide_input=True)
        save_config_value("canvas_base_url", base_url, global_config_path())
        secrets.set_secret(secrets.CANVAS_TOKEN, token)
        ui.print_info(f"Canvas configured: {base_url}")
    elif provider == "ntfy":
        import secrets as pysecrets
        from lydia.config import secrets as lydia_secrets

        topic = f"lydia-{pysecrets.token_hex(6)}"
        lydia_secrets.set_secret(lydia_secrets.NTFY_TOPIC, topic)
        ui.print_info(
            f"Your private ntfy topic: {topic}\n"
            "1. Install the ntfy app (App Store / Play Store)\n"
            f"2. Subscribe to the topic '{topic}'\n"
            "3. Test it: lydia auth status ntfy — or just wait for an automation to fire.\n"
            "Treat the topic name like a password — anyone who knows it can read your alerts."
        )
        return
    else:
        ui.print_error(f"Unknown provider '{provider}'. Use one of: {', '.join(AUTH_PROVIDERS)}.")
        raise typer.Exit(1)


@auth_app.command("status")
def auth_status(provider: str | None = typer.Argument(None, help="Specific provider or None for all")) -> None:
    """Show which personal-assistant sources are connected."""
    from lydia.config import secrets

    config = load_config()
    # Unlike auth_login/auth_logout (which only ever touch one provider and
    # can afford to hard-fail if its extra is missing), status reports on
    # everything at once — canvas/ntfy need neither google-auth-oauthlib
    # nor msal, so someone without the 'assistant' extra installed should
    # still be able to check those rather than the whole command failing.
    try:
        from lydia.connectors.auth import gmail_oauth, outlook_oauth

        gmail_connected = gmail_oauth.is_logged_in()
        outlook_connected = outlook_oauth.is_logged_in()
    except ModuleNotFoundError:
        gmail_connected = outlook_connected = False
        # Rich markup would otherwise silently eat the literal "[assistant]"
        # below (see cli/optional_deps.py's require_extra for the same
        # bug) — escape the install command, keep [dim]...[/dim] as real
        # markup for styling.
        install_hint = escape('pip install "lydia-cli[assistant]"')
        ui.console.print(f"[dim]gmail/outlook status unavailable — install with: {install_hint}[/dim]")

    rows = [
        ("gmail", gmail_connected),
        ("outlook", outlook_connected),
        ("canvas", bool(config.canvas_base_url and secrets.get_secret(secrets.CANVAS_TOKEN))),
        ("ntfy", bool(secrets.get_secret(secrets.NTFY_TOPIC))),
    ]

    if provider:
        # Show topic for ntfy
        if provider == "ntfy":
            topic = secrets.get_secret(secrets.NTFY_TOPIC)
            if topic:
                ui.console.print(f"  {provider:<8} [green]connected[/green]")
                ui.console.print(f"  Topic: {topic}")
            else:
                ui.console.print(f"  {provider:<8} [dim]not connected[/dim]")
        else:
            # Show status for other providers
            status_map = {name: connected for name, connected in rows}
            if provider in status_map:
                connected = status_map[provider]
                marker = "[green]connected[/green]" if connected else "[dim]not connected[/dim]"
                ui.console.print(f"  {provider:<8} {marker}")
            else:
                ui.print_error(f"Unknown provider '{provider}'.")
                raise typer.Exit(1)
    else:
        # Show all
        for name, connected in rows:
            marker = "[green]connected[/green]" if connected else "[dim]not connected[/dim]"
            if name == "ntfy" and connected:
                topic = secrets.get_secret(secrets.NTFY_TOPIC)
                ui.console.print(f"  {name:<8} {marker}  [dim]{topic}[/dim]")
            else:
                ui.console.print(f"  {name:<8} {marker}")


@auth_app.command("logout")
def auth_logout(provider: str = typer.Argument(..., help="gmail | outlook | canvas | ntfy")) -> None:
    """Disconnect a personal-assistant data source."""
    if provider == "gmail":
        with require_extra("assistant", "Gmail logout"):
            from lydia.connectors.auth import gmail_oauth
            gmail_oauth.logout()
    elif provider == "outlook":
        with require_extra("assistant", "Outlook logout"):
            from lydia.connectors.auth import outlook_oauth
            outlook_oauth.logout()
    elif provider == "canvas":
        from lydia.config import secrets
        secrets.delete_secret(secrets.CANVAS_TOKEN)
    elif provider == "ntfy":
        from lydia.config import secrets
        secrets.delete_secret(secrets.NTFY_TOPIC)
    else:
        ui.print_error(f"Unknown provider '{provider}'. Use one of: {', '.join(AUTH_PROVIDERS)}.")
        raise typer.Exit(1)
    ui.print_info(f"Disconnected {provider}.")


@briefing_app.command("run")
def briefing_run(
    notify: bool = typer.Option(
        False, "--notify", help="Also push a macOS notification with a one-line summary."
    ),
) -> None:
    """Generate today's personal briefing (email, Canvas, stock market, AI news)."""
    from lydia.cli.briefing import run_briefing
    raise typer.Exit(run_briefing(load_config(), notify=notify))


@briefing_app.command("show")
def briefing_show() -> None:
    """Print the last generated briefing."""
    from lydia.cli.briefing import show_briefing
    raise typer.Exit(show_briefing())


@schedule_app.command("enable")
def schedule_enable(
    time: str | None = typer.Option(
        None, "--time", help="24-hour HH:MM, e.g. 08:00. Defaults to the last-used (or 08:00) time."
    ),
) -> None:
    """Schedule `lydia briefing run --notify` to fire automatically every day."""
    from lydia.cli import scheduler

    config = load_config()
    chosen_time = time or config.briefing_schedule_time
    try:
        plist_path = scheduler.enable(chosen_time)
    except scheduler.ScheduleError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    save_config_value("briefing_schedule_enabled", True, global_config_path())
    save_config_value("briefing_schedule_time", chosen_time, global_config_path())
    ui.print_info(f"Scheduled daily briefing at {chosen_time} ({plist_path}).")


@schedule_app.command("disable")
def schedule_disable() -> None:
    """Stop the scheduled daily briefing."""
    from lydia.cli import scheduler

    scheduler.disable()
    save_config_value("briefing_schedule_enabled", False, global_config_path())
    ui.print_info("Scheduled briefing disabled.")


def _client_and_model(config: LydiaConfig):
    """Connect, or exit(1) with a printed error. Caller must close the client."""
    from lydia.cli.chat import resolve_model

    client = build_client(config)
    if not client.is_alive():
        ui.print_error(f"Cannot reach {config.server_url or config.ollama_host}.")
        raise typer.Exit(1)
    try:
        model = resolve_model(client, config)
    except OllamaError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    return client, model


@app.command()
def automate(request: str = typer.Argument(..., help="What to automate, in plain English")) -> None:
    """Create an automation from a plain-English description."""
    from lydia.cli.automate_flow import create_from_english

    config = load_config()
    client, model = _client_and_model(config)
    with client:
        ok = create_from_english(request, client, model, config)
    raise typer.Exit(0 if ok else 1)


@automations_app.command("list")
def automations_list() -> None:
    from lydia.automations import store
    autos = store.list_automations()
    if not autos:
        ui.print_info("No automations yet. Create one with: lydia automate \"...\"")
        return
    from lydia.automations.model import describe
    state = store.load_state()
    for auto in autos:
        flag = "" if auto.enabled else " (disabled)"
        last = state.get(auto.name, {}).get("last_run", "never")
        from rich.markup import escape
        ui.console.print(f"{escape(describe(auto) + flag)}  [dim]last run: {last}[/dim]")


@automations_app.command("show")
def automations_show(name: str) -> None:
    import json as _json
    from lydia.automations import store
    from lydia.automations.model import AutomationError, describe
    try:
        auto = store.load_automation(name)
    except AutomationError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    from rich.markup import escape
    ui.console.print(escape(describe(auto)))
    ui.console.print(_json.dumps(auto.to_dict(), indent=2))


@automations_app.command("run")
def automations_run(name: str) -> None:
    """Execute one automation immediately (ignores its trigger) — for testing."""
    from datetime import datetime
    from lydia.automations import runner as auto_runner, store
    from lydia.automations.model import AutomationError
    try:
        auto = store.load_automation(name)
    except AutomationError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    config = load_config()
    client, model = _client_and_model(config)
    with client:
        state = store.load_state()
        sections = None
        if auto.trigger.type == "event":
            items = auto_runner.poll_new_items(auto.trigger, config)
            sections = [("current items", "\n".join(t for _i, t in items))]
        record = auto_runner.run_one(auto, config, client, model,
                                     datetime.now(), state, extra_sections=sections)
        store.save_state(state)
        store.append_run(record)
    ui.console.print(record["result_snippet"] or "(no output)")
    ui.print_info(f"ok={record['ok']} notified={record['notified']}")


@automations_app.command("enable")
def automations_enable(name: str) -> None:
    _set_enabled(name, True)


@automations_app.command("disable")
def automations_disable(name: str) -> None:
    _set_enabled(name, False)


def _set_enabled(name: str, value: bool) -> None:
    from lydia.automations import store
    from lydia.automations.model import AutomationError
    try:
        auto = store.load_automation(name)
    except AutomationError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    auto.enabled = value
    store.save_automation(auto)
    ui.print_info(f"'{name}' {'enabled' if value else 'disabled'}.")


@automations_app.command("remove")
def automations_remove(name: str) -> None:
    from lydia.automations import store
    if store.delete_automation(name):
        ui.print_info(f"Removed '{name}'.")
    else:
        ui.print_error(f"No automation named '{name}'.")
        raise typer.Exit(1)


@automations_app.command("tick")
def automations_tick() -> None:
    """One heartbeat pass — normally invoked by launchd, not by hand."""
    from lydia.automations import runner as auto_runner
    config = load_config()
    client, model = _client_and_model(config)
    with client:
        results = auto_runner.tick(config, client, model)
    for record in results:
        status = "ok" if record["ok"] else f"FAILED: {record['error']}"
        ui.console.print(f"{record['name']}: {status}")
    if not results:
        ui.print_info("Nothing due.")


@automations_schedule_app.command("enable")
def automations_schedule_enable(
    interval: int = typer.Option(300, "--interval", help="Seconds between ticks (60-3600)"),
) -> None:
    from lydia.cli import scheduler
    try:
        path = scheduler.enable_automations(interval_seconds=interval)
    except scheduler.ScheduleError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    ui.print_info(f"Heartbeat enabled every {interval}s ({path}).")


@automations_schedule_app.command("disable")
def automations_schedule_disable() -> None:
    from lydia.cli import scheduler
    scheduler.disable_automations()
    ui.print_info("Heartbeat disabled.")


@listen_app.callback()
def listen_run(ctx: typer.Context) -> None:
    """With no subcommand: run the voice loop in the foreground (Ctrl-C stops)."""
    if ctx.invoked_subcommand is not None:
        return
    import logging
    from pathlib import Path

    from lydia.cli.chat import resolve_model
    from lydia.voice import assistant, audio, tts
    from lydia.voice.stt import Transcriber
    from lydia.voice.wake import WakeDetector, wake_label

    log_path = Path.home() / ".lydia" / "listen.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    voice_logger = logging.getLogger("lydia.voice")
    voice_logger.setLevel(logging.INFO)
    voice_logger.addHandler(handler)

    config = load_config()
    with build_client(config) as client:
        if not client.is_alive():
            ui.print_error(f"Cannot reach {config.server_url or config.ollama_host}.")
            raise typer.Exit(1)
        model = config.voice_model or resolve_model(client, config)
        with require_extra("voice", "Voice mode"):
            mic = audio.Microphone()
            ui.print_info(f'Listening for "{wake_label(config.voice_wake_word)}" — Ctrl-C to stop.')
            try:
                assistant.run_loop(
                    config, client, model,
                    frames=mic.frames(),
                    wake=WakeDetector(config.voice_wake_word),
                    transcriber=Transcriber(config.voice_stt_model),
                    speak_fn=lambda text: tts.speak(text, voice=config.voice_tts_voice),
                    chime_fn=assistant.play_chime,
                    flush_fn=mic.flush,
                )
            except KeyboardInterrupt:
                ui.print_info("Stopped listening.")


@listen_app.command("enable")
def listen_enable() -> None:
    """Start at login and keep running (launchd)."""
    from lydia.cli import scheduler

    try:
        path = scheduler.enable_listen()
    except scheduler.ScheduleError as exc:
        ui.print_error(str(exc))
        raise typer.Exit(1)
    ui.print_info(f"Voice assistant enabled at login ({path}).")


@listen_app.command("disable")
def listen_disable() -> None:
    """Stop the always-on voice assistant."""
    from lydia.cli import scheduler

    scheduler.disable_listen()
    ui.print_info("Voice assistant disabled.")


@listen_app.command("status")
def listen_status() -> None:
    from lydia.cli import scheduler

    state = "enabled at login" if scheduler.listen_enabled() else "not enabled"
    ui.print_info(f"Voice assistant: {state}.")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
