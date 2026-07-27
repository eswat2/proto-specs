# Team Retro Board Tool — Product & Engineering Specification

This document specifies a complete web application: a free, no-signup team
retrospective ("retro") board tool. It is written to be
**implementation-derivable** — a competent engineer (human or AI) should be
able to build a functionally and architecturally faithful version of the
app from this document alone, without ever seeing a reference
implementation. Where behavior matters, it is specified precisely (exact
status codes, exact field names, exact validation predicates, exact
limits). Where only visual intent matters, it is described qualitatively,
and this document says so explicitly rather than leaving it ambiguous.

## 1. Product scope

**Goal:** create or join a board via a shareable link, no signup. Add
short text cards to one of three fixed categories, keep cards hidden from
everyone but their author until a reveal action is taken, then discuss
with everything visible. Real-time sync between everyone on the board.

**Explicit non-goals (do not build these):**
- User accounts / sign-in / authentication of any kind.
- A "host" or moderator role, or any permission distinction between
  participants for most actions — see §4 for the one deliberate exception
  (card deletion has a per-item owner; everything else is callable by
  anyone on the board).
- Upvotes, likes, comments, or any card grouping/merging feature.
- Editable or configurable columns/categories — exactly three, fixed for
  the life of the application, not just one board.
- A board title or topic field. A board's only identity is its id/slug.
- A "new round" / reset action that clears cards and hides them again for
  reuse. A retro board maps to one whole meeting, not a repeating unit —
  there is no natural reset-and-reuse action, and a fresh board is cheap
  and already ephemeral (§6.2), so nothing is gained by supporting reuse
  of one board across multiple sessions.
- Persistence of boards or their history beyond a short inactivity window
  (boards are ephemeral, see §6.2).
- Any decorative "flair" beyond a simple card-appear transition.
- Integrations with third-party issue trackers or video conferencing.
- A server-side authorization model beyond the one ownership check noted
  above — participant identity is otherwise self-asserted and not verified
  against board membership (see §4).

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
   carries its own opaque identifier on every request (see §3 for exactly
   where that identifier comes from — it is not client-generated).
7. **Zero frontend build step.** The client must run exactly as authored
   in a browser — no bundler, transpiler, or compilation step. If the
   implementation uses component encapsulation, it must be achieved with
   browser-native mechanisms only (e.g. native Custom Elements / Shadow
   DOM), not a framework requiring a build pipeline.
8. **No client-side router.** Navigating from the landing page into a
   board (and back) is a real full-page navigation (URL/location change),
   not push-state SPA routing.
9. **One page component may legitimately own most of the board UI** rather
   than being decomposed into many small subcomponents — this is a
   deliberate simplicity choice for an app this small, not an oversight.
   Don't over-decompose the frontend.

## 3. Data model

A **board** is the sole unit of persisted state. One board instance:

```jsonc
{
  "id": "<board id, see §6.1>",
  "columns": [
    { "id": "went-well", "title": "Went well" },
    { "id": "to-improve", "title": "To improve" },
    { "id": "action-items", "title": "Action items" }
  ],
  "cards": {
    "<cardId>": {
      "id": "<cardId>",
      "columnId": "went-well",
      "authorId": "<participantId>",
      "text": "Great sprint",
      "createdAt": 1732300000000
    }
  },
  "participants": {
    "<participantId>": { "name": "Richard" }
  },
  "revealed": false,
  "updatedAt": 1732300000000
}
```

Field rules:
- `columns` is fixed application-wide (not per-board configurable) at
  exactly the three shown above, in that order: `went-well` ("Went well"),
  `to-improve` ("To improve"), `action-items` ("Action items"). It is
  embedded into every board's state at creation time (not hardcoded
  separately client-side) so the server remains the single source of
  truth for what columns exist, but its value never differs between
  boards or changes after creation.
- `participantId` is an **opaque identifier minted by the server**, never
  supplied by the client. Every board-creation call and every join call
  mints a brand-new one (e.g. a v4 UUID) server-side and returns it to the
  caller; there is no API path by which a client asserts its own
  `participantId` value into existence. The client's only role is to
  *remember* the value it's given (§8.6) and echo it back on later
  requests — it is opaque and never authenticated against anything
  besides the ownership check in §4 (delete-card).
- A participant has only a `name` — there is no vote, status, or other
  per-participant field the way a planning-poker-style tool would have.
- A card's `authorId` is set once at creation and never changes.
  `authorName` is **not** stored on the card and must not be denormalized
  there — a card's author display name is always resolved by looking up
  `participants[card.authorId].name` at render time. If a participant
  could ever be removed from `participants` while their cards remain
  (this application has no such removal action, so it cannot currently
  happen), a lookup miss should be handled gracefully by the renderer
  (e.g. a fallback placeholder name) rather than assumed impossible.
- `createdAt` is a server-set epoch-millisecond timestamp, used only to
  order cards within a column for display (§8.3) — it has no other
  behavioral significance (e.g. it does not gate anything, it is not an
  expiry).
- **Cards are never redacted server-side.** The full state, including
  every card's real `text`, is present in every read and every broadcast
  at all times, regardless of `revealed`. "Hidden until revealed" is
  *purely* a client rendering rule (§8.3) — there is no member of this
  system that withholds a card's content from a request or a stream
  message based on viewer identity. This is a deliberate simplicity
  trade-off (there are no accounts to protect a card's confidentiality
  from in the first place), not a bug; if this ever needs to become a
  genuine secret, that requires per-connection server-side filtering,
  which is out of scope here.
- `revealed` is a single board-wide boolean. There is no per-card reveal
  state — one flag governs every card in the board simultaneously.
- `updatedAt` is a server-set epoch-millisecond timestamp bumped on every
  persisted write.
- **There is no no-op mutation anywhere in this system.** Unlike systems
  that have a natural "re-submit the same value" case, every mutation
  described in §4 either (a) is rejected outright (fails validation, or
  targets a board/participant/card/column that doesn't satisfy the
  operation's requirements) and performs **no write at all**, or (b)
  succeeds and **always** performs a real write: it always bumps
  `updatedAt`, always refreshes the board's expiry (§6.2), and always
  broadcasts the new state (§5) — even when the resulting value is
  unchanged from before. Concretely: calling reveal on a board that is
  already `revealed: true` is not a no-op — it still writes, still
  refreshes the TTL, and still broadcasts. Do not invent a no-op/idempotent
  short-circuit for any operation in this system; none exists.

There is no other persisted entity — no user table, no board list/index
beyond what's needed to detect id collisions at creation time (§6.1), no
audit log, no per-card or per-participant reveal/visibility flag.

## 4. API contract

All endpoints are JSON in / JSON out (except the stream endpoint, §5),
mounted under a common `/api` prefix. `boardId`, and where applicable
`cardId`, are addressed as path segments, never as body fields. Every
endpoint below must reject any other HTTP method with **405** and an
empty body.

Error response bodies are always `{ error: <string> }`. The exact wording
of that string is **not** load-bearing for this spec — only the shape,
the status code, and (for 429 responses) the `Retry-After` header matter.
Don't spend implementation effort matching specific phrasing.

| Method | Path | Body | Success | Purpose |
|---|---|---|---|---|
| POST | `/api/boards` | `{ name }` | 201 `{ boardId, participantId }` | Create a board |
| GET | `/api/boards/:boardId` | — | 200 `{ board }` | Fetch current state |
| POST | `/api/boards/:boardId/join` | `{ name }` | 200 `{ participantId }` | Join a board |
| POST | `/api/boards/:boardId/cards` | `{ participantId, columnId, text }` | 201 `{ cardId }` | Add a card |
| DELETE | `/api/boards/:boardId/cards/:cardId` | `{ participantId }` | 200 `{ ok: true }` | Delete your own card |
| POST | `/api/boards/:boardId/reveal` | `{ participantId }` | 200 `{ ok: true }` | Reveal all cards |
| GET | `/api/boards/:boardId/stream` | — | `text/event-stream` | Live state feed, §5 |

**The card-deletion endpoint must use the `DELETE` HTTP method, not
`POST`**, even though every other mutation in this system uses `POST`.
Cards have genuine per-entity identity — a request addresses one specific
card by id, the way no other mutation in this system addresses a single
sub-resource — so a real REST `DELETE` is the deliberate, sole exception
to an otherwise "every mutation is POST" convention. Sending the
`participantId` as a JSON body alongside a `DELETE` request must be
supported (this is well-defined and supported by all modern HTTP client
runtimes, despite `DELETE` less commonly carrying a body).

Per-endpoint validation, in the exact order each check must run, and
exact rejection predicates:

- **Create** (`POST /api/boards`):
  1. 400 `{ error }` if `name` is absent (falsy) **or** not a string.
     This predicate is deliberately **not** a trim-emptiness check: a
     whitespace-only string (e.g. `"   "`) is a truthy string, so it
     *passes* this check. Do not add a stricter check here — after
     validation, trim the value and truncate to 40 characters before
     storing; a whitespace-only input is therefore accepted and stored as
     an **empty-string** participant name. This is intentionally looser
     than the card-text validation below (§ add-card), which does reject
     whitespace-only input — the two fields do not share a predicate.
  2. Rate limit (§9) — evaluated only after step 1 passes.
  3. Always succeeds after that (201) — creates a fresh board owned by
     nobody in particular, with this caller as its first participant
     (a freshly minted `participantId`, §3).
- **Get** (`GET /api/boards/:boardId`): 404 `{ error }` if the board
  doesn't exist (including if it expired, §6.2). No validation, no rate
  limit, no other failure mode. Not authenticated in any way — any caller
  who knows/guesses a board id can read its full state.
- **Join** (`POST .../join`):
  1. 400 under the **exact same name predicate as create** (absent or not
     a string; whitespace-only passes). Same trim-and-truncate-to-40
     storage rule.
  2. Rate limit (§9).
  3. 404 `{ error }` if the board doesn't exist.
  4. On success, mints a **new** `participantId` (§3) and adds a fresh
     entry to `participants` — joining never re-uses, looks up, or
     otherwise checks for an existing identity, even if the caller
     already holds a stored `participantId` for this exact board from a
     prior visit (that's purely a client-side concern, §8.6, not enforced
     or even consulted server-side). Two joins from the same browser
     produce two distinct participants.
- **Add card** (`POST .../cards`), checks run in this exact order:
  1. 400 `{ error }` if `participantId` is absent (falsy). This is a
     **presence-only check — there is no type check on `participantId`
     anywhere in this system.** A non-string truthy value passes this
     check and is used as-is as a lookup key later (see step 5).
  2. 400 `{ error }` if `columnId` is absent (falsy) **or** not a string.
     Note this predicate does **not** check membership in the fixed
     column set (§3) — that check happens later (step 5) and produces a
     404, not a 400.
  3. 400 `{ error }` if `text` is absent (falsy), not a string, **or**
     — after trimming — reduces to an empty string. Unlike the name
     predicate above, whitespace-only text **is** rejected here. This is
     an exact predicate, not a category: implement all three conditions.
  4. Rate limit (§9) — evaluated only after steps 1–3 all pass.
  5. 404 `{ error }` — deliberately ambiguous across three distinct
     causes, all collapsed into the same status/shape so a client can't
     distinguish them — if the board doesn't exist, **or** `columnId`
     isn't one of the three fixed column ids, **or** `participantId`
     isn't a current member of the board's `participants`.
  6. On success: trim `text` and truncate to 280 characters before
     storing; assign a fresh `cardId` (server-generated, e.g. a v4 UUID);
     record `columnId`, `authorId` (= the caller's `participantId`), and
     a server-set `createdAt`. Respond 201 `{ cardId }`.
- **Delete card** (`DELETE .../cards/:cardId`):
  1. 400 `{ error }` if `participantId` is absent (falsy) — same
     presence-only, no-type-check rule as add-card.
  2. Rate limit (§9).
  3. 404 `{ error }` — again deliberately collapsing multiple causes into
     one response — if the board doesn't exist, **or** no card with this
     `cardId` exists on it, **or** the card exists but its `authorId`
     does not equal the caller's `participantId`. There is no
     distinguishable "not your card" vs. "no such card" response; both
     produce the identical 404.
  4. On success: the card is removed from `cards` entirely. Respond 200
     `{ ok: true }`.
- **Reveal** (`POST .../reveal`):
  1. 400 `{ error }` if `participantId` is absent (falsy) — same
     presence-only rule as above.
  2. Rate limit (§9).
  3. 404 `{ error }` if the board doesn't exist.
  4. **Does not check that `participantId` belongs to this board at
     all** — any truthy value that passed step 1 is accepted; it is used
     only as a rate-limit key (§9), never looked up against
     `participants`. This mirrors the add-card/delete-card pattern of not
     type-checking `participantId`, but goes further: reveal never even
     checks *membership*, only presence.
  5. Sets `revealed` to `true` **unconditionally** — see §3's no-op rule.
     Calling reveal again on an already-revealed board still performs a
     full write, still bumps `updatedAt`/refreshes TTL, and still
     broadcasts.
  6. Reveal is **permissionless**: any participant (or, per point 4, any
     caller supplying any truthy `participantId` value at all) can reveal
     a board's cards. There is no host/owner role anywhere in this
     system.

## 5. Real-time sync

There is exactly one source-of-truth push mechanism: `GET
/api/boards/:boardId/stream`, one long-lived HTTP connection per browser
tab per board, using the streaming mechanism required by §2.2/§2.3.
Behavior:

1. If the board doesn't exist, respond 404 `{ error }` and end — same
   shape as the plain GET. This endpoint requires no `participantId` and
   performs no membership check — anyone who can reach the URL for an
   existing board id receives its live state, joined or not.
2. Otherwise, begin the streamed response and **explicitly flush its
   headers to the client immediately**, before any further asynchronous
   work (including sending the first event below). Don't rely on an
   implicit flush on first write — some server runtimes/intermediary
   proxies don't consider the connection genuinely open until headers are
   flushed on their own, independent of body data.
3. Immediately after that, before doing anything else, send the **entire
   current board state** as the first event. A client must never be left
   waiting for a first mutation to see any data.
4. From then on, **every successful mutation to this board from any
   client, via any of the API endpoints in §4** (recall from §3: every
   successful mutation is a real write, none are no-ops), results in the
   full new board JSON being delivered as a new event to every open
   stream subscribed to that board — not a diff, not a partial patch, the
   entire object, every time. This is a deliberate simplicity trade-off
   given boards are small (a handful of participants); there is no
   incremental merge protocol anywhere in the system.
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
   subscription/connection resource. Register the disconnect-listener
   before starting any `await`-able subscribe step, not after, and guard
   with a flag so that if the client is already gone by the time the
   subscribe step resolves, cleanup runs immediately instead of assuming
   the (already-fired-and-gone) disconnect listener will handle it.
8. This endpoint has a materially longer allowed execution duration than
   the other endpoints, since it's meant to stay open for the life of a
   tab's visit to a board (not a fixed short request/response).
9. This endpoint is explicitly **not** subject to the rate limiting in
   §9 — see §9's closing note for why.

**A consequence of steps 2–4 worth stating explicitly, since it affects
how the acceptance test for this section must be written:** because the
initial full-state event (step 3) is sent before the server subscribes to
the backing store's pub/sub channel for this board (step 4's mechanism),
there is a real, narrow window in which a mutation could complete *after*
a client has received the initial snapshot but *before* that specific
connection's subscription has finished registering server-side. Such a
mutation is never lost from the board's actual state (§7) — it's simply
not pushed to that one not-yet-subscribed connection the instant it
happens; that connection would only catch up on its next reconnect or the
next subsequent mutation's broadcast. A test that (a) opens the stream,
(b) reads the initial event, then (c) immediately triggers a mutation and
expects to read it as the very next event on that same connection is
exercising exactly this race, and **must not simply assume the
subscription is already active** — either poll the backing store for
confirmation that a subscriber is registered on this board's channel
before triggering the mutation under test, or otherwise deterministically
wait for subscription completion, rather than relying on the mutation
path's own incidental latency to usually win the race. A test written
without this guard is not wrong in intent but is flaky by construction —
treat this as a required property of the test itself (§10), not an
optional hardening.

## 6. Board lifecycle

### 6.1 Creation & id format

A board id is a short, memorable, URL-safe slug: two lowercase words
joined by hyphens plus a 3-digit numeric suffix, e.g. `brave-otter-482` —
pattern `^[a-z]+-[a-z]+-\d{3}$`. Generate from two fixed word lists
(adjectives, nouns — a dozen-plus entries each is enough) plus a random
number in the inclusive range 100–999 (i.e. always exactly three digits,
never leading-zero-padded).

On creation, check the candidate id against existing boards and retry a
handful of times (e.g. up to 5) on collision before giving up and
proceeding with whatever id was last generated regardless of whether it
still collides. **This existence check and the subsequent write are
deliberately *not* required to be a single atomic conditional
operation** — an implementation may perform a plain existence check
followed by a separate, unconditional write, accepting the narrow
residual race window that leaves (two creation requests independently
generating and colliding on the same id in the same instant could
overwrite one another). This is a known, deliberate simplification for
this system specifically (word-list size makes collisions rare enough,
and losing a from-scratch, still-empty board to this race is low-stakes)
— do **not** generalize the atomic-conditional-write requirement from §7
to this id-collision check; it applies only to mutations against an
*existing* board's state, not to the creation-time id pick.

### 6.2 Expiry

Boards are **not** explicitly deleted by any user action (there's no
owner to do so). Instead, a board's backing storage entry carries an
inactivity-based expiration of **4 hours**, refreshed to a full 4 hours on
every mutation that performs a write — per §3's no-op rule, that is
*every* successful mutation without exception (there is no mutation in
this system, unlike some sibling systems, that both succeeds and skips
the refresh). Once expired, the board is simply gone: subsequent
`GET`/stream/mutation calls for that id behave exactly as if it never
existed (404).

## 7. Write concurrency (must not lose updates)

Multiple participants can mutate the same board at effectively the same
instant (e.g. two people adding cards to the same column within the same
tick). A naive read-modify-write (read current state, apply change in
application code, write the whole thing back) can silently lose one of
the two updates if they interleave: both reads see the same prior state,
both writes proceed, and the second write clobbers the first with no
error and no trace of the lost change.

**Required guarantee:** under any level of concurrent write contention on
one board, every accepted mutation described in §4 must eventually be
reflected in the board's state — none may be silently dropped. Achieve
this with an optimistic concurrency loop, applied uniformly to **every**
mutation in §4 that modifies an *existing* board (join, add-card,
delete-card, reveal — not board creation, see §6.1):

1. Read the current raw state (keep the raw form for the compare step,
   not just the parsed object).
2. Apply the requested change in application code to produce a new state,
   bump `updatedAt`. If the change is invalid for this board's current
   state (e.g. an unknown `participantId` on add-card, or a non-author on
   delete-card), abort this attempt entirely and fail the whole operation
   (§4's corresponding 404) — do not retry, and do not write or publish
   anything.
3. Attempt an **atomic conditional write**: replace the stored value only
   if it is still byte-identical to what was read in step 1 (this must be
   evaluated and executed as a single atomic operation server-side by the
   backing store, e.g. a server-side script, not as separate
   read-then-write calls from the application). If the value had already
   changed (someone else wrote in between), the conditional write must be
   rejected/no-op — not silently override the interim value.
4. On rejection (someone else's write won the race, distinct from step
   2's validation failure), re-read fresh state and retry the whole cycle
   from step 1. Use a small jittered/randomized backoff that increases
   somewhat with attempt count, so that many competing retries don't keep
   re-colliding in lockstep.
5. Cap retries at a reasonable bound (e.g. 10 attempts) and treat
   exhausting them as a hard failure (an app bug or pathological
   contention), not a silently-swallowed error.
6. Only on a **successful** conditional write does the mutation publish
   the new state to real-time subscribers (§5) — a rejected/retried
   attempt must not publish an intermediate value that never actually
   became the stored state.

**Any operation that mints a fresh identifier as part of one of these
mutations must generate that identifier exactly once, before entering the
retry loop above, and reuse the identical value across every retry
attempt of that same logical operation** — never regenerate it per
attempt. This applies to **every** such case in this system, not just one
of them: both the `participantId` minted on join (§4) and the `cardId`
minted on add-card (§4) must be generated once, up front, outside the
loop described above. Regenerating per attempt would silently produce a
different id than whatever the caller was told to expect (or, in a
partial-failure scenario, orphan/duplicate an id) if a retry occurs.

A transaction/optimistic-lock mechanism that is scoped to a single
persistent connection (e.g. classic `WATCH`/`MULTI`) is **not** sufficient
if the implementation shares one backing-store connection across
concurrent, logically-unrelated requests — two unrelated in-flight
mutations sharing that connection could corrupt each other's watched
state. The conditional-write step must be atomic regardless of connection
sharing (e.g. implemented as a single server-side script/procedure the
store executes atomically, not as multiple round-trips from the app).

**Acceptance test this must pass:** 25 participants joining the same new
board in the same instant, followed by all 25 adding a distinct card to
the *same* column in the same instant — afterward, all 25 participants
must exist and all 25 cards must be present with none lost or overwritten.

## 8. Frontend behavior

### 8.1 Routing / shell

On load, read the current path. An empty/root path renders the landing
view. Any non-empty path is treated as a board id and renders the board
view for that id (no matching against a route table — whatever's in the
path segment is passed straight through as the board id to fetch/
subscribe).

### 8.2 Landing view

A single screen with two independent forms:

- **Create**: a name field (required) and a submit control. On submit,
  validate a name is present client-side (inline error if not, e.g.
  "Enter your name first."); call the create endpoint; on success,
  remember the returned `participantId` locally scoped to the new
  board's id, remember the name entered (§8.6), and navigate (full page
  load) to `/{boardId}`. On failure, show a generic inline error (e.g.
  "Could not create board. Try again.").
- **Join**: a name field and a board-code field (both required client-side
  before submitting, distinct inline errors for each missing case, e.g.
  "Enter a board code."). On submit, call the join endpoint for that
  board id; on a 404 specifically, show a distinct "board not found"
  message rather than the generic failure message; on any other failure,
  show a generic message (e.g. "Could not join board."); on success,
  remember the participant id + name as above and navigate to `/{code}`.
- The name field(s) should pre-fill from the remembered name (§8.6) so a
  returning user doesn't retype it every time.
- The exact copy/wording of every message above is open to the
  implementer (§11) — only the distinctness of the 404-vs-generic join
  error, and the presence of both required-field checks, are load-bearing.

### 8.3 Board view — layout & elements

- A header showing the board's id/code, a **Reveal cards** control, and a
  "copy invite link" control that copies the current page's full URL to
  the clipboard and gives transient feedback (its label changes to
  something like "Copied!" for roughly a second and a half before
  reverting to its original label).
- A participant strip (see §8.4).
- Exactly **three** column panels, one per fixed column (§3), built once
  from the `columns` array present in the *first* state received — the
  column set never changes during a board's life, so there is no need to
  rebuild these column shells on every subsequent update, only their
  contents (card counts, card lists).
- Each column panel contains, top to bottom: a title, a live card count
  (e.g. "N card"/"N cards" — exact copy/pluralization wording is open,
  §11), a one-line add-card mini-form (a text input plus a submit
  control), and the list of cards currently in that column.
- **Add-card mini-form behavior:** submitting the form (however
  triggered — pressing Enter in the field or activating the submit
  control both produce the same single `submit` event on the form; do
  not additionally wire a separate click handler on the submit control
  that *also* triggers the add) does the following, in order: read and
  trim the input's current value; if empty after trimming, do nothing
  (no request, no error shown); otherwise clear the input immediately
  (optimistic, before the network request resolves, so the field is
  instantly ready for the next entry) and issue the add-card request for
  that column with the entered text. There must be exactly one commit
  path per submission — wiring more than one listener that could each
  independently fire an add-card request for the same submission is a
  defect, not a valid alternate implementation.
- **Card visibility rule (hidden until revealed), evaluated per card, for
  the viewer's own `participantId`** (which may be unset/null if this
  browser has not joined this specific board yet, see the join gate
  below): render a card with its real content if `board.revealed === true`
  **or** `card.authorId === this participant's id`; otherwise render a
  face-down placeholder for that card instead — one placeholder element
  per hidden card (not merely a count), so a column visibly fills up as
  people work even though its contents aren't legible yet. A viewer who
  hasn't joined yet (no participant id) can never satisfy the
  `authorId === mine` branch, so every card renders as a placeholder for
  them until the board is revealed — this is expected, not a bug: reading
  a board's live state does not require having joined it.
- **Real (non-placeholder) card rendering**: the card's text (preserve
  internal whitespace/line breaks, wrap long text — do not truncate or
  collapse it), the author's display name resolved via
  `participants[authorId].name` (§3) with a distinguishing suffix (e.g.
  "(you)") appended only when the card's author is this viewer, and — only
  on cards authored by this viewer — a delete control that removes just
  that card. No delete control is rendered on any other participant's
  card, before or after reveal.
- Cards within a column are always rendered **sorted by `createdAt`
  ascending** (oldest first), consistently for every viewer.
- The **Reveal cards** control must reflect already-revealed state by
  disabling itself and relabeling (e.g. to "Cards revealed") once
  `board.revealed` is true, so it reads as inert rather than
  clickable-but-ignored. It has no other state.
- Reveal, unlike delete, is **not** gated to any particular participant in
  the UI — any joined participant may click it (matching §4's
  permissionless reveal).
- A **join gate**: if this browser has no remembered `participantId` for
  this specific board (e.g. someone opening a shared link for the first
  time), block interaction with a modal/overlay prompting for a name
  before they can add cards, delete cards, or reveal — the rest of the
  board (participant strip, columns, card placeholders/content per the
  visibility rule above) is still visible/live behind it, since receiving
  the live stream does not require having joined (§5.1). Submitting a name
  in this overlay calls join, stores the returned participant id, and
  dismisses the overlay. **Care point:** whatever mechanism toggles this
  overlay's visibility must actually prevent both rendering and
  interaction when hidden — a toggle that only sets a semantic "hidden"
  flag without also forcing the element to not render is insufficient if
  any of that element's own CSS rules unconditionally set a `display`
  value, since author style rules win over user-agent default hidden
  behavior at equal specificity. Any hide/show toggle in this UI needs an
  explicit rule making the hidden state actually invisible.
- A **fatal error state**: if the very first attempt to open the live
  stream for this board fails (e.g. the board doesn't exist / the link is
  invalid) *before any state has ever been successfully received*, replace
  the board view entirely with a short explanatory message and a link
  back to the landing page. If state has already been received at least
  once and the stream later has a transient error, do **not** enter this
  fatal state — the transport is expected to recover on its own (§5.6).

### 8.4 Participant strip rendering

One element per participant, in the order the state object provides them
(insertion/join order), each showing:
- An avatar: the first letter of their name (uppercased), on a background
  color that is **deterministic per name** (the same name always produces
  the same color for every viewer, every session — derive it via some
  stable hash of the name mapped into a color space; the exact hue/formula
  is the implementer's own choice, §11 — the requirement is determinism
  plus reasonable visual variety across different names, not a specific
  palette or algorithm).
- Their display name, with a distinguishing suffix (e.g. "(you)") appended
  only to the entry matching this browser's own remembered
  `participantId` for this board — every other viewer sees that same
  person's entry without the suffix.
- No vote, status, or other per-participant indicator — unlike a
  planning-poker-style participant list, there is nothing else to show per
  participant in this system (§3: participants have only a name).

### 8.5 Client-side persistence (no server session)

Two independent pieces of state persisted in the browser (e.g.
`localStorage`), never sent anywhere except as explicit request fields:
- The `participantId` for a given board, keyed by that board's id, so a
  page refresh doesn't drop the user from the board or prompt them to
  join again unnecessarily. This value always originates from a create or
  join response (§3) — the client never invents or asserts one on its
  own.
- The most recently used display name, independent of any board, so it
  can pre-fill name fields on future visits (both landing forms and the
  join overlay).

### 8.6 Live update handling

Every message from the live stream (§5) fully replaces the client's
notion of board state and re-renders from it — there is no local
merge/patch logic anywhere in the client, and (unlike a system with an
in-progress editable field a user might be focused on) there is no
focus-preservation exception needed anywhere in this UI: the only text
input with any persistent value is the add-card field, which is cleared
immediately on its own submission (§8.3) rather than ever needing to
survive an incoming state replace.

### 8.7 Visual design intent

A single dark theme (not user-toggleable) built from one small set of
shared design tokens (a background color, one or two panel/surface
colors, a border color, a primary and a dimmed text color, a primary
accent, a secondary accent, an error/danger color, a shared corner-radius
value, and font stacks for body/monospace text), applied consistently
across the landing view and board view. Layered on top of that shared
set: **three additional accent tokens, one per fixed column**, used
consistently (e.g. as a top border accent on each column panel and as the
color of that column's add-card submit control) to give each column a
distinct, consistent identity at a glance. If component encapsulation is
used (§2.7), it should consume one shared token source rather than
duplicating color values per component, while still not leaking styles
across component boundaries. Visual character: deep near-black background
with a soft radial accent-colored glow near the top, cards/panels as
slightly-raised surfaces with subtle borders, a vivid accent color for
primary actions, generous rounded corners. The exact color values,
gradients, spacing, and animation timings are the implementer's own
choice (§11) — only the token *structure* (shared base tokens plus three
column accents) and the qualitative character above are load-bearing.

## 9. Abuse mitigation (rate limiting)

The system is fully permissionless for most actions (§2, §4) — anyone can
call any mutation for any board they know the id of. To bound abuse
without introducing accounts, every mutation endpoint enforces one or more
request-rate limits, evaluated *before* performing the underlying action.

**Ordering relative to request validation:** §4's request-body validation
always runs *before* rate-limit evaluation, for every mutation endpoint,
with no exceptions. A request that fails §4's validation (missing name,
missing participantId, missing/invalid columnId, missing/empty text,
etc.) must return its 400 without touching any rate-limit counter at
all — malformed requests are free; only requests that pass validation can
consume a limit. Where an endpoint has several validation checks (e.g.
add-card checks `participantId`, then `columnId`, then `text`), all of
them run and can produce a 400 before the rate limit is ever consulted;
none of them touch a rate-limit counter.

| Endpoint | Limit key(s) | Threshold |
|---|---|---|
| create | client IP | 10 per 10 minutes |
| join | client IP + board id | 20 per 10 minutes |
| add card | participantId + board id | 30 per 10 seconds |
| add card | client IP + board id | 100 per 10 seconds |
| delete card | participantId + board id | 30 per 10 seconds |
| delete card | client IP + board id | 100 per 10 seconds |
| reveal | participantId + board id | 10 per 10 seconds |
| reveal | client IP + board id | 30 per 10 seconds |

Rules:
- Where an endpoint has two limits (a participant-scoped one and an
  IP-scoped one), **both** must hold; check them in a fixed order and stop
  at the first one that's already over its threshold (don't touch/
  increment counters for checks after the first failure — this must be
  independently testable: pre-exhaust the first check, confirm the second
  check's counter is untouched).
- The participant-scoped limit alone is a weak defense — `participantId`
  is minted per join call and never re-verified as belonging to a
  specific human, so a client can trivially join again to mint a fresh id
  and dodge a participant-scoped-only limit. The IP-scoped companion
  limit is what actually bounds a determined abuser, and is deliberately
  set generously above the participant limit — this is a team tool that
  may be used by a room full of people sharing one office network/VPN
  egress IP, so one IP legitimately represents many simultaneous real
  users.
- On exceeding any applicable limit: respond **429** with a `Retry-After`
  header (seconds until the limit window resets) and a JSON error body,
  and do not perform the requested mutation at all (no partial effect).
- A simple fixed-window counter (increment-and-check, with the window's
  expiry set only on that window's first request) is sufficient — it
  doesn't need to be a sliding window or token bucket. A benign edge case
  (a counter briefly having no expiry set right at the moment a new
  window starts) is acceptable; the worst case is self-correcting within
  one window and is not a meaningful abuse vector.
- `create` has no `participantId` yet to key on (it's what mints the
  first one) and no board yet either — IP-only is sufficient. `join` has
  no `participantId` yet either (same reason) but does have a target
  board — IP + board id is sufficient.
- The live stream endpoint (§5) is explicitly **not** rate-limited by this
  scheme, nor is the plain `GET` board-state endpoint — opening many
  concurrent long-lived stream connections, or polling board state, is a
  different kind of resource concern (connection exhaustion / read load,
  not mutation spam) and is out of scope for this mechanism.

**Deriving "client IP" — an exact, independently unit-testable predicate,
not left to guesswork:** every "client IP" key above must be produced by
this exact procedure, in order: (1) if the request carries an
`x-forwarded-for` header, split it on commas and take only the **first**
entry, trimmed of surrounding whitespace — that is the originating
client, not any intermediate proxy hop, and later entries in the list must
be ignored; (2) otherwise, fall back to the raw transport connection's
remote address (i.e. the socket the request arrived on); (3) if neither
is available, fall back to a fixed placeholder string (e.g. `"unknown"`)
rather than throwing or leaving the key `undefined`. This function must be
callable and unit-testable in isolation (given a plain object with
`headers`/socket shape, not a live network request) — it is not
acceptable to leave IP derivation as an unstated implementation detail,
since two implementations that disagree on it (e.g. one takes the last
forwarded-for entry instead of the first, or omits the final fallback)
produce different, non-interoperable rate-limit keys from the same
request.

## 10. Acceptance checklist

Each item below **must exist as an automated, independently re-runnable
test against a real backing store** — not mocked, and not a one-time
manual script run once and discarded. Roughly one test file per concern
(lifecycle, concurrency, streaming, rate limiting) mirrors the grouping
below; that grouping is a reasonable way to split them, not a requirement
to use exactly four files. Manual verification across two real browser
sessions is a useful supplement for the UI-facing behavior in §8, not a
substitute for automated coverage of the items below:

- [ ] Creating a board returns a slug-shaped id matching
      `^[a-z]+-[a-z]+-\d{3}$`, the fixed 3-column set, an empty `cards`
      map, and `revealed: false`, plus a working participant id whose
      name matches what was sent.
- [ ] Full lifecycle: create → second participant joins (participant
      count now 2) → both add cards to different columns (each card's
      `columnId`/`authorId` recorded correctly, board still unrevealed) →
      reveal flips `revealed` to `true`.
- [ ] Adding a card with an unknown/non-member `participantId` is
      rejected (returns a null/failure result, not a thrown error),
      performs **no write** (board's `updatedAt` and `cards` are
      byte-for-byte unchanged after the attempt), and triggers **no
      broadcast** to subscribers.
- [ ] Adding a card with a `columnId` that isn't one of the three fixed
      ids is rejected the same way (no write) even when `participantId`
      is valid.
- [ ] Deleting a card as a participant who is not that card's author is
      rejected (no write, no broadcast) — the card must still be present
      and unchanged afterward.
- [ ] Deleting a card as its actual author succeeds and the card is gone
      from the board's `cards` afterward.
- [ ] Every read/mutate operation against a nonexistent/expired board id
      fails predictably (null/404-equivalent) rather than throwing or
      crashing — cover get, join, add-card, delete-card, and reveal all
      independently against a bad id.
- [ ] 25 participants joining the same new board at the exact same
      instant, followed by all 25 adding a card to the *same* column at
      the exact same instant, all land with none lost (§7's concurrency
      guarantee) — afterward exactly 25 participants and exactly 25 cards
      exist.
- [ ] Connecting to the live stream immediately yields the current board
      state as the first message, and a card added after connecting is
      pushed down that same open connection without needing a reconnect.
      Per §5's closing note, this test must deterministically wait for the
      server's subscription on this board's channel to be active before
      triggering the card add — not assume it, or the test is flaky by
      construction.
- [ ] A rate limit under its threshold allows requests; the same key over
      threshold is rejected with a 429 and a positive `Retry-After`;
      independent keys (including two different limit checks on the same
      endpoint) are tracked independently of each other, and a check that
      short-circuits before a later check must leave that later check's
      counter untouched.

## 11. Implementer's judgment vs. hard requirements

To avoid leaving this ambiguous by omission, everything in this document
is a hard requirement **except** the following, which are explicitly left
open to whoever builds from this spec:

- Exact wording of every user-facing error/status message (§4, §8.2), the
  reveal button's relabeled text, and card-count pluralization copy
  (§8.3) — only the *behavior* they gate on is load-bearing, not the
  strings themselves.
- The exact avatar color-hashing formula (§8.4) — only determinism +
  reasonable variety is required.
- All specific color values, gradients, exact spacing/sizing, corner
  radius value, animation/transition timings, and font choices (§8.7) —
  only the token *structure* (a shared base palette plus three
  per-column accents) and the qualitative dark-theme character described
  are load-bearing.
- The exact heartbeat interval within the "20–30 seconds" range (§5.5)
  and the exact retry/backoff constants in §7 (attempt cap, backoff
  formula) as long as they satisfy the stated bounds and guarantees.
- Word-list contents/size for board-id generation (§6.1) — any
  reasonably sized adjective/noun list works, the format and collision
  handling are what's load-bearing.
- File/module organization, variable/function naming, and how many test
  files the acceptance checklist (§10) is split across.

Everything else — every status code, every validation predicate, every
rate-limit threshold, the collapsed-vs-distinct 404 behaviors, the no-op
rule in §3, the atomic-id-generation rule in §7, and the visibility rule
in §8.3 — is a hard requirement a faithful implementation must match
exactly, not merely approximate.
