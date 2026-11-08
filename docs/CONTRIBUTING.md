# Contributing

The project's viability depends on strangers running relays and reading this
code, so accessibility is a design constraint, not a courtesy.

## Ground rules

* **Code, comments and `docs/` are in English.** The HUD is translated, Spanish
  by default, and the README is bilingual (§15). The code must not be a barrier.
* **Cite the architecture.** When a decision is not obvious, reference the
  section it comes from (`§5`, `§16`) rather than restating the reasoning.
* **Pure Python is the reference implementation.** Any native acceleration is
  optional and must have a working pure-Python fallback (§16, tactic 5). Nobody
  should need a toolchain to audit or run a node.
* **No new runtime dependency without a reason in the PR description.** Every
  dependency is attack surface for an anonymity tool.

## Before opening a PR

```bash
ruff check . && ruff format --check .
pytest -q
python bench/link_throughput.py        # §16 gates; CI runs this too
```

A change that touches the frame layer, the link, or anything in the hot path has
to keep the performance gates passing. If a gate turns out to measure the wrong
thing, change the gate **in the same PR** and say so in `§16` — that is what
happened in M1, and the record of it is in the document.

## Things that will be asked of you

* A test that fails before your change and passes after.
* For anything touching anonymity: which line of the threat model (§10) it
  affects, and why it does not weaken it.
* No telemetry, no external requests from the HUD, no CDN (§9, hard rules).
