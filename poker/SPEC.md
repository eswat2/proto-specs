# Planning Poker Tool — Product & Engineering Specification

This document specifies a complete web application: a free, no-signup
"planning poker" estimation tool for agile teams. It is written to be
**implementation-derivable** — a competent engineer (human or AI) should be
able to build a functionally and architecturally faithful version of the
app from this document alone, without ever seeing a reference
implementation. Where behavior matters, it is specified precisely (exact
status codes, exact field names, exact limits). Where only visual intent
matters, it is described qualitatively.

## 1. Product scope

**Goal:** create or join a room via a shareable link, no signup. Vote on a
story using a card deck, reveal everyone's vote simultaneously, start a new
round. Real-time sync between everyone in the room.

**Explicit non-goals (do not build these):**
- User accounts / sign-in / authentication of any kind.
- A "host" or moderator role, or any permission distinction between
  participants — every write action is callable by anyone in the room.
- Persistence of rooms or history beyond a short inactivity window (rooms
  are ephemeral, see §6).
- Custom/configurable card decks, timers, or round history.
- Any decorative "flair" (animations beyond a simple card-reveal
  transition, reactions, sound, etc).
- Integrations with third-party issue trackers or video conferencing.
- A server-side authorization model — see §3, participant identity is
  self-asserted and not verified against room membership for most actions.

## 2. Required platform constraints

These are hard constraints on *how* the system must be built, not just
what it must do — a correct implementation that ignores them (e.g. using
WebSockets, or a client-side router) does not satisfy this spec:

1. **Backend runs as small, independent serverless functions**, not behind
   a monolithic web framework/router. Each API route is its own
   self-contained handler.
2. **No inbound WebSocket support may be assumed.** The deployment target
   is a FaaS platform whose functions cannot accept a WebSocket upgrade.
   Real-time server→client push must be built on a mechanism compatible
   with a plain streamed HTTP response (e.g. Server-Sent Events / a
   long-lived `text/event-stream` response), not a bidirectional socket.
3. **The real-time-push-capable function must run in a runtime with raw
   socket access** (i.e. not a restricted edge/isolate runtime), because
   holding a live subscription to the backing store's pub/sub feature
   requires a persistent TCP connection.
4. **No third-party realtime SaaS vendor** (managed pub/sub or "realtime
   as a service" subscription products). All fan-out must run against the
   same backing data store already used for state, not a second vendor
   relationship.
5. **A single backing store serving two roles**: ordinary key/value
   read-modify-write, and publish/subscribe for fan-out. It must offer (or
   be paired with) a way to perform an **atomic conditional write**
   (write-only-if-unchanged-since-read) — plain `WATCH`/transaction
   semantics scoped to one persistent connection are not sufficient if the
   implementation shares one connection across concurrent requests (see
   §7 for why this matters and what it must guarantee).
6. **No server-side sessions, cookies, or auth tokens.** Every client
   carries its own opaque, self-generated identifier on every request.
7. **Zero frontend build step.** The client must run exactly as authored
   in a browser — no bundler, transpiler, or compilation step. If the
   implementation uses component encapsulation, it must be achieved with
   browser-native mechanisms only (e.g. native Custom Elements / Shadow
   DOM), not a framework requiring a build pipeline.
8. **No client-side router.** Navigating from the landing page into a room
   (and back) is a real full-page navigation (URL/location change), not
   push-state SPA routing.
9. **One page component may legitimately own most of the room UI** rather
   than being decomposed into many small subcomponents — this is a
   deliberate simplicity choice for an app this small, not an oversight.
   Don't over-decompose the frontend.

## 3. Data model

A **room** is the sole unit of persisted state. One room instance:

```jsonc
{
  "id": "<room id, see §6.1>",
  "topic": "",                 // free-text string, "" by default
  "deck": [1, 2, 3, 5, 8, 13, 21, "♠", "?"],
  "revealed": false,
  "participants": {
    "<participantId>": { "name": "Alice", "vote": null }
  },
  "updatedAt": 1732300000000   // ms epoch, set on every persisted write
}
```

Field rules:
- `deck` is fixed for the life of a room: `[1, 2, 3, 5, 8, 13, 21, "♠", "?"]`
  — Fibonacci-ish numbers through 21, plus two special non-numeric cards:
  `"♠"` (a wildcard / "no estimate applies" card) and `"?"` (a "need more
  information" / pass card). Both are excluded from any numeric average
  (see §8).
- `participantId` is a client-generated opaque identifier (e.g. a v4 UUID),
  never validated or authenticated by the server. It is the map key under
  `participants`.
- `vote` is `null` until the participant casts one, then holds whatever
  value they last cast — the server does **not** validate that the cast
  value is actually a member of `deck`.
- **Votes are never redacted server-side.** The full state, including all
  cast vote values, is present in every read and every broadcast at all
  times, whether or not `revealed` is `true`. "Hidden until revealed" is
  *purely* a client rendering rule — there is no member of this system
  that withholds vote values from a request or a stream message. (This is
  a deliberate simplicity trade-off: there are no accounts to protect a
  vote's confidentiality from in the first place.)
- `updatedAt` only changes on a write that actually happens — see §7's
  no-op rule.

There is no other persisted entity — no user table, no room list/index
beyond what's needed to detect id collisions at creation time (§6.1), no
audit log.

## 4. API contract

All endpoints are JSON in / JSON out (except the stream endpoint, §5),
mounted under a common `/api` prefix. Every endpoint below must reject any
other HTTP method with **405** and an empty body.

Error response bodies are always `{ error: <string> }`. The exact wording
of that string is **not** load-bearing for this spec — only the shape,
the status code, and (for 429 responses) the `Retry-After` header matter.
Don't spend implementation effort matching specific phrasing.

| Method | Path | Body | Success | Purpose |
|---|---|---|---|---|
| POST | `/api/rooms` | `{ name }` | 201 `{ roomId, participantId }` | Create a room |
| GET | `/api/rooms/:roomId` | — | 200 `{ room }` | Fetch current state |
| POST | `/api/rooms/:roomId/join` | `{ name }` | 200 `{ participantId }` | Join a room |
| POST | `/api/rooms/:roomId/vote` | `{ participantId, value }` | 200 `{ ok: true }` | Cast/change a vote |
| POST | `/api/rooms/:roomId/reveal` | `{ participantId }` | 200 `{ ok: true }` | Reveal votes |
| POST | `/api/rooms/:roomId/new-round` | `{ participantId, topic? }` | 200 `{ ok: true }` | Clear votes, hide again, optionally retitle |
| POST | `/api/rooms/:roomId/topic` | `{ participantId, topic }` | 200 `{ ok: true }` | Retitle without clearing votes |
| GET | `/api/rooms/:roomId/stream` | — | `text/event-stream` | Live state feed, §5 |

Per-endpoint validation and error behavior:

- **Create** (`POST /api/rooms`): 400 `{ error }` if `name` is absent, not
  a string, or — after trimming leading/trailing whitespace — reduces to
  an empty string. This is an exact predicate, not a category: a
  whitespace-only name (e.g. `"   "`) must be rejected too, not silently
  accepted and stored as empty. Otherwise use the trimmed value,
  truncated to 40 characters, before storing. Always succeeds after that
  (201) — creates a fresh room owned by nobody in particular, with this
  caller as its first participant.
- **Get** (`GET /api/rooms/:roomId`): 404 `{ error }` if the room doesn't
  exist (including if it expired, §6.2). No other failure mode.
- **Join** (`POST .../join`): 400 under the exact same name predicate as
  create (absent, not a string, or empty after trimming — same
  trim+truncate-to-40 rule). 404 if the room doesn't exist. On
  success, mints a **new** `participantId` and adds a fresh participant
  with `vote: null` — joining never re-uses or looks up an existing
  identity, even if the same browser already has a stored id for this
  room (that's a client-side concern, not enforced server-side).
- **Vote** (`POST .../vote`): 400 if `participantId` missing. `value` may
  be any JSON value the client sends — not validated against `deck`. 404
  `{ error }` — deliberately ambiguous between "room not found" and
  "participant not found" (same message/status for both, so a client
  can't distinguish a bad room id from a bad participant id) — if the room
  doesn't exist *or* `participantId` isn't a known member of it.
  Re-submitting the exact same value the participant already has recorded
  must succeed (200 `{ ok: true }`) but must count as a **no-op**: no
  underlying state write, no broadcast to other clients (§7), and no
  refresh of the room's inactivity TTL (§6.2).
- **Reveal** (`POST .../reveal`): 400 if `participantId` missing. 404 if
  the room doesn't exist. **Does not check that `participantId` is an
  actual member of the room** — any string is accepted as long as it's
  present (it's used only as a rate-limit key, see §9). Sets `revealed` to
  `true` **unconditionally** — calling it again when already revealed is
  *not* a no-op: it still counts as a write, still bumps `updatedAt`, and
  still broadcasts (unlike vote's no-op rule above — reveal/new-round/
  topic never suppress a write, only vote does).
- **New round** (`POST .../new-round`): 400 if `participantId` missing
  (same non-membership-checked rule as reveal). 404 if room doesn't exist.
  Sets `revealed` to `false` and every participant's `vote` back to `null`,
  unconditionally (always a write, per the reveal note above). `topic` in
  the body is optional — if present (and a string), replace the room's
  topic; if omitted, leave the existing topic untouched.
- **Topic** (`POST .../topic`): 400 if `topic` is missing/not a string,
  400 if `participantId` missing (not membership-checked). 404 if room
  doesn't exist. Truncate to 200 characters before storing. Always a
  write, even if identical to the current topic.

## 5. Real-time sync

There is exactly one source of truth push mechanism: `GET
/api/rooms/:roomId/stream`, one long-lived HTTP connection per browser tab
per room, using the streaming mechanism required by §2.2. Behavior:

1. If the room doesn't exist, respond 404 `{ error }` and end — same
   shape as the plain GET.
2. Otherwise, begin the streamed response and **explicitly flush its
   headers to the client immediately**, before any further asynchronous
   work (including sending the first event below). Don't rely on an
   implicit flush on first write — some server runtimes/intermediary
   proxies don't consider the connection genuinely open until headers are
   flushed on their own, independent of body data.
3. Immediately after that, before doing anything else, send the **entire
   current room state** as the first event. A client must never be left
   waiting for a first mutation to see any data.
4. From then on, **every successful, non-no-op mutation to this room from
   any client, via any of the API endpoints in §4**, results in the full
   new room JSON being delivered as a new event to every open stream
   subscribed to that room — not a diff, not a partial patch, the entire
   object, every time. This is a deliberate simplicity trade-off given
   rooms are small (a handful of participants); there is no incremental
   merge protocol anywhere in the system.
5. The connection must periodically send an inert keep-alive
   (comment/no-op frame) on the order of every 20–30 seconds, so
   intermediary proxies/load balancers don't time out an otherwise-idle
   stream.
6. The connection must tolerate the underlying client transport
   reconnecting after a drop (e.g. the browser's native `EventSource`,
   which reconnects automatically on error). Because step 3 always
   replays full current state on (re)connect, a client that reconnects
   after missing some messages is never stuck showing stale data — it
   just gets caught up on connect.
7. Server-side connection cleanup (releasing whatever subscribed to the
   backing store's pub/sub channel) must be wired up so that a client
   disconnecting **at any point, including mid-setup before the
   subscription finishes establishing**, cannot leak that
   subscription/connection resource. A client that disconnects
   pathologically fast (setup a listener for the disconnect event before
   starting any `await`-able subscribe step, not after) must still result
   in cleanup.
8. This endpoint has a materially longer allowed execution duration than
   the other endpoints, since it's meant to stay open for the life of a
   tab's visit to a room (not a fixed short request/response).

## 6. Room lifecycle

### 6.1 Creation & id format

A room id is a short, memorable, URL-safe slug: two lowercase words joined
by hyphens plus a 3-digit numeric suffix, e.g. `brave-otter-482` — pattern
`^[a-z]+-[a-z]+-\d{3}$`. Generate from two reasonably-sized fixed word
lists (adjectives, nouns — a few dozen entries each is enough) plus a
random number in the inclusive range 100–999 (i.e. always exactly three
digits, never leading-zero-padded).

On creation, check the candidate id against existing rooms and retry a
handful of times (e.g. up to 5) on collision before giving up and using it
anyway — full uniqueness isn't guaranteed, but the word-list size makes
collisions rare enough that this is acceptable for an ephemeral,
non-critical room id. **The collision check and the write must happen as
a single atomic conditional operation** — the same write-only-if-changed
mechanism required by §7, used here in its write-only-if-absent form —
not a separate existence check followed by an unconditional write. Two
creation requests that race on the same generated id must not be able to
silently clobber one another; this is the same class of lost-update bug
§7 exists to prevent, just at creation time instead of mutation time.

### 6.2 Expiry

Rooms are **not** explicitly deleted by any user action (there's no owner
to do so). Instead, a room's backing storage entry carries a
inactivity-based expiration of **4 hours**, refreshed to a full 4 hours on
every mutation **that actually performs a write** — per §4's no-op rule,
re-casting an already-current vote value does *not* refresh the timer.
Once expired, the room is simply gone: subsequent `GET`/stream/mutation
calls for that id behave exactly as if it never existed (404).

## 7. Write concurrency (must not lose updates)

Multiple participants can mutate the same room at effectively the same
instant (e.g. two people voting within the same tick). A naive
read-modify-write (read current state, apply change in application code,
write the whole thing back) can silently lose one of the two updates if
they interleave: both reads see the same prior state, both writes
proceed, and the second write clobbers the first with no error and no
trace of the lost change.

**Required guarantee:** under any level of concurrent write contention on
one room, every accepted mutation must eventually be reflected in the
room's state — none may be silently dropped. Achieve this with an
optimistic concurrency loop:

1. Read the current raw state (keep the raw form for the compare step,
   not just the parsed object).
2. Apply the requested change in application code to produce a new state,
   bump `updatedAt`.
3. Attempt an **atomic conditional write**: replace the stored value only
   if it is still byte-identical to what was read in step 1 (this must be
   evaluated and executed as a single atomic operation server-side by the
   backing store, e.g. a server-side script, not as separate
   read-then-write calls from the application). If the value had already
   changed (someone else wrote in between), the conditional write must be
   rejected/no-op — not silently override the interim value.
4. On rejection, re-read fresh state and retry the whole cycle. Use a
   small jittered/randomized backoff that increases somewhat with attempt
   count, so that many competing retries don't keep re-colliding in
   lockstep.
5. Cap retries at a reasonable bound (e.g. 10 attempts) and treat
   exhausting them as a hard failure (an app bug or pathological
   contention), not a silently-swallowed error.
6. Only on a **successful** conditional write does the mutation publish
   the new state to real-time subscribers (§5) — a rejected/retried
   attempt must not publish an intermediate value that never actually
   became the stored state.

A transaction/optimistic-lock mechanism that is scoped to a single
persistent connection (e.g. classic `WATCH`/`MULTI`) is **not** sufficient
if the implementation shares one backing-store connection across
concurrent, logically-unrelated requests — two unrelated in-flight
mutations sharing that connection could corrupt each other's watched
state. The conditional-write step must be atomic regardless of connection
sharing (e.g. implemented as a single server-side script/procedure the
store executes atomically, not as multiple round-trips from the app).

**Acceptance test this must pass:** 25 participants joining the same new
room in the same instant, followed by all 25 casting distinct votes in the
same instant — afterward, all 25 participants must exist and all 25 votes
must be present with none lost or overwritten.

## 8. Frontend behavior

### 8.1 Routing / shell

On load, read the current path. An empty/root path renders the landing
view. Any non-empty path is treated as a room id and renders the room view
for that id (no matching against a route table — whatever's in the path
segment is passed straight through as the room id to fetch/subscribe).

### 8.2 Landing view

A single screen with two independent forms:

- **Create**: a name field (required) and a submit control. On submit,
  validate a name is present (inline error if not, e.g. "Enter your name
  first."); call the create endpoint; on success, remember the returned
  `participantId` locally scoped to the new room's id, remember the name
  entered (see §8.6), and navigate (full page load) to `/{roomId}`. On
  failure, show a generic inline error.
- **Join**: a name field and a room-code field (both required, distinct
  inline errors for each missing case, e.g. "Enter a room code."). On
  submit, call the join endpoint for that room id; on a 404 specifically,
  show a distinct "room not found" message rather than the generic
  failure message; on success, remember the participant id + name as
  above and navigate to `/{code}`.
- The name field(s) should pre-fill from the remembered name (§8.6) so a
  returning user doesn't retype it every time.

### 8.3 Room view — layout & elements

- A header showing the room's id/code and a "copy invite link" control
  that copies the current page URL to the clipboard and gives transient
  feedback (e.g. its label changes to "Copied!" for roughly a second and
  a half before reverting).
- An editable single-line topic field, showing the room's current topic
  (placeholder text like "What are we estimating?" when empty).
- A participant list (see §8.4).
- A stats area (see §8.5).
- Two controls: **Reveal votes** and **New round**, both callable by
  anyone at any time (no permission gating in the UI, matching §4).
  Reveal's control must reflect already-revealed state by disabling
  itself and relabeling (e.g. "Votes revealed") so it reads as inert
  rather than clickable-but-ignored; New round has no such disabled state.
- A card deck: one clickable control per value in the room's `deck`,
  rendered once from the deck present in the first state received (the
  deck doesn't change during a room's life, so there's no need to rebuild
  these controls on every update). The two special cards need distinct
  presentation: the wildcard gets a non-numeric glyph/icon (not literally
  the string) with a tooltip/label conveying "wildcard", and the pass
  card gets a `?`-style glyph with a tooltip/label conveying "need more
  info" / pass. Clicking a card immediately marks it visually selected
  and casts that value as this participant's vote; the selection state
  must be re-derived from server state on every incoming update (not just
  set-and-forget on click), so a vote change reflected from elsewhere
  (e.g. another tab for the same participant) is picked up here too.
- A **join gate**: if this browser has no remembered participant id for
  this specific room (e.g. someone opening a shared link for the first
  time), block interaction with a modal/overlay prompting for a name
  before they can do anything else; the rest of the room (participant
  list, topic, etc.) may already be visible/read-only behind it since the
  live stream doesn't require having joined. Submitting the name calls
  join, stores the returned participant id, and dismisses the overlay.
  **Care point:** whatever mechanism toggles this overlay's visibility
  must actually prevent both rendering and interaction when hidden — a
  toggle that only sets a semantic "hidden" flag without also forcing the
  element to not render is insufficient if any of that element's own CSS
  rules unconditionally set a `display` value, since author style rules
  win over user-agent default hidden behavior at equal specificity. Any
  hide/show toggle in this UI needs an explicit rule making the hidden
  state actually invisible.
- A **fatal error state**: if the very first attempt to open the live
  stream for this room fails (e.g. the room doesn't exist / the link is
  invalid) *before any state has ever been successfully received*, replace
  the room view entirely with a short explanatory message and a link back
  to the landing page. If state has already been received at least once
  and the stream later has a transient error, do **not** enter this fatal
  state — the transport is expected to recover on its own (§5.6).

### 8.4 Participant list rendering

One row per participant, in the order the state object provides them,
each showing:
- An avatar: the first letter of their name (uppercased), on a
  background color that is **deterministic per name** (the same name
  always produces the same color for every viewer, every session — derive
  it via some stable hash of the name mapped into a color space; exact
  hue/formula is an implementation's own choice, the requirement is
  determinism + reasonable visual variety across different names, not a
  specific palette).
- Their display name, with a distinguishing suffix (e.g. "(you)") appended
  only to the row matching this browser's own remembered participant id
  for this room — every other viewer sees that same person's row without
  the suffix.
- A vote-status indicator with three distinct visual states:
  - No vote yet: render nothing in the indicator — no glyph, no
    placeholder character, just empty space occupying the same layout
    footprint as the other two states.
  - Voted, not yet revealed: a "someone voted" indicator (e.g. a
    checkmark) that does **not** show the value — even for the viewer's
    own row, even though the full value is technically already present in
    the state object client-side; this is a deliberate render-only
    convention, not a data-hiding mechanism (§3).
  - Revealed: show the actual value for everyone who voted (a brief
    reveal-transition animation is a nice touch, not required), and a
    placeholder (e.g. an em dash) for anyone who didn't vote at all.

### 8.5 Stats

Visible only when `revealed` is true **and** at least one revealed vote is
numeric. Compute the arithmetic mean of only the numeric vote values
(explicitly excluding the two special non-numeric cards and anyone who
didn't vote), formatted to one decimal place, labeled clearly as an
average. Hidden in every other case (not revealed, or revealed but zero
numeric votes).

### 8.6 Client-side persistence (no server session)

Two independent pieces of state persisted in the browser (e.g.
`localStorage`), never sent anywhere except as explicit request fields:
- The participant id for a given room, keyed by that room's id, so a page
  refresh doesn't drop the user from the room or mint a duplicate
  identity for them.
- The most recently used display name, independent of any room, so it can
  pre-fill name fields on future visits (both landing forms and the join
  overlay).

### 8.7 Live update handling

Every message from the live stream (§5) fully replaces the client's
notion of room state and re-renders from it — there is no local
merge/patch logic anywhere in the client. The one deliberate exception:
while the topic field currently has keyboard focus (the user is mid-edit),
an incoming update must **not** overwrite the field's displayed value out
from under them — only sync the displayed value from incoming state when
the field isn't currently focused. Topic edits commit **exactly once per
edit**: wire a single commit trigger that fires when the field loses
focus with a changed value (a plain `change` event already covers this,
since it fires on blur when the value differs from before) — not on
every keystroke, and not from more than one listener on the same field.
Wiring both a `change` listener and a separate `blur` listener on the
same input double-submits, since both fire on one blur-after-edit; pick
one trigger, not both.

### 8.8 Visual design intent

A single dark theme (not user-toggleable) built from one small set of
shared design tokens (a background color, one or two panel/surface
colors, a border color, a primary and a dimmed text color, a primary
accent, a secondary accent used for revealed-value emphasis, an error/
danger color, a shared corner-radius value, and font stacks for
body/monospace text), applied consistently across the landing view and
room view. If component encapsulation is used (§2.7), it should consume
one shared token source rather than duplicating color values per
component, while still not leaking styles across component boundaries.
Visual character: deep near-black background with a soft radial
accent-colored glow near the top, cards/panels as slightly-raised surfaces
with subtle borders, a vivid accent color for primary actions and the
currently-selected card, generous rounded corners.

## 9. Abuse mitigation (rate limiting)

The system is fully permissionless (§2, §4) — anyone can call any
mutation for any room they know the id of. To bound abuse without
introducing accounts, every mutation endpoint enforces one or more
request-rate limits, evaluated *before* performing the underlying action.

**Ordering relative to request validation:** §4's request-body validation
always runs *before* rate-limit evaluation, for every mutation endpoint,
with no exceptions. A request that fails §4's validation (missing name,
missing participantId, etc.) must return its 400 without touching any
rate-limit counter at all — malformed requests are free; only requests
that pass validation can consume a limit.

| Endpoint | Limit key(s) | Threshold |
|---|---|---|
| create | client IP | 10 per 10 minutes |
| join | client IP + room id | 20 per 10 minutes |
| vote | participantId + room id | 30 per 10 seconds |
| vote | client IP + room id | 100 per 10 seconds |
| reveal | participantId + room id | 10 per 10 seconds |
| reveal | client IP + room id | 30 per 10 seconds |
| new-round | participantId + room id | 10 per 10 seconds |
| new-round | client IP + room id | 30 per 10 seconds |
| topic | participantId + room id | 10 per 10 seconds |
| topic | client IP + room id | 30 per 10 seconds |

Rules:
- Where an endpoint has two limits (a participant-scoped one and an
  IP-scoped one), **both** must hold; check them in a fixed order and stop
  at the first one that's already over its threshold (don't touch/
  increment counters for checks after the first failure).
- The participant-scoped limit alone is a weak defense — `participantId`
  is self-issued and never verified, and (per §4) most mutation endpoints
  don't even check it belongs to the room, so a client can trivially mint
  a fresh id per request to dodge a participant-scoped-only limit. The
  IP-scoped companion limit is what actually bounds a determined abuser,
  and is deliberately set generously above the participant limit — this
  is a planning-poker tool used by real teams that may share one office
  network/VPN egress IP, so one IP legitimately represents many
  simultaneous real users.
- On exceeding any applicable limit: respond **429** with a `Retry-After`
  header (seconds until the limit window resets) and a JSON error body,
  and do not perform the requested mutation at all (no partial effect).
- A simple fixed-window counter (increment-and-check, with the window's
  expiry set only on that window's first request) is sufficient — it
  doesn't need to be a sliding window or token bucket. A benign edge case
  (a counter briefly having no expiry set right at the moment a new
  window starts) is acceptable; the worst case is self-correcting within
  one window and is not a meaningful abuse vector.
- `create` and `join` have no `participantId` yet to key on (they're what
  mint one) — IP-only (create) or IP+room (join) is sufficient for those
  two.
- The live stream endpoint (§5) is explicitly **not** rate-limited by this
  scheme — opening many concurrent long-lived stream connections is a
  different kind of resource concern (connection exhaustion, not mutation
  spam) and is out of scope for this mechanism.

## 10. Acceptance checklist

Each item below **must exist as an automated, independently re-runnable
test against a real backing store** — not mocked, and not a one-time
manual script run once and discarded. Roughly one test file per concern
(lifecycle, concurrency, streaming, rate limiting) mirrors the grouping
below; that grouping is a reasonable way to split them, not a requirement
to use exactly four files. Manual verification across two real browser
sessions is a useful supplement for the UI-facing behavior in §8, not a
substitute for automated coverage of the items below:

- [ ] Creating a room returns a slug-shaped id and a working participant
      id; the room has the default deck and starts unrevealed.
- [ ] Full lifecycle: create → second participant joins → topic set → both
      vote → reveal shows both values → new round clears votes, keeps
      unrevealed, and can retitle in the same call.
- [ ] Non-numeric vote values (the two special cards) round-trip
      faithfully and are excluded from the average.
- [ ] Voting as an unknown participant id is rejected, performs no write,
      and triggers no broadcast.
- [ ] Re-casting an already-current vote value succeeds but performs no
      write and triggers no broadcast (distinguish this from the rejection
      case above — this one is *not* an error).
- [ ] Operations against a nonexistent/expired room id fail predictably
      (404-equivalent) rather than throwing/crashing.
- [ ] 25 participants joining and then voting at the exact same instant
      all land with none lost (§7's concurrency guarantee).
- [ ] Connecting to the live stream immediately yields current state, and
      a mutation made after connecting is pushed down that same open
      connection without needing a reconnect.
- [ ] A rate limit under its threshold allows requests; the same key over
      threshold is rejected with retry guidance; independent keys are
      tracked independently of each other.
