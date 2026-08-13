# Providers

This guide uses *provider* for AI, speech, and vision adapters. GitHub, email,
and calendar use the external-service lifecycle described in
[integrations.md](integrations.md); browser engines are covered in
[browser.md](browser.md).

All provider and external/tool content is untrusted. Model requests, active
conversation context, and action-result payloads are bounded before transport.
When JARVIS asks a model to summarize an executed action, that follow-up has no
tools and cannot propose another action; its raw tool exchange is discarded
afterward instead of entering ordinary conversation history. Generated prose is
presentation only and must not override the structured action result or local
permission policy.

Providers translate between JARVIS's internal models and external AI services.
They are optional adapters, not trusted execution engines.

## Implemented provider interfaces

### Language models

`LLMProvider.complete_with_tools(messages, tools)` accepts provider-neutral
conversation messages and the exact action schemas exported by the local
registry. Implemented adapters are:

- `OpenAIResponsesProvider`, for the official OpenAI Responses API; and
- `OpenAICompatibleProvider`, for Chat Completions-style tool-calling endpoints.

The Responses adapter sends caller-managed history with `store: false` and
requires an API key. The compatible adapter accepts a configurable absolute
HTTP(S) base URL and may omit an API key for a trusted local endpoint.

### Vision

`VisionProvider.analyze_image(path, prompt)` accepts one local image and returns
a semantic analysis. Implemented adapters are:

- `OpenAIResponsesVisionProvider`; and
- `OpenAICompatibleVisionProvider`.

Neither implemented adapter advertises coordinate grounding. See
[vision.md](vision.md).

### Voice

Voice uses separate `SpeechToText` and `TextToSpeech` interfaces:

- `SpeechRecognitionSTT` uses the optional SpeechRecognition package and its
  Google recognizer. Audio transcription therefore depends on an external
  Google service and network availability.
- `Pyttsx3TTS` uses the optional local `pyttsx3` system-speech engine.

`WakeWordDetector` is an extension contract only. No wake-word engine or
continuous-listening implementation is bundled.

Activating voice requires an explicit `stt_provider` of `google` or
`speech-recognition`; the default `none` value fails startup. The bundled
adapter can upload captured audio to Google, so leave voice disabled if remote
transcription is not acceptable.

## Enabling AI or vision

Provider-backed features are disabled by default and run only when explicitly
enabled with a model and any required credentials. Configuration can come from
`~/.jarvis/config.toml`, an explicitly selected TOML file, or `JARVIS_*`
environment variables. Environment values override TOML.

Example for the official OpenAI adapters:

```toml
[ai]
enabled = true
provider = "openai"
model = "<tool-capable-model>"

[vision]
enabled = true
provider = "openai"
model = "<vision-capable-model>"
```

Set credentials in the environment rather than committing them:

```powershell
$env:JARVIS_AI_API_KEY = "<secret>"
$env:JARVIS_VISION_API_KEY = "<secret>"
```

For a compatible endpoint, set `provider = "openai-compatible"` and an absolute
HTTPS `base_url` you trust. Compatibility is not guaranteed: the endpoint must
implement the specific tool-calling or multimodal request and response shapes
used by the adapter. The low-level adapter also accepts HTTP for trusted local
development, but rejects an API key on an HTTP base URL. Its built-in transport
permits only same-origin redirects and blocks HTTPS downgrades. Avoid untrusted
proxy endpoints that could receive private payloads.

Base URLs with embedded usernames/passwords, query strings, or fragments are
rejected. The default transport bounds response bodies and converts provider
HTTP, JSON, and malformed-payload failures into sanitized domain errors rather
than surfacing response bodies to users.

Relevant environment variables are:

```text
JARVIS_AI_ENABLED
JARVIS_AI_PROVIDER
JARVIS_AI_MODEL
JARVIS_AI_BASE_URL
JARVIS_AI_API_KEY
JARVIS_AI_TIMEOUT_SECONDS

JARVIS_VISION_ENABLED
JARVIS_VISION_PROVIDER
JARVIS_VISION_MODEL
JARVIS_VISION_BASE_URL
JARVIS_VISION_API_KEY
JARVIS_VISION_TIMEOUT_SECONDS
```

The configuration diagnostic output redacts API keys. Do not place credentials
in screenshots, issue reports, command history, logs, or committed TOML files.

## Trust and data flow

Enabling a hosted LLM transmits the active bounded conversation history used for
the request, action schemas, prompts, and tool results. Enabling hosted vision
transmits the requested screenshot and analysis prompt. Review the provider's
terms, retention settings, region, and organizational data policy before
enabling either mode. Local cleanup cannot delete a provider-side copy.
Provider-backed screen analysis is classified `SENSITIVE` and asks for
confirmation by default.

Deterministically recognized local action commands can contain memory values or
text to type. The runtime deliberately does not append those commands or action
arguments to conversation history, so a later remote-model turn does not
silently receive them. Requests that actually use an enabled LLM and the tool
results from those requests are part of the active provider conversation.

A provider response is always untrusted:

- only registered tool names are accepted;
- arguments must pass the local strict schema;
- local permission policy is applied after parsing;
- handlers remain trusted, explicit Python code; and
- no response is executed as shell, PowerShell, CMD, Bash, Python, or another
  programming language.

## Adding a provider

Use the Provider Request issue template before adding an adapter. A proposal
should identify the exact API, authentication and secret handling, local versus
hosted processing, payload retention, tool/vision support, streaming behavior,
timeouts, error mapping, licensing, optional dependencies, and a mock-based test
strategy. Standard tests must not need paid credentials or live network access.

New providers should preserve the provider-neutral models instead of branching
core orchestration by vendor.
