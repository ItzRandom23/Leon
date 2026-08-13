# Browser automation

Phase 7 provides an opt-in, bounded Playwright adapter for supervised public-web
reading and interaction. It is an explicit action surface, not a general browser
remote-control API: callers cannot submit CSS/XPath selectors, arbitrary
JavaScript, browser command-line flags, or a persistent profile.

Status: the controller/action foundation and fake-based automated coverage are
implemented. A real session is experimental and requires Playwright, a matching
browser binary, DNS/network access, and an environment where that browser can
launch. Normal unit tests do not claim a live browser was manually exercised.

## Enable the runtime

```powershell
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

```toml
[browser]
enabled = true
browser_type = "chromium"
headless = true
profile = "ephemeral"
max_sessions = 2
max_tabs = 8
```

Run `jarvis doctor` before starting a session. Supported engine names are
`chromium`, `firefox`, and `webkit`; install the selected Playwright binary
separately.

## Action surface

| Action | Category | Behavior |
| --- | --- | --- |
| `browser_start` | `ACTION` | Start an isolated browser context and initial tab |
| `browser_close` | `DESTRUCTIVE` | Close the active context and all its tabs |
| `browser_navigate` | `ACTION` | Navigate to an explicit validated public HTTP(S) URL |
| `browser_search_web` | `SENSITIVE` | Put a bounded query into a DuckDuckGo URL |
| `browser_snapshot` | `SENSITIVE` | Read bounded visible text and interactive accessibility-like entries |
| `browser_click` | `SENSITIVE`, always confirm | Click the exact verified element from the latest snapshot |
| `browser_type` | `SENSITIVE`, always confirm | Type at most 500 characters into the exact verified element |
| `browser_press_key` | `SENSITIVE`, always confirm | Press one allowlisted navigation/edit key on the verified element |
| `browser_back`, `browser_forward`, `browser_reload` | `ACTION` | Navigate the active tab's history |
| `browser_scroll` | `ACTION` | Scroll vertically by a bounded delta |
| `browser_list_tabs` | `SENSITIVE` | Return bounded tab IDs, titles, URLs, and active state |
| `browser_visible_text` | `SENSITIVE` | Return bounded visible body text |
| `browser_find_text` | `SENSITIVE` | Find a bounded literal string in visible text |
| `browser_list_downloads` | `SENSITIVE` | Return in-memory filename/source metadata only |

Web reads are `SENSITIVE`, not ordinary `READ`, because URLs, tab titles, page
text, search terms, and accessible labels can reveal private browsing context.
The three element actions retain mandatory confirmation even when the configured
`SENSITIVE` policy is `allow`.

## Snapshot-to-action workflow

1. Start the browser and navigate to a public URL.
2. Take a fresh snapshot.
3. Review the returned title, URL, visible text, and bounded interactive entries.
4. Select an entry by its opaque `element_id` and repeat its exact `role` and
   accessible `name` in the action request.
5. Review the permission prompt, including the target and any text to be typed.
6. JARVIS rechecks the same node before acting. Navigation, tab/page changes,
   disabled/detached nodes, changed role/name/type, or stale snapshots fail
   closed.

Opaque IDs such as `s3-e2` are not selectors and are meaningful only for the
latest snapshot. An action binds to the stable element handle, active tab/page,
URL, tag/type, role, accessible name, and enabled state. After an interaction or
navigation, the snapshot is invalidated and a new one is required.

This reduces stale-target and time-of-check/time-of-use risk. It cannot prove
that a page's visible label honestly describes the site's eventual server-side
effect.

## Network policy

Each session starts a bounded authenticated proxy on `127.0.0.1`. The browser is
launched/configured to use that proxy without a direct-network bypass. The proxy
resolves destination hosts itself, requires an entirely public DNS answer, pins
the answer set for the session, and connects to a selected numeric public IP.
Both ordinary HTTP requests and HTTPS `CONNECT` tunnels pass through the same
destination policy. Per-session credentials prevent an unrelated local process
from casually using the listener.

The browser and proxy validate top-level navigation, routed subresources,
redirects, and tunnel targets:

- only absolute HTTP and HTTPS URLs are accepted;
- malformed escapes, embedded credentials, fragments with unsafe controls,
  backslashes, whitespace/control characters, and invalid host/port forms are
  rejected;
- localhost, `.localhost`, `.local`, and IP literals that are not globally
  routable are rejected;
- DNS resolution must return only global unicast addresses;
- a hostname's resolved address set is pinned for the session, and a changed set
  is rejected as possible rebinding;
- outbound sockets connect to the validated numeric address instead of asking a
  second resolver to reinterpret the hostname;
- HTTPS-to-HTTP redirects are rejected; and
- unsafe post-navigation URLs cause recovery to the prior page or `about:blank`.

Browser launch also disables or constrains common alternate/direct network
surfaces, including service workers, QUIC, WebRTC non-proxied UDP behavior,
background networking, speculative/prefetch activity, and resolver bypass
rules. Context init-script behavior is restricted so page setup cannot quietly
restore a direct network path outside the proxy policy.

This blocks ordinary access to loopback, link-local, private, reserved,
multicast, and metadata-service addresses. It is still defense in depth, not a
complete network sandbox. A public service can itself proxy a request elsewhere;
browser/OS compromise and the surrounding host/network remain outside the
application's proof boundary. HTTPS tunnels enforce the destination but do not
decrypt page content at the proxy. Do not use JARVIS as the sole isolation layer
around sensitive infrastructure.

## Isolation, limits, and downloads

- Every session uses a fresh ephemeral Playwright context. No persistent cookie,
  credential, extension, or history profile is configured.
- Service workers are blocked.
- Page-created popups are registered only within the configured per-session tab
  cap; excess popup/tab growth is closed or refused.
- Accepted downloads are disabled. Page download events may contribute a
  bounded in-memory record containing a sanitized suggested filename and public
  source URL; no file-content action is exposed.
- Default internal caps include 50,000 visible-text characters, 200 snapshot
  elements, 100 find matches, 500 typed characters, 100 download records, a
  10,000-pixel scroll delta, and a 15-second operation timeout.
- Configuration caps sessions at 8 and tabs at 32. The built-in action service
  exposes one active session while the lower-level controller supports bounded
  multiple sessions.
- Closing the application shuts down its browser resources.
- If browser runtime/context/session startup fails partway through, already
  allocated browser, proxy, socket, task, and session objects are closed so a
  retry does not inherit orphaned state.
- Proxy connections, header sizes, request lines, authentication attempts,
  resolution results, session/tab counts, timeouts, and owned background tasks
  are bounded and cleaned up at session/application shutdown.

## Untrusted content and prompt injection

All page-authored fields are tagged as untrusted and sanitized/bounded for
display. That does not make their meaning trustworthy. A page can instruct a
model or user to reveal secrets, change settings, enable a plugin, type a token,
or approve another action. Treat those instructions as page content, not as
JARVIS policy.

Never:

- enter a password, API key, recovery code, payment detail, or private message
  unless you deliberately intend to send it to that exact site;
- approve an interaction solely because the page or model says it is safe;
- assume a headless session is less network-capable; or
- treat a successful click as proof of the remote result.

The browser does not include a password manager, credential vault, CAPTCHA
bypass, file upload action, saved-download action, arbitrary script execution,
or unattended form-submission loop.

See [permissions](permissions.md), [security](security.md), and
[configuration](configuration.md).
