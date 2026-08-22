# Voice Mode ("Hey Jarvis"): Design

Date: 2026-07-17
Status: Approved (design conversation with Levi; Approach A chosen)

## Goal

An always-listening, fully local, $0 voice assistant on the MacBook Air: say the
wake word, ask something out loud, Lydia answers out loud. Assistant-only
capabilities only: the safe connector tools (email, Canvas, stocks, news) and
conversation, never file edits or shell commands by voice.

## Constraints

- Free forever, fully offline: no cloud STT/TTS, no API keys, no accounts.
- Runs on the MacBook Air with local Ollama (`server_url` unset), the
  summer-mode setup. Must not assume the gaming PC exists.
- Wake word v1 is **"Hey Jarvis"** (openWakeWord's best pre-trained model).
  A custom "Hey Lydia" model is a stretch goal, not v1.
- Mic is permanently hot when enabled; `lydia listen disable` must fully stop it.
- Unit tests never touch the microphone, audio devices, model downloads, or a
  live Ollama. Every stage is behind an injectable interface.

## New dependencies

`openwakeword` (wake word, ONNX runtime), `faster-whisper` (STT), `sounddevice`
(mic capture). TTS uses macOS `say` via subprocess, so no dependency.
First run downloads the Whisper model (~150MB, cached in `~/.cache`).

## Architecture

New `src/lydia/voice/` package, same layering rule as `automations/`: may import
`agent/`, `llm/`, `config/`, but **never `cli/`**.

```
voice/
  audio.py      mic capture + energy/VAD silence detection  (wraps sounddevice)
  wake.py       WakeDetector: feeds audio frames to openWakeWord, fires on match
  stt.py        Transcriber: faster-whisper wrapper, audio buffer -> text
  tts.py        speak(text): strip markdown/emoji, subprocess `say`; injectable runner
  assistant.py  the loop: wake -> chime -> record until silence -> transcribe
                -> agent turn -> speak. All stages injected (testable with fakes).
```

CLI (in `cli/main.py` + `cli/scheduler.py`, following existing patterns):

- `lydia listen`: run the loop in the foreground, Ctrl-C to stop.
- `lydia listen enable|disable|status`: third launchd agent
  `com.lydia.listen` (`RunAtLoad` + `KeepAlive` so it starts at login and
  restarts on crash), logging to `~/.lydia/listen.log`. Mirrors
  `enable_automations` exactly.

## The loop (assistant.py)

1. Wake word detected → play a short chime (afplay a bundled/system sound) so
   Levi knows the mic is recording.
2. Record until ~1.2s of silence (VAD), hard cap 15s.
3. Transcribe with faster-whisper (`base.en` default; config-overridable).
   Empty/garbage transcription → soft "didn't catch that" chime, back to step 1.
4. One agent turn via the existing agent loop with:
   - a **filtered registry**: safe-risk connector tools only (`check_email`,
     `check_canvas`, `check_stocks`, `check_news`, `notify`), reusing the
     risk-tier filtering in `agent/tools.py`; `confirm=lambda _r: False`.
   - a voice system prompt: answers are spoken aloud, so 1–3 sentences, no
     markdown, no lists, no code.
   - `keep_alive=config.keep_alive` as everywhere.
5. Speak the reply with `say`. One exchange per wake in v1 (no follow-up
   window, no barge-in interruption).
6. Any exception: log it, speak nothing or a one-line "something went wrong",
   keep the loop alive. Ollama unreachable → speak "I can't reach my brain
   right now" and continue listening.

## Config (new LydiaConfig keys)

- `voice_wake_word: str = "hey_jarvis"` (an openWakeWord model name)
- `voice_stt_model: str = "base.en"`
- `voice_tts_voice: str | None = None` (None = system default voice; e.g. "Samantha")

## Error handling & robustness

- The loop must survive: mic device changes, model load failures at startup
  (clear spoken/printed error, exit nonzero so launchd restarts), transient
  Ollama outages, and empty transcriptions.
- launchd `KeepAlive` restarts crashes; the log file is the debugging surface.
- macOS asks for mic permission once (TCC) on first `lydia listen`.

## Testing

- Every stage faked: FakeWake (fires on demand), FakeTranscriber (canned text),
  FakeSpeaker (records what would be spoken), FakeAudio (canned frames).
- `assistant.py` loop unit-tested end-to-end with fakes: wake→answer→speak,
  empty transcription path, Ollama-down path, exception-survival path.
- `tts.py` tested with an injected runner (asserts the `say` argv, never runs it).
- scheduler tests mirror the existing fake-launchctl pattern.
- Real-mic/e2e verification is a manual checklist (like automations).

## Out of scope (v1)

- Custom "Hey Lydia" wake word (stretch: openWakeWord custom training).
- Follow-up conversation window, barge-in/interruption, Piper/Kokoro voices.
- Voice-driven coding tools or anything above safe risk.
- Voice control of automations CRUD beyond what the model can do with its tools.

## Battery note

openWakeWord inference on frames is ~1–2% CPU. Real cost is the always-hot mic;
acceptable per Levi. `lydia listen disable` unloads the launchd agent entirely.
