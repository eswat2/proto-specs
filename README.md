# proto-specs

Prototype application specifications, published for cleanroom implementation.

**Live site:** [eswat2.github.io/proto-specs](https://eswat2.github.io/proto-specs/)

## What this is

Each subdirectory contains a `SPEC.md` for a small web app that was originally designed and built in a private repo. Instead of open-sourcing the code, this repo open-sources the **specification** — a document precise enough that someone who has never seen the original implementation (human or AI) can build a functionally and architecturally faithful version from the document alone.

## Why specs instead of code

The original implementations stay private, but the *design* behind them — the data model, the API contract, the concurrency guarantees, the exact validation rules — is genuinely useful to share:

- as a portfolio artifact
- as a teaching example of what an unambiguous spec looks like
- as a reproducible benchmark for how well an AI coding agent can turn a complete specification into working software with zero access to a reference implementation

Publishing the spec delivers all of that without exposing a single line of the private source.

This only works if the spec is actually complete. Each `SPEC.md` is written to be **implementation-derivable**:

- Anywhere behavior matters, it is pinned down exactly — status codes, field names, validation predicates, rate limits, error shapes.
- Anywhere only visual or cosmetic intent matters, the document says so explicitly and leaves the specifics to the implementer, rather than leaving them ambiguous by omission.
- Hard platform constraints (no WebSockets, no client-side router, no frontend build step, single backing store with atomic conditional writes, etc.) are called out separately from product behavior, so an implementation cannot accidentally satisfy the “what” while ignoring the “how.”
- An acceptance checklist at the end of each spec doubles as the test plan — every item is meant to become a real, automated, re-runnable test.

The bet is simple: if a specification is unambiguous enough, a capable engineer — or a capable model — should be able to produce a working, behaviorally faithful app from it in one pass, without ever seeing the private original.

## Results so far

Both specs in this repo have been used to drive a real cleanroom build. Claude Sonnet 5 (not the absolute latest frontier model), given only the `SPEC.md` and no access to the original implementation, produced a fully working app on the first attempt for both the [planning poker tool](poker/SPEC.md) and the [retro board tool](retro/SPEC.md).

## How to use these specs

1. Pick a spec (e.g. `poker/SPEC.md`) and drop it into a fresh, empty project directory — no other context from this repo is needed or should be used. In particular, don't hand a coding agent the corresponding `architecture.html` — it documents the private reference implementation's actual file/function names and would bias a "cleanroom" build away from being cleanroom.
2. Hand it to a coding agent (or a human engineer) with instructions to treat it as the sole source of truth — see the example prompt below.
3. Verify the result against the spec’s acceptance checklist (§10 in both documents).

### Example cleanroom-build prompt

```
You are building an application strictly from the specification in
SPEC.md in this directory. Do not search for, reference, or assume
knowledge of any existing/reference implementation of this app —
treat SPEC.md as if it is the only thing anyone has ever written about
this system.

Rules:
- Everything the spec states as a hard requirement (status codes, field
  names, validation predicates, limits, concurrency guarantees) must be
  implemented exactly, not approximately.
- Anywhere the spec explicitly leaves a detail to implementer’s
  judgment, make a reasonable choice yourself — do not stop to ask.
- Build a complete, deployable application, and implement the spec’s
  acceptance checklist as real, automated, independently re-runnable
  tests against a real backing store (not mocked).
- When you’re done, list anything you had to make a judgment call on
  that the spec didn’t fully pin down.

Build it end to end: backend, frontend, and tests.
```

## Repo structure

- [`poker/SPEC.md`](poker/SPEC.md) — a no-signup, real-time planning poker estimation tool.
- [`retro/SPEC.md`](retro/SPEC.md) — a no-signup, real-time team retro board tool.

Each directory also has an `architecture.html` — a human-readable map of the *private* reference implementation (file layout, function names, data flow), published for browsing, not for feeding to a coding agent. See the cleanroom-build caveat above.

Additional specs will be added here as they are written.

## License

MIT — see [LICENSE](LICENSE).

---

*README refined with help from Grok.*
