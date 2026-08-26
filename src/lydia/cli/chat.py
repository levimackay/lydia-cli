"""The interactive Lydia chat REPL."""

from __future__ import annotations

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from lydia.agent import facts
from lydia.agent.loop import run_agent_turn
from lydia.agent.memory import SessionHistory
from lydia.agent.prompts import build_system_prompt
from lydia.agent.tools import ToolContext, Todo, build_registry, filter_for_mode
from lydia.cli import ui
from lydia.config.settings import GLOBAL_DIR, LydiaConfig, find_project_root
from lydia.context.scanner import ProjectSummary, scan_project
from lydia.llm.client import OllamaError
from lydia.llm.factory import build_client
from lydia.llm.models import pick_default_model, supports_tool_calling
from lydia.llm.protocol import ModelClient
from lydia.llm.types import Message

HELP_TEXT = """\
| Command | Effect |
|---|---|
| `/help` | Show this help |
| `/mode [plan\\|ask\\|auto]` | Show or change the session mode (Shift-Tab also cycles it) |
| `/model <name>` | Switch model for this session |
| `/models` | List installed Ollama models |
| `/new` | Start a fresh conversation |
| `/remember <fact>` | Save a fact that persists across sessions |
| `/memory` | List remembered facts |
| `/forget <n>` | Remove remembered fact #n |
| `/automate <text>` | Create an automation in plain English |
| `/exit` | Quit (also Ctrl-D) |
"""

VALID_MODES = ("plan", "ask", "auto")
MODE_CYCLE = {"plan": "ask", "ask": "auto", "auto": "plan"}

PROMPT_STYLE = Style.from_dict({
    "prompt.plan": "ansicyan bold",
    "prompt.ask": "ansimagenta bold",
    "prompt.auto": "ansigreen bold",
})


def _apply_mode(session: "ChatSession", new_mode: str) -> bool:
    """Validate and apply a mode change. Returns whether it was applied."""
    if new_mode not in VALID_MODES:
        return False
    session.config.mode = new_mode
    return True


class ChatSession:
    """Holds the state of one interactive session."""

    def __init__(self, config: LydiaConfig, client: ModelClient, model: str,
                 summary: ProjectSummary | None, project_root: Path | None) -> None:
        self.config = config
        self.client = client
        self.model = model
        self.root = project_root or Path.cwd()
        self.summary = summary
        self.facts = facts.load_facts(self.root)
        self.messages: list[Message] = []
        self.history = SessionHistory(project_root)
        self.registry = build_registry()
        self.todos: list[Todo] = []
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        return build_system_prompt(self.summary, self.facts, self.config.mode, self.config.verify_command)

    def reset(self) -> None:
        self.messages.clear()

    def refresh_facts(self) -> None:
        """Reload remembered facts from disk and fold them back into the prompt."""
        self.facts = facts.load_facts(self.root)
        self.system_prompt = self._build_system_prompt()

    def send(self, user_text: str) -> None:
        # Rebuilt every turn (not just on fact changes) so a /mode switch or
        # Shift-Tab cycle since the last message is reflected immediately.
        self.system_prompt = self._build_system_prompt()
        tools = filter_for_mode(self.registry, self.config.mode)

        user_message = Message(role="user", content=user_text)
        self.messages.append(user_message)
        self.history.append(user_message)
        ui.console.print()  # blank line between the user's input and Lydia's response
        ctx = ToolContext(
            root=self.root, config=self.config, confirm=ui.confirm, client=self.client, todos=self.todos,
        )
        try:
            reply, stats = run_agent_turn(
                client=self.client,
                model=self.model,
                temperature=self.config.temperature,
                num_ctx=self.config.num_ctx,
                think=self.config.think_flag,
                keep_alive=self.config.keep_alive,
                system_prompt=self.system_prompt,
                messages=self.messages,
                registry=tools,
                ctx=ctx,
                stream_fn=ui.stream_agent_response,
                on_tool_call=ui.print_tool_call,
                on_tool_result=ui.print_tool_result,
            )
        except KeyboardInterrupt:
            self.messages.pop()  # drop the unanswered user turn; pairs with the loop's own rollback
            ui.console.print("\n[dim]interrupted[/dim]")
            return
        except OllamaError as exc:
            self.messages.pop()  # same: no reply means no half-open turn left dangling
            ui.print_error(str(exc))
            return
        self.history.append(Message(role="assistant", content=reply))
        self.refresh_facts()  # pick up anything remembered via the tool this turn
        line = ui.format_stats(stats)
        if line:
            ui.console.print(f"[dim]{line}[/dim]")
        ui.console.print()


def resolve_model(client: ModelClient, config: LydiaConfig) -> str:
    """Use the configured model if installed, otherwise auto-pick."""
    models = client.list_models()
    if not models:
        raise OllamaError("No models installed. Pull one first, e.g. `ollama pull qwen3.5`.")

    chosen: str | None = None
    if config.model:
        if any(m.name == config.model or m.name.split(":")[0] == config.model for m in models):
            chosen = config.model
        else:
            ui.print_error(f"Configured model '{config.model}' is not installed; auto-selecting.")
    if chosen is None:
        chosen = pick_default_model(models) or models[0].name

    # Covers both cases: an explicitly-configured bad model, and auto-select
    # being forced to fall back to one because nothing else qualifies (e.g.
    # a remote backend whose only installed models all lack tool support).
    if not supports_tool_calling(chosen):
        ui.print_warning(
            f"'{chosen}' is known not to support structured tool calling — "
            "file/git/assistant tools will silently do nothing or error. Install a "
            "tool-capable model (e.g. `ollama pull qwen3.5` or a llama3.1+ model) on "
            "whichever Ollama instance is actually handling requests, or use `lydia "
            "ask` without --yes for plain Q&A."
        )
    return chosen


def run_chat(config: LydiaConfig) -> int:
    client = build_client(config)
    if not client.is_alive():
        if config.server_url:
            ui.print_error(f"Cannot reach the Lydia Server at {config.server_url}.")
        else:
            ui.print_error(
                f"Cannot reach Ollama at {config.ollama_host}.\n"
                "  Start it with `ollama serve` or by opening the Ollama app."
            )
        return 1

    try:
        model = resolve_model(client, config)
    except OllamaError as exc:
        ui.print_error(str(exc))
        return 1

    project_root = find_project_root()
    summary = scan_project(project_root) if project_root else None
    session = ChatSession(config, client, model, summary, project_root)

    ui.print_banner(model, summary.project_kind if summary else None)

    GLOBAL_DIR.mkdir(parents=True, exist_ok=True)

    def _prompt_message() -> list[tuple[str, str]]:
        return [(f"class:prompt.{session.config.mode}", f"Lydia ({session.config.mode}) > ")]

    key_bindings = KeyBindings()

    @key_bindings.add("s-tab")
    def _cycle_mode(event) -> None:  # noqa: ANN001 - prompt_toolkit's KeyPressEvent
        # No console.print here: this fires mid-render inside an active
        # prompt_session.prompt() call, and Rich's console writes aren't
        # coordinated with prompt_toolkit's own screen buffer — the updated
        # mode name in the prompt text itself is the feedback.
        _apply_mode(session, MODE_CYCLE[session.config.mode])
        event.app.invalidate()

    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(str(GLOBAL_DIR / "prompt_history")),
        style=PROMPT_STYLE,
        key_bindings=key_bindings,
    )

    while True:
        try:
            text = prompt_session.prompt(_prompt_message).strip()
        except KeyboardInterrupt:
            continue  # clear the current line, like a shell
        except EOFError:
            break
        if not text:
            continue
        if text.startswith("/"):
            if _handle_slash(text, session):
                break
            continue
        session.send(text)

    ui.console.print("[dim]bye[/dim]")
    client.close()
    return 0


def _handle_slash(text: str, session: ChatSession) -> bool:
    """Handle a /command. Returns True if the REPL should exit."""
    command, _, argument = text.partition(" ")
    command = command.lower()
    argument = argument.strip()

    if command in ("/exit", "/quit", "/q"):
        return True
    if command == "/help":
        from rich.markdown import Markdown
        ui.console.print(Markdown(HELP_TEXT))
    elif command == "/new":
        session.reset()
        ui.print_info("Started a fresh conversation.")
    elif command == "/mode":
        if not argument:
            ui.print_info(f"Current mode: {session.config.mode}")
        elif _apply_mode(session, argument.lower()):
            ui.print_info(f"Mode: {session.config.mode}")
        else:
            ui.print_error(f"Unknown mode '{argument}'. Use one of: {', '.join(VALID_MODES)}.")
    elif command == "/models":
        try:
            for m in session.client.list_models():
                marker = "→" if m.name == session.model else " "
                ui.console.print(f" {marker} {m.name}  [dim]{m.size_human}[/dim]")
        except OllamaError as exc:
            ui.print_error(str(exc))
    elif command == "/model":
        if not argument:
            ui.print_info(f"Current model: {session.model}")
        elif session.client.has_model(argument):
            session.model = argument
            ui.print_info(f"Switched to {argument} for this session.")
        else:
            ui.print_error(f"Model '{argument}' is not installed. Try `ollama pull {argument}`.")
    elif command == "/remember":
        if not argument:
            ui.print_error("Usage: /remember <fact>")
        else:
            fact = facts.remember(session.root, argument)
            session.refresh_facts()
            ui.print_info(f"Remembered: {fact.text}")
    elif command == "/memory":
        if not session.facts:
            ui.print_info("No facts remembered yet. Use /remember <fact> to add one.")
        else:
            for i, fact in enumerate(session.facts, start=1):
                ui.console.print(f"  {i}. {fact.text}  [dim]{fact.created_at}[/dim]")
    elif command == "/forget":
        try:
            index = int(argument)
        except ValueError:
            ui.print_error("Usage: /forget <n> — see /memory for fact numbers.")
        else:
            try:
                removed = facts.forget(session.root, index)
            except ValueError as exc:
                ui.print_error(str(exc))
            else:
                session.refresh_facts()
                ui.print_info(f"Forgot: {removed.text}")
    elif command == "/automate":
        if not argument:
            ui.print_error("Usage: /automate <what to automate, in plain English>")
        else:
            from lydia.cli.automate_flow import create_from_english
            create_from_english(argument, session.client, session.model, session.config)
    else:
        ui.print_error(f"Unknown command {command}. Try /help.")
    return False
