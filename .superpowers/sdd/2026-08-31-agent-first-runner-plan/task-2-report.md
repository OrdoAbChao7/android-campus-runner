# Task 2 report — run safety kernel

## Status

Implemented the typed, dependency-free safety kernel without changing dashboard or provider lifecycle code.

- Added immutable `RunIntent` and `RunObservation` bindings for intent ID, ADB serial, device fingerprint, enterprises, route SHA-256, time window, maximum duration, and allowed action IDs.
- Added exact-byte route hashing plus a concurrency-safe, in-process single-use `IntentUseRegistry` that rejects replayed or duplicated intent IDs.
- Added the complete documented `RunState` enum, ordered state machine, typed illegal-transition error, `SAFE_STOP`, and journaled rejected transitions.
- Added sanitized JSONL events and immutable JSON snapshots. Sensitive key values (including passwords, tokens, authorization headers, cookies, and credentials) are redacted.

## Commit

`3f1bf95 feat: add run safety kernel`

## Verification

`pytest -q` completed successfully: **59 passed, 1 skipped**.

The focused kernel test file also passed independently: **7 passed**.

## Concerns

- Intent consumption is intentionally in-process only. A future orchestration layer should provide durable storage for consumed IDs if a process restart must preserve anti-replay protection.
- Existing unrelated working-tree entries were preserved: `.superpowers/sdd/2026-08-31-agent-first-runner-plan/progress.md` is modified and `task-2-brief.md` is untracked.

## Fix round 1

### Status

- `IntentUseRegistry.register()` now records the full canonical `RunIntent` at issuance. `consume()` requires that registration and rejects a same-ID `dataclasses.replace()` copy whose serial, route hash, or any other bound field differs.
- Evidence sanitization now also redacts Bearer credentials and named `token`, `credential`, password, secret, cookie, authorization, and API/access-key values embedded in ordinary message or exception text. This applies to both events and snapshots.
- `StateMachine.transition()` now records a rejected transition and raises `InvalidStateTransition` for non-`RunState` input rather than dereferencing `.name` and raising `AttributeError`.

### Commit

`ea7c1f6 fix: harden safety kernel bindings`

### Verification

- Focused safety-kernel suite: `9 passed`.
- Full suite: `61 passed, 1 skipped`.

### Remaining concern

The explicit registration step is part of the anti-tamper contract: orchestration code must register the original issued intent before it calls `consume()`.

## Fix round 2

### Status

Evidence persistence now treats non-primitive values as untrusted and records only their type plus a redaction marker. Text cleaning consumes complete quoted sensitive values, redacts sensitive token-like fragments, and replaces sensitive event names with `redacted_event`. Snapshot names containing sensitive terms are rejected before a filename can leak them. Ordinary non-sensitive text remains available in events and snapshots.

### Commit

`56429a8 fix: redact untrusted evidence text`

### Verification

- Focused safety-kernel suite: `10 passed`.
- Full suite: `62 passed, 1 skipped`.

### Covered regressions

- `token: 'secret value'` does not retain the quoted value tail.
- `KeyError('secret-token')` and snapshot exception values do not persist their arguments.
- Sensitive event names do not persist their original identifier; non-sensitive status and UI text stay readable.

## Fix round 3

### Status

Quoted-value sanitization now consumes escaped quotes and multiline content for named sensitive fields and Bearer values, so no value tail remains after redaction. Sensitive labels are matched only on non-alphanumeric boundaries; ordinary words such as `tokenize`, `credentialing`, and the `tokenizer_started` event remain readable.

### Commit

`33e7452 fix: preserve escaped evidence redaction`

### Verification

- Focused safety-kernel suite: `11 passed`.
- Full suite: `63 passed, 1 skipped`.

### Covered regressions

- Escaped quote payload: `token: "abc\\\"ordinary words"`.
- Multiline quoted credential and Bearer values.
- Non-sensitive `tokenizer_started` event and `tokenize credentialing continues` text.

## Fix round 4

### Status

- Unified named, quoted, and spaced sensitive-label matching on non-alphanumeric boundaries, so underscore-delimited labels such as `X_TOKEN`, `oauth_token`, and `API_CREDENTIAL` enter the value-redaction path.
- Named and spaced unquoted values now consume through an explicit line or punctuation boundary, preventing multi-word value tails from reaching evidence artifacts.
- Preserved ordinary `tokenize` and `credentialing` text, which does not meet the sensitive-label boundary rule.

### Verification

- Added a regression covering named, quoted, and spaced underscore-prefixed secret labels and ordinary-word preservation.
- Focused safety-kernel suite: `12 passed`.
- Full suite: `64 passed, 1 skipped`.
