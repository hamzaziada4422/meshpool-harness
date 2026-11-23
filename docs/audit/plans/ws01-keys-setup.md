# ws01-keys-setup: keys, redirects and provider setup (plan)

Checked against 0.0.12 (branch base c189e04). Every item below still reproduces unless stated. Line numbers are 0.0.12.

## Root causes, grouped

### R1. No shared transport for keyed model calls (W-01, S-07, W-15, H-04)

Every call that carries the endpoint key builds its own `urllib.request.urlopen`:
`core/wire.py:190` (SimpleLedger: engines, `meshpool check`, onboarding), `agent/core.py:320,336,2250` (app agent,
probe, stream), `ui/workbench.py:177` (model list), `ui/settings_v2.py:179` (key spend), `engines/forge/design.py:474`.
Only `provider_setup._get` refuses redirects (`provider_setup.py:85`).

- W-01 / S-07: urllib's default `HTTPRedirectHandler` follows 301/302/303 to any host and copies every header,
  including `Authorization` / `x-api-key`, even https -> http. It refuses 307/308 on POST (raises), so a legitimate
  same-host 307 fails.
- W-15: urllib's `ProxyHandler` uses `HTTP_PROXY`/`HTTPS_PROXY` for 127.0.0.1/localhost unless `NO_PROXY` lists it, so
  a local model server is reached through the proxy, with the key.
- H-04: `ssl.create_default_context()` on python.org macOS Python has no CA certificates (until "Install
  Certificates.command"), and a corporate TLS proxy's CA is in the OS keychain, not in OpenSSL's bundle. Every HTTPS
  call then fails with CERTIFICATE_VERIFY_FAILED, reported as "connection problem". Only `harnesses/acp.py` forwards
  SSL_CERT_FILE to agents ("partly handled").

Fix (removes the class): new `meshpool_harness/core/http.py`, the only way a keyed request is opened.
- `open(req, timeout, handlers=())`: an opener with
  - `_KeyedRedirect`: follows 301/302/303/307/308 only to the same host (an http -> https upgrade on the same host is
    allowed, https -> http never), keeps method and body, at most 5 hops. Any other target raises
    `urllib.error.HTTPError(code=<3xx>)` with a message naming the new host and saying the key was not sent there.
  - no proxy for loopback hosts (127.0.0.0/8, ::1, localhost, 0.0.0.0): `ProxyHandler({})`; other hosts keep the
    environment's proxies.
  - `HTTPSHandler(context=tls_context())`.
- `tls_context()` (cached): the default context; when it holds no CA certificates, or `MESHPOOL_CA_BUNDLE` /
  `SSL_CERT_FILE` is set, load in order: `MESHPOOL_CA_BUNDLE`, `SSL_CERT_FILE`, `certifi` if importable, system bundle
  files (`/etc/ssl/cert.pem`, `/etc/ssl/certs/ca-certificates.crt`, `/etc/pki/tls/certs/ca-bundle.crt`); on macOS also
  the System and SystemRoot keychains exported with `/usr/bin/security find-certificate -a -p` (covers a corporate
  proxy CA). `install_tls()` sets `ssl._create_default_https_context` to it so every other urllib call in the app
  (web fetch, updater, MCP) gets the same trust, and sets `SSL_CERT_FILE`/`NODE_EXTRA_CA_CERTS` for child processes
  only when Meshpool built a bundle and they are unset. Called once from `config.load_into_env`.
- `explain(exc)`: CERTIFICATE_VERIFY_FAILED -> "The HTTPS certificate of <host> could not be verified ... set
  MESHPOOL_CA_BUNDLE to your company's CA file"; used where errors are turned into text.
- All keyed call sites above switch to `http.open` (agent/core.py keeps its `_TrackHTTP/_TrackHTTPS` sink handlers by
  passing them in; the HTTPS tracker gets the TLS context). `provider_setup._get` uses it too, keeping "never follow"
  (setup must show the user the redirect, so it passes a refuse-all redirect handler).

Tests (`app/tests/test_http_transport.py`, real local servers on two ports/hosts):
- `test_cross_host_redirect_never_carries_key[301|302|303|307|308]` for SimpleLedger, the app agent `_post` (stream and
  non-stream), the Anthropic wire, workbench model list: the second server records no Authorization/x-api-key, the call
  fails with HTTP 3xx and a message naming the host. Fails today (second server sees the key).
- `test_https_to_http_downgrade_refused`.
- `test_same_host_307_and_308_resend_post_body` (fails today: urllib raises on 307 POST).
- `test_loopback_bypasses_http_proxy`: HTTP_PROXY/HTTPS_PROXY point at a recording dead proxy; a call to 127.0.0.1
  succeeds and the proxy sees nothing. Fails today.
- `test_tls_context_loads_fallback_bundle_when_default_is_empty` (monkeypatched `cert_store_stats` -> 0 CAs, fake bundle
  file; macOS branch with a fake `security` binary on PATH and `sys.platform` monkeypatched), `test_ca_bundle_env_wins`,
  `test_certificate_error_message_is_actionable`, plus a real TLS server on 127.0.0.1 with a self-signed CA proving a
  call fails without and passes with `MESHPOOL_CA_BUNDLE`.

### R2. Key and endpoint resolved independently (S-01, M15-01)

`providers._cfg` (`providers.py:33`) takes each name from the settings file first, else the environment, separately.
A settings file copied from `defaults/meshpool.config` has `MESHPOOL_API_KEY=` (empty) and the default base URL, so an
environment key set for another endpoint (with `MESHPOOL_BASE_URL` in the environment too) is paired with the file's
default base and sent to llm.meshpool.ai (S-01). `ui/workbench.py:164-165` and `cli._check` read os.environ directly,
after `load_into_env(override=True)` has replaced the shell's base URL with the file's.

`provider_setup.save` (`provider_setup.py:287-289`) writes `MESHPOOL_API_KEY` only when a key was given, so saving a
local endpoint with no key keeps the hosted key, which then goes to the local server (M15-01). `ui/server.save_settings`
has the same shape (blank key field = keep), guarded only by a host comparison.

Fix: one resolver, `providers.endpoint_pair() -> (base, key, source)`; `key()`/`key_base()` delegate to it:
1. settings file has a key: (file base or default, file key).
2. settings file has an explicit base that is not the default and no key: (file base, "") (a keyless local endpoint).
3. shell environment key: (shell base or default, shell key), where "shell" means values not injected from the file
   (`config` records the shell's own values before `load_into_env` overrides them).
4. OPENAI_API_KEY + OPENAI_BASE_URL (unchanged rule).
5. (file base or shell base or default, "").
A key is never paired with a base from another source. `workbench.models`, `cli._check`, `server.get_settings` use it.
`provider_setup.save` and `server.save_settings`: when the endpoint changes and no key is given, write
`MESHPOOL_API_KEY=` (cleared) instead of keeping the old one; a keyless non-local endpoint is still refused.

Tests (`app/tests/test_key_endpoint_pairing.py`): defaults-copied file + env key + env Azure-like base -> pair is the
env base (mock server receives the key, a mock "default" server receives nothing; the default URL is monkeypatched to a
local recorder); env key without env base + file custom base -> key not sent there; `load_into_env(override=True)` does
not break the pairing; M15-01: save hosted endpoint with key, then save `http://127.0.0.1:<port>/v1` without a key ->
the local server receives no Authorization header and `PV.key()` is "". Same through `/api/settings`.

### R3. Setup checks accept what is not a working model endpoint (F-14, W-06, W-17, W-18, W-16, F-15, X-07)

- F-14: `provider_setup.test` returns ok when `/models` is 404 or a 200 non-list (`provider_setup.py:238-240`,
  "the key stays unchecked"); `server.test_connection` -> `cli.ping` -> `SimpleLedger.chat` routes by `PV.split(model)`,
  so a `chatgpt:` default model, or `MH_PROVIDER=chatgpt` with no key, tests the ChatGPT sign-in instead of the typed
  key.
- W-06: `SimpleLedger.chat` accepts any 200: a streamed 200 that is not SSE (LM Studio error JSON, an HTML page) gives
  an empty reply, and a non-streamed 200 without `choices` is returned as is. `ping` reports OK; chats end "(no reply)".
- W-17: with no `/models`, the wire defaults to openai (`provider_setup.py:239`).
- W-18: `_get` does not catch `http.client.HTTPException` (BadStatusLine from a non-HTTP port), and the server route
  only catches `SetupError`, so the page gets HTTP 500.
- W-16: `normalise_base` gives `https://` to every bare host except localhost/127./::1, so `0.0.0.0:1234` (and LAN
  addresses) get https; the TLS handshake failure is a URLError -> "No server at that address".
- F-15: `server.test_connection` never validates the base; `_key_follows_host` and `connMsg` turn a malformed URL into
  "Enter the API key for ..."/"didn't accept this key".
- X-07: `cli.ping` sends `max_tokens=5`; Responses API endpoints require >= 16.

Fix:
- `provider_setup._get`: catch `http.client.HTTPException` -> "not_http"; `ssl.SSLError` (and URLError wrapping one) ->
  "tls"; messages: "Something answers at that address, but it is not a web API" / "That address does not speak HTTPS:
  try http://..." (with the http URL).
- `normalise_base`: `http://` for loopback, 0.0.0.0, `*.local`, private IPv4 ranges and bare hosts with an explicit
  port that are not public names; https elsewhere.
- `provider_setup.test`: when `/models` is missing (404/405, or 200 that is not a model list), probe with a real tiny
  call on each wire (`POST /chat/completions` Bearer, `POST /messages` x-api-key, `max_tokens` 16, model = the given
  model or a placeholder): 401/403 -> key rejected; 404 -> wire absent; a 200 that parses as that wire's reply, or a
  400/404/422 JSON error that names the model -> wire present and key accepted. ok only then, with the detected wire.
  An HTML or non-JSON 200 -> not ok ("answers, but not like a model API"). `test` takes an optional `model`.
- `SimpleLedger.chat` (`core/wire.py`): a 200 whose body is not a reply of the wire (no `choices` for chat, no
  `content` for Messages, no `output` for Responses; streamed body with no `data:` events) raises
  `RuntimeError("meshpool HTTP 200 ... not a model reply: <first 200 chars>")`; an LM Studio style `{"error": ...}`
  with 200 is surfaced as that error.
- `cli.ping`: always tests the key endpoint (never routes to ChatGPT: strips a `chatgpt:` prefix by using the endpoint's
  default model, and bypasses `MH_PROVIDER=chatgpt`), `max_tokens=16`, ok only with non-empty content or a valid
  reply object; `meshpool check --live` prints the endpoint it tested.
- `server.test_connection` / `save_settings`: base validated and normalised with `provider_setup.normalise_base`
  first; a bad address gets the address message, never a key message.

Tests (`app/tests/test_provider_setup_checks.py`, mock servers): wrong key on an endpoint without /models -> not ok
(F-14); `/api/test` with `MH_MODEL=chatgpt:x` + wrong key -> not ok and the key endpoint was called (F-14);
LM Studio 200 error body and HTML 200 -> not ok for `test`, `ping`, and `SimpleLedger.chat` raises (W-06); Anthropic
endpoint without /models -> wire "anthropic" (W-17); raw TCP server speaking garbage -> ok False with the not-HTTP
message, `/api/provider/test` returns 200 (W-18); `0.0.0.0:port` -> http and a plain-http server is reached; an https
URL to a plain-http server -> the "does not speak HTTPS" message (W-16); `/api/test` with `MESHPOOL_BASE_URL="ht!tp:/x"`
-> address message (F-15); Responses-style mock rejecting `max_output_tokens < 16` -> `meshpool check --live` passes
(X-07).

### R4. Key cleaning, wording and onboarding flow (F-38, F-16, W-23, M15-04)

- F-38: `ui/server.py:157` and `:157`-style code in `test_connection` remove all whitespace first, so "Bearer sk-1"
  becomes "Bearersk-1" and the `^bearer\s+` strip never matches. `provider_setup.clean_key` does it in the right order.
  Fix: every key input (`save_settings`, `test_connection`, the new onboarding route, CLI `key_step`) uses
  `provider_setup.clean_key`.
- F-16: `providers.setup("key")` says "Add your Meshpool key", the CLI says "Connect your Meshpool account"/"Checking
  your key", `cli._check` says "API key" with Meshpool defaults, when the endpoint is another provider. Fix: wording
  from `providers.key_name()` (the endpoint's host) everywhere the key endpoint is named.
- W-23: web onboarding posts the typed base untouched to `/api/test` and `/api/settings`; "localhost:11434" has no
  scheme and ".../v1/chat/completions" keeps the path, so the check 404s. CLI custom onboarding has the same gap.
  Fix: both go through `normalise_base` + the `/v1` candidate logic of provider_setup.
- M15-04: web onboarding saves with `/api/settings` then `/api/provider` (`app.js:2192`); closing between them leaves
  the key saved, the provider unset and onboarding shown again. Fix: `POST /api/onboard` does check + one `C.write`
  of key, base, model and provider (via `HR.use_provider` values merged into the same write); `app.js` calls only it.

Tests (`app/tests/test_onboarding_keys.py`): "Bearer sk-..." / "'Bearer sk-...'" saved and tested as "sk-..." through
`/api/settings` and `/api/test` (F-38); `providers.setup("key")` and `cli._check` output name the custom host and not
Meshpool when the endpoint is custom (F-16); `/api/onboard` with base "127.0.0.1:<port>" and with
".../v1/chat/completions" passes against a mock server and saves the normalised base (W-23); `/api/onboard` writes key
and provider in one `C.write` call (spy) and a crash injected after the write leaves both set (M15-04); CLI `_key_step`
custom with "localhost:<port>" normalises.

## Execution

Four Sonnet subagents, each in its own worktree, merged here:
- A (R1): core/http.py + call sites (wire.py transport line only, agent/core.py transport, workbench.py:177 urlopen
  line, settings_v2.py, forge/design.py, provider_setup._get opener) + test_http_transport.py.
- B (R2): providers.py resolver, config.py shell snapshot, provider_setup.save key clearing, server.save_settings key
  clearing / get_settings, workbench.models key+base lines, cli._check source lines + test_key_endpoint_pairing.py.
- C (R3 setup side): provider_setup.test/_get error kinds/normalise_base + server provider-route catch-all +
  test_provider_setup_checks.py (setup half).
- D (R3 ping side + R4): wire.py reply validation, cli.ping, server.test_connection/save_settings validation, /api/onboard,
  app.js onboarding, onboard.py, providers wording + test_onboarding_keys.py and the ping half of R3 tests.
Then review line by line, revert-and-rerun each new test, full suite with `-n 4`.
