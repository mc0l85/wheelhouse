# PRD — herdr web hardening and input correctness (2026-11)

**Status:** decided, reviewer-checked (rev 2) · **Repo:** herdr-web (this repo) · **Owner:** repo owner
**Last updated:** 2026-11-15

## 1. Goal

Close the seven bug groups found by the 2026-11-15 review, each proven by a check that runs and can fail:

1. Restarting the web service never kills the herdr server or its panes; the web service never starts a server.
2. Mic auto-Enter never fires blind.
3. A terminal whose herdr client died says so and reconnects instead of freezing behind healthy pings.
4. A paste never carries control sequences into the pane; pane-set clipboard content is bounded.
5. Sticky Ctrl/Alt work on every keyboard, touch only the next key, and lock only on a double tap.
6. The IME / dictation / autocorrect input path never doubles, drops or misplaces text in the listed cases.
7. Smaller defects: exec-failure fork clone, push misses/duplicates, terminal-limit / connect-timeout feedback,
   open API docs, unpinned dependencies, stale deployment notes, no tests.

## 2. Non-goals

- Network access control and file-mode tightening (accepted risk by the owner).
- Push device eviction at the subscriber cap (follows from the accepted network model).
- Framing headers, argv hygiene for leading `-` text, new features, redesign, changes to herdr itself.

## 3. Reference

Review findings F1, F4–F9, F12–F14 (private report). Acceptance ledger: `prd/acceptance.json`.

## 4. Architecture

### 4.1 Frontend: split the hook, then extract pure modules

H0 does a **behaviour-neutral** split of `frontend/src/hooks/useHerdrTerminal.ts` into:
- `hooks/terminalSocket.ts` — socket lifecycle (connect, onopen/onclose, ping, reconnect). Exposes
  `send(bytes): boolean` (false when not open) and an `onReset` event fired on every (re)open.
- `hooks/useHerdrTerminal.ts` — xterm setup and input wiring.
- Frozen types: `TermApi`, `ModState = "off" | "once" | "locked"`, `ConnInfo { state, message? }`.

Then the pure modules (no DOM; clock and timers injected):

| Module | Contract |
|---|---|
| `input/sanitize.ts` | `sanitizePaste(text)`: drop ESC, other C0 except `\t \n \r`, DEL, and C1 (U+0080–U+009F). `OSC52_MAX_BYTES` counts decoded bytes. |
| `input/modifiers.ts` | `ModState` machine: tap → once; tap again within `DOUBLE_TAP_MS` → locked; tap while once/locked (after the window) → off; a tap after "once" was consumed → once. `apply(data, source)` with `source ∈ {key, composition, tray, paste, report}`: Ctrl applies to one printable char from key/composition/tray (table: a–z, space, `@ [ \ ] ^ _ ?`; unmapped char → sent unmodified, modifier still consumed); Alt applies to one printable char or `\r \t \x7f`; paste/report never modified and never consume. Emits a state-change callback so the UI indicator updates when "once" is consumed. |
| `input/composer.ts` | `reduce(state, event) → { state, send: string, effects: Effect[] }`. Events carry `now` and textarea snapshots `{ value, selStart, selEnd }`: `compositionstart/update/end`, `beforeinput(inputType, data)`, `input(inputType)`, `onData(d, origin)` with `origin ∈ {commit, keydownFinalize, textareaChange, other}` (xterm 5.5.0's three onData sources), `timeout(id)`, `sendFailed`, `reset`. Effects: `setTimer(id, ms)`, `cancelTimer(id)`, `clearTextarea`, `consumeModifier(m)` (a "once" modifier was applied to a composition's first letter). Takes the modifier state so a composing keyboard's first letter can be modified. Backspace unit: grapheme clusters via `Intl.Segmenter` (Decision 9). **Prefix-minimal edits:** replacing text the pane already has (a composition update, a selected-tail `insertText` / `insertReplacementText`) sends one DEL per grapheme after the common grapheme prefix, then the new suffix ("teh"→"the" = `7f 7f he`; "hello"→"help" = `7f 7f p`). `DUP_WINDOW_MS` (exported, 1500): xterm's commit `onData` that arrives within this long after `compositionend` and matches the committed text (or its xterm substring) is dropped once; later, an identical `onData` is always sent. |
| `components/micSubmit.ts` | the whole post-dictation sequence with injected clock, timer and api: deliver text (unless target is blocked at deliver time), wait `SUBMIT_MS`, re-check, send Enter. Skip Enter (text kept, toast with reason) when: timer fired > 1.5 s late, pane blocked now, pane gone, status fetch failed. |
| `hooks/socketPolicy.ts` | `onClose(code, reason, everOpened, failStreak)` → `{ retry, delayMs, message? }`, `failStreak` = consecutive closes of sockets that never opened, counting this one (0 when this one opened); 1008, 1013, 4503, 4500 and `failStreak >= NEVER_OPENED_LIMIT` (exported, 3) show a message, which contains the close reason when one was given; `CONNECT_TIMEOUT_MS`. |

Paste call sites (all through `sanitizePaste`): `term.paste` at the keyboard/Controls/Ctrl+V/image paths and
the native paste fallthrough, which gets `preventDefault` + an explicit sanitised paste.
On-screen Enter / ^C (tray) clear xterm's textarea.
Dev-only input recorder (restored from 17d6d0c behind a build flag, off in production) captures fixture traces.
The wrapped-URL link provider (`src/wrapLinks.ts`, registered in `useHerdrTerminal.ts` by 3e78f69) must be kept
by every lane that edits the hook.

### 4.2 Backend

- `routers/terminal.py`:
  - **accept first, then close** with code + reason for every refusal (the browser sees a pre-accept close as
    HTTP 403 → 1006 with no reason): 1008 origin, 1013 terminal limit, 4503 herdr server not running,
    4500 herdr binary missing.
  - `_spawn(session, rows, cols) → (pid, fd)` forks unconditionally; the binary check and the socket probe live in
    the route, before `_spawn` is called. `reset_live_count()` (tests only) zeroes the live-terminal counter.
  - Before spawning: check the herdr binary exists (else 4500, no fork) and probe the herdr socket with a
    1–2 s timeout in a thread (no socket file / stale socket / listener that never replies → 4503, client not
    spawned).
  - Exec failure in the child: `try: execvp … finally: os._exit(127)`.
  - Client exit → close 1012 (also when output is backed up).
  - Output backlog over `OUT_Q_MAX_BYTES` → close 1013 "output backlog"; the client reconnects and herdr repaints.
  - The live-terminal counter is incremented inside the guarded block (no leak when spawn fails).
  - Text frames: `resize`, `ping`, `glitch`, `input` handled; any other JSON `type` dropped; text frames with no
    `type` keep today's behaviour; binary frames are always forwarded verbatim (pasting JSON works).
- `herdr.py`: bounded socket probe `server_alive(timeout)`.
- `push.py` / `events.py`: on every (re)subscribe, reconcile — `notified ∩= currently blocked`; any blocked pane
  not in `notified` goes through `on_status`. At most one pending confirm task per pane.
- `main.py`: `/docs`, `/redoc`, `/openapi.json` disabled.
- `config.py`: probe timeout setting (`probe_timeout`); the `::1` allowlist default fixed (bracketed IPv6 Host).
  Starlette's `TrustedHostMiddleware` takes the host as `Host.split(":")[0]`, so `[::1]:8086` becomes `[`: B-7 needs
  bracket-aware host parsing in `main.py` (its own host check), not only a new default in `config.py`.
- `requirements.txt`: the full freeze of the running venv, exact pins.
- `docs/API.md`: close codes, frame types, binary input.

### 4.3 herdr server as its own unit (F1)

- `deploy/herdr.service`: **system** unit, `User=`/`Group=` the service user, clean `Environment=`
  (HOME, PATH, SHELL, LANG; nothing `HERDRWEB_*`), `ExecStart=<herdr> server`, `Restart=always`,
  `KillMode=mixed`, `TimeoutStopSec=15`, `OOMPolicy=continue`, `TasksMax=infinity`.
- `deploy/herdr-web.service`: system unit; `Wants=`/`After=herdr.service`; **no** `Requires=`, `BindsTo=`,
  `PartOf=` either way. `deploy/install.sh` installs both as system units.
- Nightly update: the host's `herdr update --handoff` timer is either proven to survive under the unit (P0-1)
  or switched to plain `herdr update` (the running server keeps going; the next server start picks up the
  new binary).
- Native resume: herdr 0.9.x restores layout and resumes agents itself on server start
  (`[session] resume_agents_on_restore`). The cutover relies on it and only relaunches where it did not.
- `deploy/cutover.sh` — runs detached (`systemd-run --unit=herdr-cutover`, its own cgroup); herdr CLI as the
  service user with HOME set, root only for systemd steps. Steps before 4 abort with nothing changed:
  1. **Manifest**: copy `session.json` + newest session snapshot; record `pane list`, `agent list`,
     `pane process-info`, `/proc/<pid>/cmdline` per pane. Abort if an agent pane has no session ref.
     `--dry-run` stops here and prints the planned commands.
  2. **Idle gate**: wait ≤ 5 min for working panes; abort if any pane is blocked or still working (`--force`).
  3. **Quit each agent, coordinator pane last**: one `send-keys ctrl+c ctrl+c` call, wait until `pane list` shows no agent (fallback SIGTERM to the manifest pid);
     re-copy `session.json`.
  4. **Stop the web unit** (this also ends the old in-cgroup server); wait until no `herdr server` runs and the
     socket refuses; check `session.json` parses with the manifest's tab count, else restore the copy.
  5. **Deploy** the staged backend + frontend (old tree kept); start web; prove the guard live (E-6).
  6. **Start herdr.service**; wait for the socket; assert cgroup and pane count.
  7. **Relaunch** (Decision 11): every agent quit in step 3 had its `agent_session` cleared, so native resume
     will not bring it back. Per manifest row, once the pane's foreground is the shell: `claude --resume <uuid>` /
     `pi --session <path>` (no prompt yet → plain `claude` / `pi`); wait ≤ 60 s for `pane list` to show the agent
     with the manifest session id; mismatch or timeout → record, don't retry blindly.
  8. **Resume**: per pane `agent wait --until idle`, then send `resume` + Enter (blocked on a dialog → skip,
     record). Coordinator last.
  9. **Verify + report**: result file; independent E-checks; restart web once (E-3); notify the owner.
  Rollback after step 4: stop herdr.service, restore old tree, start web (first attach auto-starts a server,
  which restores and resumes natively).
- Coordinator protocol: before launching, write a handoff file (step, pending checks, result-file path) and
  commit; launch the unit; confirm its cgroup is not the web unit's; end the turn. On "resume", read the handoff.

### 4.4 Tests

- Backend `pytest backend/tests`: fake herdr client scripts and a **fake herdr API unix-socket server**
  (Python) in `backend/tests/fakes/`. Autouse safety fixture fails the run if `HERDR_BIN` resolves to the real
  binary or the herdr home is the real config dir. Close codes asserted through a real uvicorn handshake
  (or the ASGI message order `websocket.accept` before `websocket.close`).
- Frontend `vitest run` (jsdom), dev dependencies pinned in H0 (lanes never touch `package-lock.json`).
  Composer regression tests replay **recorded fixture traces** (iPhone dictation, iOS predictive, Gboard,
  hardware keyboard) where recordings exist; synthetic cases state their event order explicitly.
- E2E `frontend/e2e/` (Playwright, Chromium): page served locally with mocked `/api` and a `routeWebSocket`
  mock that answers pings and enables bracketed paste (`\x1b[?2004h`); asserts the bytes sent. Runs in P1
  against the local build and in P2 against the deployed bundle. Desktop Chromium simulates iOS events; V-1 is
  the real proof.
- P1 safety rule: nobody restarts the live web service or runs the backend against the real herdr.

## 5. Phases and lanes

| Phase | Lane | Delivers | Exit check |
|---|---|---|---|
| P0 | probes (serial, scratch herdr session under a transient unit) | facts the tests depend on | P0 items recorded |
| H0 | serial | hook split (behaviour-neutral), frozen types, harnesses, fakes, safety fixture, dev deps, recorder, skeletons | H0 items + C-7 regression green on the split |
| P1 | A: `routers/terminal.py`, `herdr.py` | F1 guard, F4, F5, F13 server | verify A-* |
| P1 | B: `push.py`, `events.py`, `main.py`, `config.py`, `requirements.txt`, `deploy/*`, `docs/` (not PRD.md) | F12, F14, units | verify B-* |
| P1 | C: `input/*`, `useHerdrTerminal.ts`, `Keys.tsx`, `App.tsx` modifier indicator, CSS for it | F6, F8, F9 | verify C-* |
| P1 | D: `micSubmit.ts`, `Mic.tsx`, `socketPolicy.ts`, `terminalSocket.ts`, `App.tsx` connection indicator | F7, F13 client | verify D-* |
| P2 | E (serial) | stage, cutover, deploy, live checks | verify E-* |
| P3 | V | owner sign-off on a real phone | sign V-* |

`App.tsx` is shared by C and D in different components; C lands first, D rebases; both re-verify after merge.

## 6. Requirements (generated — `prd/acceptance.json`)
<!-- prd:items -->
- [ ] **A-1** missing herdr binary → close 4500 after accept, no fork; exec failure in _spawn → child exit 127, no Python cleanup — _fixture-test, sonnet_
- [ ] **A-2** herdr client exit → close 1012 within 2 s, also while output is backed up — _fixture-test, sonnet_
- [ ] **A-3** no socket / stale socket / silent listener → close 4503 after accept, client never executed, probe bounded and off the event loop — _fixture-test, sonnet_
- [ ] **A-4** nine spawn failures then a success still connects (live-terminal counter not leaked) — _fixture-test, sonnet_
- [ ] **A-5** multi-MB output to a slow consumer: byte-exact in order below OUT_Q_MAX_BYTES; above it close 1013 "output backlog", never silent loss — _fixture-test, sonnet_
- [ ] **A-6** text {"type":"bogus"} dropped; input works; binary bogus JSON forwarded verbatim; untyped text, resize, ping unchanged — _fixture-test, sonnet_
- [ ] **A-7** terminal limit → close 1013 with a reason after accept (and foreign Origin → 1008 after accept) — _fixture-test, sonnet_
- [ ] **B-1** pane blocked at resubscribe with no event → exactly one push (reconcile on every (re)subscribe) — _fixture-test, sonnet_
- [ ] **B-2** one pending confirm per pane: flicker inside the window → 1 push; blocked→working stays → 0; two episodes → 2 — _fixture-test, sonnet_
- [ ] **B-3** /docs, /redoc, /openapi.json → 404 — _test, sonnet_
- [ ] **B-4** requirements.txt = the full exact-pinned freeze of the running venv — _test, sonnet_
- [ ] **B-5** deploy/herdr.service + herdr-web.service parse as INI with the PRD 4.3 keys; no hard dependency; install.sh installs system units — _test, sonnet_
- [ ] **B-6** docs updated: API close codes and frames, deployment notes, no token-gate text (checklist) — _visual, sonnet_
- [ ] **B-7** bracketed IPv6 loopback Host ([::1], [::1]:port) accepted by the default allowlist; other hosts still 400 — _test, sonnet_
- [ ] **C-1** sanitizePaste drops ESC, C0 (keeps \t\n\r), DEL, C1; bracketed-paste breakout comes out inert — _test, sonnet_
- [ ] **C-2** every paste path goes through sanitizePaste (native, Ctrl+V, keyboard, Controls › Paste, image); OSC 52 over the cap ignored with a toast — _fixture-test, sonnet_
- [ ] **C-3** modifier machine once/locked/off transitions with an injected clock; hook uses it — _test, sonnet_
- [ ] **C-4** paste/report sources never modified, never consume; Alt on \r \t \x7f — _test, sonnet_
- [ ] **C-5** composing keyboard: Ctrl once + update "c" → exactly 0x03 + consumeModifier; "ca"/end "c" → no c, no 0x7f; once cleared from composition/tray — _test, sonnet_
- [ ] **C-6** regression: iPhone dictation replacement, extending and rewriting an earlier word (insertText and composition) → clean final text; hook uses the composer — _test, sonnet_
- [ ] **C-7** regression: iOS predictive, Gboard and hardware traces byte-identical — _test, sonnet_
- [ ] **C-8** (F9a) commit with trailing chars → only the remainder, before and after the commit timer — _test, sonnet_
- [ ] **C-9** (F9b) start, update "hel", onData("hel", keydownFinalize), onData("\r"), end → exactly hel\r — _test, sonnet_
- [ ] **C-10** (F9c) re-composition into an existing word does not retype it — _test, sonnet_
- [ ] **C-11** (F9d) sendFailed / socket reset → composer reset, no backspaces for undelivered text — _test, sonnet_
- [ ] **C-12** (F9e) non-tail replacement → zero bytes (insertText and insertReplacementText) + clearTextarea; tail replacement is prefix-minimal — _test, sonnet_
- [ ] **C-13** (F9f) backspace counts in grapheme clusters: ZWJ family, flag, e+U+0301 with exact bytes — _test, sonnet_
- [ ] **C-14** (F9g/h) duplicate-commit guard drops only xterm's pending commit, once; second identical composition is sent — _test, sonnet_
- [ ] **C-15** e2e: dictation (both modes, extending and rewriting), predictive, hardware, paste breakout, Ctrl on a composition and on a hardware key (indicator clears), tray Enter/^C clear the textarea, socket drop mid-composition sends no 7f after reopen — _fixture-test, sonnet_
- [ ] **D-1** Mic: Enter timer fired > 1.5 s late → no Enter, text kept, toast; Mic.tsx uses submitDictation — _test, sonnet_
- [ ] **D-2** Mic: pane blocked now / gone / status failed → no Enter; blocked at deliver time → text not sent — _test, sonnet_
- [ ] **D-3** 1008/1013/4503/4500 and N never-opened closes → visible message (with the reason), retry with backoff; terminalSocket uses the policy — _test, sonnet_
- [ ] **D-4** handshake not open within CONNECT_TIMEOUT_MS → abandoned and retried — _test, sonnet_
- [ ] **D-5** reconnect while CONNECTING replaces the pending socket — _test, sonnet_
- [ ] **D-6** e2e: mocked close 1013/4503 shows the message; a never-opening handshake leads to a second socket; Mic into a blocked pane sends nothing, into an idle pane sends text then Enter — _fixture-test, sonnet_
<!-- /prd:items -->

### 6.1 Requirement intent

**P0 (probes, recorded as facts in the ledger notes)**
- P0-1 `herdr update --handoff` under a systemd unit: does the server survive?
- P0-2 does any CLI subcommand (`pane list` …) auto-start a server when none runs? (if yes, A-3's guard
  needs `/api/health` and every CLI call to probe first)
- P0-3 quit keys for claude and pi; does a quit clear `agent_session`; pi's resume flag.
- P0-4 backspace unit in Claude Code vs bash for an emoji/ZWJ/flag.

**H0**
- H0-1 pytest harness, fake client scripts, fake API socket server, safety fixture (fails on the real binary).
- H0-2 vitest + jsdom + Playwright pinned; `npm ci` reproducible.
- H0-3 hook split is behaviour-neutral: today's e2e scenarios (dictation, predictive, hardware typing) give
  identical bytes before and after.
- H0-4 skeleton modules exist and throw "todo"; dev recorder behind a flag, absent from the production bundle.

**A (backend terminal)**
- A-1 missing herdr binary → close 4500 with reason, no fork; forced exec failure in `_spawn` → child exit
  status 127 (`waitpid`), no Python cleanup in the child.
- A-2 client exit → close 1012 within 2 s, including while output is backed up.
- A-3 no socket / stale socket / non-replying listener → close 4503 (after accept, through real uvicorn),
  client binary never executed, probe returns within its timeout.
- A-4 nine spawn failures then a success still connects (counter not leaked).
- A-5 multi-MB output to a slow consumer arrives byte-exact and in order, or the socket closes 1013 "output
  backlog" — never silent loss; backlog close happens above `OUT_Q_MAX_BYTES`.
- A-6 text frame `{"type":"bogus"}` dropped; `{"type":"input"}` works; binary frame with bytes
  `{"type":"bogus"}` forwarded verbatim; resize/ping unchanged.
- A-7 terminal limit → close 1013 with a reason, after accept.

**B (backend misc)**
- B-1 pane idle before resubscribe, blocked in `pane.list` at resubscribe, no event → exactly one push.
- B-2 blocked → working → blocked inside the confirm window → one push; blocked → working and stays → zero;
  two episodes outside the window → two.
- B-3 `/docs`, `/redoc`, `/openapi.json` → 404.
- B-4 `requirements.txt` equals the full pinned freeze (includes starlette, websockets, uvicorn, anyio, h11,
  httptools, uvloop, pydantic_core).
- B-5 unit files parse as INI: herdr unit has the keys in §4.3; web unit `Wants=`+`After=herdr.service`; no
  `Requires=`/`BindsTo=`/`PartOf=` between them; `install.sh` installs system units.
- B-6 (checklist, not locked) docs updated: API close codes and frames, deployment notes, no token-gate text.
- B-7 a bracketed IPv6 loopback Host is accepted by the default allowlist.

**C (frontend input)**
- C-1 `sanitizePaste` drops ESC, C0 (keeps `\t\n\r`), DEL, C1; the bracketed-paste breakout string comes out inert.
- C-2 every paste path calls it (keyboard, Controls › Paste, Ctrl+V, image path, native fallthrough); OSC 52
  over `OSC52_MAX_BYTES` decoded is ignored with a rate-limited toast.
- C-3 modifier machine: once/locked/off transitions per §4.1, injected clock.
- C-4 paste and report sources never modified and never consume, including a one-char paste with bracketed
  mode off; Alt on `\r`, `\t`, `\x7f` works.
- C-5 composing keyboard: Ctrl once + update "c" → exactly `0x03`; later update "ca" / end "c" → no "c", no
  `0x7f`; indicator clears when "once" is consumed; tray keys consume "once".
- C-6 regression: iPhone dictation replacement trace → clean final text.
- C-7 regression: predictive composition, Gboard and hardware traces unchanged.
- C-8 (F9a) commit with trailing chars → only the remainder, both before and after the 150 ms timer.
- C-9 (F9b) start, update "hel", onData("hel", keydownFinalize), onData("\r"), end → exactly `hel\r`.
- C-10 (F9c) re-composition into an existing word does not retype the word.
- C-11 (F9d) `send` false → composer resets; next correction emits no backspaces for undelivered text;
  socket reset resets it.
- C-12 (F9e) non-tail replacement: zero bytes for both `insertText` and `insertReplacementText`, then a
  textarea reset.
- C-13 (F9f) backspace counts per the P0-4 unit, with emoji/ZWJ/flag/combining cases stating exact bytes.
- C-14 (F9g/h) tray Enter/^C clear the textarea; a second identical commit with its own composition events
  inside the window is sent; only an `onData` with no intervening composition is dropped, once.
- C-15 e2e: dictation, predictive, hardware, paste-breakout (bracketed), Ctrl-on-composition scenarios.

**D (frontend Mic + socket)**
- D-1 Mic: timer fired > 1.5 s late → no Enter, text kept, toast.
- D-2 Mic: pane blocked now / gone / status fetch failed → no Enter; target blocked at deliver time → text not sent.
- D-3 1008 / 1013 / 4503 / 4500 and "N closes without opening" → visible message, retry with backoff.
- D-4 handshake not open within `CONNECT_TIMEOUT_MS` → abandoned and retried.
- D-5 reconnect while CONNECTING replaces the pending socket.
- D-6 e2e: mocked close 1013/4503 shows the message; a never-opening handshake leads to a second socket.

**E (ops, live)**
- E-1 cutover `--dry-run` manifest lists every pane with agent, session ref and planned action.
- E-2 `herdr status server --json` + `/proc/<pid>/cgroup` → server in `herdr.service`.
- E-3 restart web → herdr server PID and every pane child PID unchanged; web cgroup holds only uvicorn and
  `herdr --session` clients.
- E-4 per manifest row: same agent and `agent_session.value` in `pane list`; `/proc` cmdline has the id; the
  Claude transcript gained a user message `resume` after the cutover time.
- E-5 served bundle hash = build output; deployed backend tree = repo.
- E-6 (in the cutover window) server down + new backend up → attach gets 4503 and no `herdr server` appears.
- E-7 nightly update path per P0-1 (proven or switched).

**V (owner, real phone)** — V-1 dictation/predictive clean; V-2 Ctrl once + double-tap lock; V-3 Mic
late-Enter skip toast (lock the phone during the countdown).

## 7. Risks

| Risk | Likelihood | Mitigation | Detecting check |
|---|---|---|---|
| Cutover loses an agent's context | medium | idle gate, clean quit, native resume + reconcile, manifest copies, rollback | E-1, E-4 |
| Coordinator inside a pane the cutover stops | certain | detached unit, handoff file, coordinator last | E-4 |
| Browser reconnect re-spawns a server in the web cgroup during cutover | high if unhandled | stop web first; guard deployed before herdr starts | E-6, E-2 |
| Nightly `--handoff` update kills the unit's cgroup | unknown | P0-1, then prove or switch | E-7 |
| A CLI call auto-starts a server | unknown | P0-2; probe-first on every path | E-2 after 24 h |
| Hook split regresses typing | medium | behaviour-neutral split gated by H0-3 | H0-3, C-15, V-1 |
| Grapheme vs code-point backspace mismatch | medium | P0-4 decides; smaller count | C-13, V-1 |
| A pane can still poison the clipboard for pastes outside the terminal | accepted | Decision 4 | — |

## 8. Decisions

| # | Decision | Chosen | Why | Date |
|---|---|---|---|---|
| 1 | Sticky Ctrl/Alt | one-shot; double tap locks | owner | 2026-11-15 |
| 2 | Mic Enter late or into a question | skip Enter, keep text, toast | owner | 2026-11-15 |
| 3 | Cutover | record → quit each → switch → relaunch where needed → send "resume" | owner | 2026-11-15 |
| 4 | OSC 52 | keep (agents copy via OSC 52 without a gesture), cap size; paste sanitising closes the exploit | gesture gating breaks agent copy | 2026-11-15 |
| 5 | Units | both system units, same manager, no hard dependency | ordering only works within one manager | 2026-11-15 |
| 6 | Builders | Opus per lane; a reviewer checked the PRD | owner | 2026-11-15 |
| 7 | Backlog | close 1013 above a byte cap instead of pause/resume | no stall, no EOF trap | 2026-11-15 |
| 8 | Mic "status changed" | narrowed to blocked / gone / fetch failed | most dictations finish a turn within 10 s | 2026-11-15 |

| 9 | Backspace unit (C-13) | grapheme clusters (`Intl.Segmenter`) | P0-4: the Claude prompt box deletes one grapheme per DEL; bash deletes code points (UTF-8) or bytes (C locale) — grapheme is the smaller count | 2026-11-15 |
| 10 | Nightly update (E-7) | `herdr update` without `--handoff` | P0-1: a live handoff ends a `KillMode=mixed` unit and every pane; with `KillMode=process` the new server is orphaned in a dead unit | 2026-11-15 |
| 11 | Cutover relaunch | relaunch every quit agent from the manifest (`claude --resume <uuid>`, `pi --session <path>`); an agent with no prompt yet is relaunched fresh | P0-3: a clean quit clears `agent_session`, so native resume will not fire for quit panes; quit = one `send-keys ctrl+c ctrl+c` call (≈0.8 s claude, ≈0.4 s pi) | 2026-11-15 |
| 12 | herdr.service locale | keep today's `LANG=C`, `LC_CTYPE=C.UTF-8` | behaviour-neutral for every pane | 2026-11-15 |
| 13 | Guard scope (A-3) | probe only before spawning a client | P0-2: no CLI verb (`pane list`, `api snapshot`, `status`) auto-starts a server; only a client attach does | 2026-11-15 |

## 9. Open decisions

None. P0 results are recorded as Decisions 9–13.
