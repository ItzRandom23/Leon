# Vision and screenshots

JARVIS 0.2.0 provides a semantic screen-understanding foundation. It does not
provide coordinate grounding or automatic visual clicking.

## Capture lifetimes

The screen controller supports:

- persistent full-screen screenshots;
- persistent active-window screenshots on supported Windows desktops;
- temporary full-screen screenshots; and
- temporary active-window screenshots.

Persistent captures use generated PNG names under the configured screenshot
directory, which defaults to:

```text
~/.jarvis/screenshots
```

Temporary captures are deleted in a `finally` path after the consumer finishes
or fails. Explicit `take screenshot` and `capture active window` requests create
persistent files and accumulate them until the user removes them. Configure
another absolute directory with:

```toml
[screenshots]
directory = "D:/JarvisData/screenshots"
keep_temporary = false
```

Screenshot capture requires Pillow and an interactive desktop. Active-window
capture additionally depends on Windows returning usable window bounds.

## Semantic analysis

`VisionAnalyzer` captures a temporary screenshot, sends it to the selected
`VisionProvider`, returns a description, and then removes the temporary file.
Because this crosses a provider boundary and can reveal screen content,
`analyze_screen` is `SENSITIVE` and asks for confirmation by default. Local
persistent screenshot capture is a distinct `READ` action.

Representative requests include:

```text
what's on my screen?
what error is visible?
explain this dialog
read the visible text
```

Implemented remote adapters support the official OpenAI Responses image input
and an OpenAI-compatible multimodal Chat Completions shape. Images are size
bounded, encoded as data URLs, and sent only after a user request reaches an
enabled, configured provider.

## No coordinate grounding

Both built-in vision adapters set `supports_grounding = false`. Their prompts
explicitly tell the model not to invent coordinates. JARVIS may describe a Save
button or report visible text, but it will not convert that prose into a click.

The vision models include bounding-box and target types, plus a parser that can
validate structured boxes from a future grounded provider. Those types are an
extension foundation, not evidence that the current providers return reliable
screen coordinates. Automatic visual clicking remains unavailable.

Coordinate-based mouse commands are separate, explicit actions such as:

```text
move the cursor to 500, 300
click at 500, 300
```

They use caller-supplied coordinates, validate them against current desktop
bounds, and remain subject to `ACTION` permission policy.

## Privacy and safety

Screenshots can expose credentials, private messages, health or financial data,
source code, customer information, notifications, and content from other
monitors. Before capture or analysis:

- close or cover sensitive content;
- understand whether all monitors may be included by the platform provider;
- review `READ` policy for local capture and `SENSITIVE` policy for remote
  analysis;
- verify which remote provider will receive the image;
- check that provider's retention and training settings; and
- remove persistent captures when they are no longer needed.

`keep_temporary = false` expresses the intended default, but remote provider
retention and filesystem backups are outside the local cleanup guarantee.

## Known limitations

- Screen capture can fail in headless sessions, locked desktops, remote
  sessions, protected surfaces, or when OS permissions deny access.
- Active-window capture and window bounds are Windows-first.
- Semantic descriptions can be incomplete or incorrect; do not use them as the
  sole basis for safety-critical decisions.
- OCR is provider-dependent rather than a dedicated local OCR engine.
- There is no region redaction, sensitive-field detection, visual target
  disambiguation, or automatic click loop in this release.
