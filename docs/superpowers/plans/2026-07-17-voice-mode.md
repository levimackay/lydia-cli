# Voice Mode ("Hey Jarvis") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Always-listening local voice assistant: "Hey Jarvis" wake word → record → faster-whisper transcription → one safe-tools agent turn → spoken reply via macOS `say`.

**Architecture:** New `src/lydia/voice/` package (tts, audio, wake, stt, assistant), same layering rule as `automations/`: may import `agent/`, `llm/`, `config/`, NEVER `cli/`. CLI adds `lydia listen` (+ launchd agent `com.lydia.listen`) in `cli/main.py`/`cli/scheduler.py`. Every hardware/model dependency is behind an injectable seam so unit tests never touch the mic, audio devices, model downloads, or Ollama.

**Tech Stack:** Python, sounddevice (mic), openwakeword (wake), faster-whisper (STT), macOS `say` (TTS), launchd. Spec: `docs/superpowers/specs/2026-07-17-voice-mode-design.md`. Read it first.

## Global Constraints

- Layering: `voice/` must never import `lydia.cli`. Real-device glue (mic stream) lives in `voice/audio.py` but is constructor-injected everywhere it's used.
- Unit tests NEVER touch microphone/audio devices, download models, or hit Ollama; fakes only. `.venv/bin/pytest` from repo root; all 320 existing tests stay green.
- Voice agent turns offer ONLY `VOICE_TOOLS = {"check_email", "check_canvas", "check_stocks", "check_news", "notify"}`, never file/shell tools. `confirm=lambda _r: False`.
- Pass `keep_alive=config.keep_alive` on every model call (via `run_agent_turn`).
- New deps go in `pyproject.toml` `dependencies`: `sounddevice>=0.4`, `openwakeword>=0.6`, `faster-whisper>=1.0`.
- **Never add a `Co-Authored-By: Claude` (or any Claude/Anthropic) trailer to commits.** Plain imperative commit subjects.
- New config keys (Task 2): `voice_wake_word: str = "hey_jarvis"`, `voice_stt_model: str = "base.en"`, `voice_tts_voice: str | None = None`.

---

### Task 1: TTS in `voice/tts.py`

**Files:**
- Create: `src/lydia/voice/__init__.py` (docstring only: `"""Voice assistant: wake word, speech-to-text, spoken replies."""`)
- Create: `src/lydia/voice/tts.py`
- Test: `tests/test_voice_tts.py`

**Interfaces:**
- Produces: `strip_for_speech(text: str) -> str`; `speak(text: str, voice: str | None = None, runner=subprocess.run) -> None`

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_voice_tts.py"""
from lydia.voice import tts


def test_strip_removes_markdown():
    text = "**Bold** and `code` and [a link](http://x.com) and # Heading\n- item one"
    out = tts.strip_for_speech(text)
    for bad in ("**", "`", "](", "#", "- "):
        assert bad not in out
    assert "Bold" in out and "code" in out and "a link" in out and "item one" in out


def test_strip_drops_code_blocks_and_emoji():
    text = "Before\n```python\nx = 1\n```\nAfter 🎉"
    out = tts.strip_for_speech(text)
    assert "x = 1" not in out and "🎉" not in out
    assert "Before" in out and "After" in out


def test_speak_invokes_say_with_voice():
    calls = []
    tts.speak("hello there", voice="Samantha", runner=lambda argv, **kw: calls.append(argv))
    assert calls == [["say", "-v", "Samantha", "hello there"]]


def test_speak_without_voice_and_empty_text():
    calls = []
    tts.speak("hi", runner=lambda argv, **kw: calls.append(argv))
    assert calls == [["say", "hi"]]
    tts.speak("   ", runner=lambda argv, **kw: calls.append(argv))
    assert len(calls) == 1  # empty after strip: no call
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_voice_tts.py -q`. Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
"""src/lydia/voice/tts.py — speak text aloud via macOS `say`.

Markdown/emoji are stripped first: the model sometimes formats despite the
voice prompt, and `say` reads asterisks aloud ("asterisk asterisk bold").
"""

from __future__ import annotations

import re
import subprocess

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"[*_`#]+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002190-\U000021FF️]"
)


def strip_for_speech(text: str) -> str:
    text = _CODE_BLOCK.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _BULLET.sub("", text)
    text = _INLINE.sub("", text)
    text = _EMOJI.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def speak(text: str, voice: str | None = None, runner=subprocess.run) -> None:
    clean = strip_for_speech(text)
    if not clean:
        return
    argv = ["say"] + (["-v", voice] if voice else []) + [clean]
    runner(argv, check=False)
```

- [ ] **Step 4: Run to green**: `.venv/bin/pytest tests/test_voice_tts.py -q`, then the full suite once.
- [ ] **Step 5: Commit**: `Add voice TTS module wrapping macOS say`

---

### Task 2: Audio capture + config keys in `voice/audio.py`

**Files:**
- Create: `src/lydia/voice/audio.py`
- Modify: `src/lydia/config/settings.py` (add three fields after `briefing_schedule_time`)
- Modify: `pyproject.toml` (add `"sounddevice>=0.4",` to `dependencies`)
- Test: `tests/test_voice_audio.py`

**Interfaces:**
- Produces: `record_until_silence(read_frame: Callable[[], np.ndarray], *, silence_after: float = 1.2, max_seconds: float = 15.0, frame_seconds: float = 0.08, threshold: float = 500.0) -> np.ndarray` (int16 mono 16 kHz); `SAMPLE_RATE = 16000`; `FRAME_SAMPLES = 1280`; `mic_frames()` (real-device generator, NOT unit tested); config fields `voice_wake_word`, `voice_stt_model`, `voice_tts_voice`.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_voice_audio.py"""
import numpy as np

from lydia.config.settings import LydiaConfig
from lydia.voice import audio


def _frames(seq):
    """read_frame() stub yielding int16 frames of given absolute amplitude."""
    frames = [np.full(audio.FRAME_SAMPLES, amp, dtype=np.int16) for amp in seq]
    it = iter(frames)
    return lambda: next(it)


def test_records_speech_then_stops_on_silence():
    # 3 loud frames, then plenty of silence: stops after `silence_after` quiet time
    read = _frames([3000, 3000, 3000] + [0] * 100)
    out = audio.record_until_silence(read, silence_after=0.16, frame_seconds=0.08)
    assert out.dtype == np.int16
    # 3 loud + 2 silent frames (0.16s / 0.08s) = 5 frames
    assert len(out) == 5 * audio.FRAME_SAMPLES


def test_hard_cap_max_seconds():
    read = _frames([3000] * 1000)  # never goes silent
    out = audio.record_until_silence(read, max_seconds=0.4, frame_seconds=0.08)
    assert len(out) == 5 * audio.FRAME_SAMPLES  # 0.4 / 0.08


def test_config_voice_defaults():
    cfg = LydiaConfig()
    assert cfg.voice_wake_word == "hey_jarvis"
    assert cfg.voice_stt_model == "base.en"
    assert cfg.voice_tts_voice is None
```

- [ ] **Step 2: Run to verify failure.** `.venv/bin/pytest tests/test_voice_audio.py -q` fails.

- [ ] **Step 3: Implement**

In `settings.py`, append after `briefing_schedule_time`:

```python
    # Voice mode (see voice/). Wake word is an openWakeWord model name.
    voice_wake_word: str = "hey_jarvis"
    voice_stt_model: str = "base.en"
    voice_tts_voice: str | None = None  # None = system default `say` voice
```

```python
"""src/lydia/voice/audio.py — microphone frames and silence-bounded recording.

`record_until_silence` is pure logic over an injected `read_frame` callable so
tests never open a device; `mic_frames` is the one real-hardware function and
is exercised only by the manual checklist.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # 80 ms — the frame size openWakeWord expects


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


def record_until_silence(
    read_frame: Callable[[], np.ndarray],
    *,
    silence_after: float = 1.2,
    max_seconds: float = 15.0,
    frame_seconds: float = 0.08,
    threshold: float = 500.0,
) -> np.ndarray:
    """Collect frames until `silence_after` seconds of quiet (or the hard cap)."""
    frames: list[np.ndarray] = []
    quiet = 0.0
    while len(frames) * frame_seconds < max_seconds:
        frame = read_frame()
        frames.append(frame)
        quiet = 0.0 if _rms(frame) >= threshold else quiet + frame_seconds
        if quiet >= silence_after:
            break
    return np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)


def mic_frames():
    """Yield int16 mono FRAME_SAMPLES frames from the default mic. Real hardware."""
    import sounddevice as sd

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=FRAME_SAMPLES) as stream:
        while True:
            data, _overflowed = stream.read(FRAME_SAMPLES)
            yield data[:, 0].copy()
```

Add `"sounddevice>=0.4",` to `pyproject.toml` dependencies, then `.venv/bin/pip install -e ".[dev]"`.

- [ ] **Step 4: Run to green** on the focused file, then the full suite.
- [ ] **Step 5: Commit**: `Add voice audio capture and voice config keys`

---

### Task 3: Wake word in `voice/wake.py`

**Files:**
- Create: `src/lydia/voice/wake.py`
- Modify: `pyproject.toml` (add `"openwakeword>=0.6",`)
- Test: `tests/test_voice_wake.py`

**Interfaces:**
- Consumes: frames shaped like `audio.FRAME_SAMPLES` int16.
- Produces: `WakeDetector(model_name: str, model=None, threshold: float = 0.5)` with `.process(frame: np.ndarray) -> bool` (True exactly once per activation; internal reset avoids repeat-firing while the score stays high).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_voice_wake.py"""
import numpy as np

from lydia.voice.wake import WakeDetector


class FakeOww:
    def __init__(self, scores):
        self.scores = list(scores)

    def predict(self, frame):
        return {"hey_jarvis": self.scores.pop(0)}

    def reset(self):
        pass


FRAME = np.zeros(1280, dtype=np.int16)


def test_fires_once_when_score_crosses_threshold():
    det = WakeDetector("hey_jarvis", model=FakeOww([0.1, 0.7, 0.8, 0.2, 0.9]))
    fired = [det.process(FRAME) for _ in range(5)]
    # fires on first crossing, NOT on the still-high next frame, refires after dropping
    assert fired == [False, True, False, False, True]


def test_ignores_other_models_scores():
    class Multi(FakeOww):
        def predict(self, frame):
            return {"alexa": 0.99, "hey_jarvis": self.scores.pop(0)}

    det = WakeDetector("hey_jarvis", model=Multi([0.1, 0.1]))
    assert det.process(FRAME) is False and det.process(FRAME) is False
```

- [ ] **Step 2: Run to verify failure.** FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""src/lydia/voice/wake.py — openWakeWord wrapper with one-shot activation."""

from __future__ import annotations

import numpy as np


class WakeDetector:
    """True exactly once per wake-word activation.

    `model` is injectable for tests; the real openWakeWord model is built
    lazily so importing this module never downloads anything.
    """

    def __init__(self, model_name: str, model=None, threshold: float = 0.5):
        self.model_name = model_name
        self.threshold = threshold
        self._model = model
        self._armed = True

    def _ensure_model(self):
        if self._model is None:
            from openwakeword.model import Model

            self._model = Model(wakeword_models=[self.model_name])
        return self._model

    def process(self, frame: np.ndarray) -> bool:
        score = self._ensure_model().predict(frame).get(self.model_name, 0.0)
        if score >= self.threshold:
            if self._armed:
                self._armed = False
                return True
            return False
        self._armed = True
        return False
```

Add `"openwakeword>=0.6",` to pyproject, `pip install -e ".[dev]"`.

- [ ] **Step 4: Run to green** on the focused tests, then the full suite.
- [ ] **Step 5: Commit**: `Add wake word detector wrapping openWakeWord`

---

### Task 4: STT in `voice/stt.py`

**Files:**
- Create: `src/lydia/voice/stt.py`
- Modify: `pyproject.toml` (add `"faster-whisper>=1.0",`)
- Test: `tests/test_voice_stt.py`

**Interfaces:**
- Consumes: int16 numpy audio from `audio.record_until_silence`.
- Produces: `Transcriber(model_name: str, model=None)` with `.transcribe(pcm: np.ndarray) -> str` (stripped text, `""` for silence/empty).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_voice_stt.py"""
import numpy as np

from lydia.voice.stt import Transcriber


class Seg:
    def __init__(self, text):
        self.text = text


class FakeWhisper:
    def __init__(self, segments):
        self.segments = segments
        self.seen = None

    def transcribe(self, pcm, **kwargs):
        self.seen = pcm
        return iter(self.segments), None


def test_joins_segments_and_normalizes_audio():
    fake = FakeWhisper([Seg(" Hello"), Seg(" world. ")])
    t = Transcriber("base.en", model=fake)
    out = t.transcribe(np.array([0, 16384, -16384], dtype=np.int16))
    assert out == "Hello world."
    assert fake.seen.dtype == np.float32 and abs(float(fake.seen[1]) - 0.5) < 0.01


def test_empty_audio_returns_empty_string():
    t = Transcriber("base.en", model=FakeWhisper([]))
    assert t.transcribe(np.zeros(0, dtype=np.int16)) == ""
```

- [ ] **Step 2: Run to verify failure.** FAIL.

- [ ] **Step 3: Implement**

```python
"""src/lydia/voice/stt.py — faster-whisper transcription behind one seam."""

from __future__ import annotations

import numpy as np


class Transcriber:
    def __init__(self, model_name: str, model=None):
        self.model_name = model_name
        self._model = model

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            # int8 keeps memory/CPU sane on the Air; downloads once to ~/.cache.
            self._model = WhisperModel(self.model_name, compute_type="int8")
        return self._model

    def transcribe(self, pcm: np.ndarray) -> str:
        if pcm.size == 0:
            return ""
        audio = (pcm.astype(np.float32) / 32768.0)
        segments, _info = self._ensure_model().transcribe(audio, language="en")
        return " ".join(seg.text.strip() for seg in segments).strip()
```

Add `"faster-whisper>=1.0",` to pyproject, `pip install -e ".[dev]"`.

- [ ] **Step 4: Run to green** on the focused tests, then the full suite.
- [ ] **Step 5: Commit**: `Add speech-to-text transcriber wrapping faster-whisper`

---

### Task 5: Assistant loop in `voice/assistant.py`

**Files:**
- Create: `src/lydia/voice/assistant.py`
- Test: `tests/test_voice_assistant.py`

**Interfaces:**
- Consumes: `WakeDetector.process(frame) -> bool`; `Transcriber.transcribe(pcm) -> str`; `audio.record_until_silence(read_frame)`; `tts.speak`; `agent.loop.run_agent_turn` (kwargs-only, returns `(text, stats)`); `agent.tools.build_registry`, `ToolContext`; `llm.client.OllamaError`.
- Produces: `VOICE_TOOLS` (constant, see Global Constraints), `voice_registry() -> list[ToolSpec]`, `run_loop(config, client, model, *, frames, wake, transcriber, speak_fn, chime_fn, max_turns=None) -> None`. `frames` is an iterator of int16 frames; `max_turns` stops the loop after N wake-activations (tests); production passes None.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_voice_assistant.py"""
import itertools

import numpy as np
import pytest

from lydia.config.settings import LydiaConfig
from lydia.llm.client import OllamaError
from lydia.llm.types import ChatChunk
from lydia.voice import assistant

FRAME = np.full(1280, 3000, dtype=np.int16)
SILENT = np.zeros(1280, dtype=np.int16)


class OneShotWake:
    """Fires on the first frame only."""

    def __init__(self):
        self.fired = False

    def process(self, frame):
        if not self.fired:
            self.fired = True
            return True
        return False


class FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, pcm):
        return self.text


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def chat_stream(self, **kwargs):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield ChatChunk(content=reply, done=True)


def _run(client, transcriber_text, spoken, chimes):
    frames = itertools.chain([FRAME], itertools.repeat(SILENT))
    assistant.run_loop(
        LydiaConfig(), client, "m",
        frames=frames, wake=OneShotWake(),
        transcriber=FakeTranscriber(transcriber_text),
        speak_fn=lambda text: spoken.append(text),
        chime_fn=lambda kind: chimes.append(kind),
        max_turns=1,
    )


def test_wake_transcribe_answer_speak():
    spoken, chimes = [], []
    _run(FakeClient(["It is sunny."]), "what's the weather", spoken, chimes)
    assert chimes == ["wake"]
    assert spoken == ["It is sunny."]


def test_empty_transcription_chimes_miss_and_skips_model():
    spoken, chimes = [], []
    _run(FakeClient([]), "   ", spoken, chimes)  # client never called: no replies needed
    assert chimes == ["wake", "miss"]
    assert spoken == []


def test_ollama_down_speaks_apology_and_survives():
    spoken, chimes = [], []
    _run(FakeClient([OllamaError("connection refused")]), "hello", spoken, chimes)
    assert any("reach my brain" in s for s in spoken)


def test_voice_registry_is_safe_tools_only():
    names = {spec.name for spec in assistant.voice_registry()}
    assert names == assistant.VOICE_TOOLS
    assert "write_file" not in names and "run_command" not in names
```

- [ ] **Step 2: Run to verify failure.** FAIL.

- [ ] **Step 3: Implement**

```python
"""src/lydia/voice/assistant.py — the wake → listen → think → speak loop.

Every stage is injected: `frames` (mic), `wake`, `transcriber`, `speak_fn`,
`chime_fn`. cli/main.py wires the real ones; tests wire fakes. Never
imports cli/ (same layering rule as automations/).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from lydia.agent.loop import run_agent_turn
from lydia.agent.tools import ToolContext, ToolSpec, build_registry
from lydia.config.settings import LydiaConfig
from lydia.llm.client import OllamaError
from lydia.llm.protocol import ModelClient
from lydia.llm.types import Message
from lydia.voice import audio

logger = logging.getLogger(__name__)

VOICE_TOOLS = {"check_email", "check_canvas", "check_stocks", "check_news", "notify"}

VOICE_SYSTEM_PROMPT = (
    "You are Lydia, a spoken voice assistant. The user talked to you out loud "
    "and your reply will be read aloud by text-to-speech. Answer in one to "
    "three short sentences of plain conversational prose — no markdown, no "
    "lists, no code, no emoji. Use your tools when the question needs live "
    "data (email, Canvas, stocks, news); otherwise just answer."
)

_CHIMES = {"wake": "/System/Library/Sounds/Glass.aiff",
           "miss": "/System/Library/Sounds/Basso.aiff"}


def voice_registry() -> list[ToolSpec]:
    return [spec for spec in build_registry() if spec.name in VOICE_TOOLS]


def play_chime(kind: str) -> None:
    subprocess.run(["afplay", _CHIMES[kind]], check=False)


def run_loop(config: LydiaConfig, client: ModelClient, model: str, *,
             frames, wake, transcriber, speak_fn, chime_fn,
             max_turns: int | None = None) -> None:
    registry = voice_registry()
    ctx = ToolContext(root=Path.home(), config=config, confirm=lambda _r: False,
                      client=client)
    turns = 0
    for frame in frames:
        if max_turns is not None and turns >= max_turns:
            return
        if not wake.process(frame):
            continue
        turns += 1
        chime_fn("wake")
        pcm = audio.record_until_silence(lambda: next(frames))
        text = transcriber.transcribe(pcm).strip()
        if not text:
            chime_fn("miss")
            continue
        logger.info("Heard: %s", text)
        try:
            reply, _stats = run_agent_turn(
                client=client, model=model,
                temperature=config.temperature, num_ctx=config.num_ctx,
                think=config.think_flag, keep_alive=config.keep_alive,
                system_prompt=VOICE_SYSTEM_PROMPT,
                messages=[Message(role="user", content=text)],
                registry=registry, ctx=ctx,
            )
        except OllamaError:
            speak_fn("I can't reach my brain right now.")
            continue
        except Exception:  # noqa: BLE001 - the loop must survive anything
            logger.exception("Voice turn failed")
            speak_fn("Something went wrong with that one.")
            continue
        if reply.strip():
            speak_fn(reply)
```

Note for the implementer: `run_agent_turn` uses the default silent `stream_fn`, and `ToolContext` lives in `agent/tools.py`. Check its actual constructor fields (`root`, `config`, `confirm`, `client`) before writing, and match them exactly.

- [ ] **Step 4: Run to green** on the focused tests, then the full suite.
- [ ] **Step 5: Commit**: `Add voice assistant wake-listen-think-speak loop`

---

### Task 6: CLI + launchd + docs

**Files:**
- Modify: `src/lydia/cli/scheduler.py` (listen launchd trio, mirroring `enable_automations`)
- Modify: `src/lydia/cli/main.py` (`listen` Typer sub-app)
- Modify: `README.md`, `ROADMAP.md`, `CLAUDE.md` (voice section)
- Test: `tests/test_scheduler.py` (extend), `tests/test_cli_voice.py` (new)

**Interfaces:**
- Consumes: `voice.assistant.run_loop`/`voice_registry`/`play_chime`, `voice.audio.mic_frames`, `voice.wake.WakeDetector`, `voice.stt.Transcriber`, `voice.tts.speak`, `cli.chat.resolve_model`, `llm.factory.build_client`, scheduler patterns.
- Produces: `scheduler.LISTEN_LABEL = "com.lydia.listen"`, `LISTEN_PLIST_PATH`, `LISTEN_LOG_PATH = ~/.lydia/listen.log`, `enable_listen(lydia_path=None, runner=subprocess.run) -> Path`, `disable_listen(runner=...)`, `listen_enabled() -> bool`; CLI `lydia listen` (runs loop), `lydia listen enable|disable|status`.

- [ ] **Step 1: Write the failing tests**

Extend `tests/test_scheduler.py` (mirror the automations tests exactly: monkeypatch `LISTEN_PLIST_PATH` to tmp_path, fake runner):

```python
def test_enable_listen_writes_runatload_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler, "LISTEN_PLIST_PATH", tmp_path / "listen.plist")
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    path = scheduler.enable_listen(lydia_path="/usr/local/bin/lydia", runner=fake_run)
    content = path.read_text()
    assert "com.lydia.listen" in content and "RunAtLoad" in content and "KeepAlive" in content
    assert "<string>listen</string>" in content
    assert calls[0][:2] == ["launchctl", "load"]


def test_disable_listen_unloads_and_removes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plist = tmp_path / "listen.plist"
    plist.write_text("x")
    monkeypatch.setattr(scheduler, "LISTEN_PLIST_PATH", plist)
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    scheduler.disable_listen(runner=fake_run)
    assert not plist.exists() and calls[0][:2] == ["launchctl", "unload"]
```

New `tests/test_cli_voice.py` (CliRunner; the loop itself is faked, so the CLI test only proves wiring):

```python
"""tests/test_cli_voice.py"""
from typer.testing import CliRunner

from lydia.cli import main as cli_main

runner = CliRunner()


def test_listen_status_reports_disabled(monkeypatch):
    monkeypatch.setattr("lydia.cli.scheduler.listen_enabled", lambda: False)
    result = runner.invoke(cli_main.app, ["listen", "status"])
    assert result.exit_code == 0 and "not" in result.output.lower()


def test_listen_enable_calls_scheduler(monkeypatch):
    called = {}
    monkeypatch.setattr("lydia.cli.scheduler.enable_listen",
                        lambda **kw: called.setdefault("yes", True) or __import__("pathlib").Path("/tmp/x"))
    result = runner.invoke(cli_main.app, ["listen", "enable"])
    assert result.exit_code == 0 and called
```

- [ ] **Step 2: Run to verify failure.** FAIL.

- [ ] **Step 3: Implement scheduler additions**

Append to `cli/scheduler.py` (same shape as the automations block; `_keepalive_plist_contents` has `RunAtLoad` true + `KeepAlive` true, ProgramArguments `[lydia_path, "listen"]`, logs to `LISTEN_LOG_PATH`):

```python
LISTEN_LABEL = "com.lydia.listen"
LISTEN_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LISTEN_LABEL}.plist"
LISTEN_LOG_PATH = Path.home() / ".lydia" / "listen.log"


def _keepalive_plist_contents(lydia_path: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>{LISTEN_LABEL}</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>{lydia_path}</string>
\t\t<string>listen</string>
\t</array>
\t<key>RunAtLoad</key>
\t<true/>
\t<key>KeepAlive</key>
\t<true/>
\t<key>StandardOutPath</key>
\t<string>{LISTEN_LOG_PATH}</string>
\t<key>StandardErrorPath</key>
\t<string>{LISTEN_LOG_PATH}</string>
</dict>
</plist>
"""


def enable_listen(lydia_path: str | None = None, runner: Runner = subprocess.run) -> Path:
    resolved_path = lydia_path or _find_lydia_executable()
    LISTEN_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LISTEN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LISTEN_PLIST_PATH.write_text(_keepalive_plist_contents(resolved_path), encoding="utf-8")
    result = runner(["launchctl", "load", str(LISTEN_PLIST_PATH)], capture_output=True, text=True)
    if result.returncode != 0:
        raise ScheduleError(f"launchctl load failed: {(result.stderr or result.stdout).strip()}")
    return LISTEN_PLIST_PATH


def disable_listen(runner: Runner = subprocess.run) -> None:
    if not LISTEN_PLIST_PATH.is_file():
        return
    runner(["launchctl", "unload", str(LISTEN_PLIST_PATH)], capture_output=True, text=True)
    LISTEN_PLIST_PATH.unlink()


def listen_enabled() -> bool:
    return LISTEN_PLIST_PATH.is_file()
```

- [ ] **Step 4: Implement the CLI sub-app** in `main.py` (near the automations apps):

```python
listen_app = typer.Typer(invoke_without_command=True,
                         help="Always-listening voice assistant (\"Hey Jarvis\").")
app.add_typer(listen_app, name="listen")


@listen_app.callback()
def listen_run(ctx: typer.Context) -> None:
    """With no subcommand: run the voice loop in the foreground (Ctrl-C stops)."""
    if ctx.invoked_subcommand is not None:
        return
    from lydia.cli.chat import resolve_model
    from lydia.voice import assistant, audio, tts
    from lydia.voice.stt import Transcriber
    from lydia.voice.wake import WakeDetector

    config = load_config()
    with build_client(config) as client:
        if not client.is_alive():
            ui.print_error(f"Cannot reach {config.server_url or config.ollama_host}.")
            raise typer.Exit(1)
        model = resolve_model(client, config)
        ui.print_info(f'Listening for "{config.voice_wake_word.replace("_", " ")}" — Ctrl-C to stop.')
        try:
            assistant.run_loop(
                config, client, model,
                frames=audio.mic_frames(),
                wake=WakeDetector(config.voice_wake_word),
                transcriber=Transcriber(config.voice_stt_model),
                speak_fn=lambda text: tts.speak(text, voice=config.voice_tts_voice),
                chime_fn=assistant.play_chime,
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
```

(Match `main.py`'s actual import/helper conventions: `load_config`, `build_client`, and `ui` are already imported there; verify before writing.)

- [ ] **Step 5: Run to green** on the new and extended tests, then the full suite.
- [ ] **Step 6: Docs.** README: new "Voice mode" section (setup: `pip install -e .` pulls deps; first `lydia listen` downloads the Whisper model ~150MB and triggers the macOS mic-permission prompt; wake word is "Hey Jarvis"; `lydia listen enable` for always-on; note battery cost and `disable`). ROADMAP: mark voice shipped, note stretch goals (custom "Hey Lydia" model, Piper voice, follow-up window). CLAUDE.md: add `voice/` to the architecture layering diagram (depends on: agent, llm, config) with one paragraph, same style as `automations/`.
- [ ] **Step 7: Commit**: `Wire voice mode into the CLI with launchd and docs`

---

## Manual end-to-end verification (Levi's machine, after all tasks)

- [ ] `.venv/bin/pip install -e ".[dev]"`: deps resolve on Apple Silicon.
- [ ] `lydia listen` in a terminal → grant mic permission → first run downloads models.
- [ ] Say "Hey Jarvis" → chime → "what's in the AI news today?" → spoken reply.
- [ ] Ask "check my email" → it calls the tool and speaks a short summary.
- [ ] Quit Ollama, say "Hey Jarvis" + anything → hears "I can't reach my brain right now", loop survives.
- [ ] `lydia listen enable` → `launchctl list | grep lydia` shows `com.lydia.listen`; log at `~/.lydia/listen.log`.
- [ ] `lydia listen disable` → process gone, mic released (menu-bar orange dot off).
