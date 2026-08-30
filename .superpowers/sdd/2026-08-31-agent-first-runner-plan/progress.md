# SDD ledger — plan: E:\\Projects\\school\\running\\.worktrees\\agent-first-runner\\docs\\superpowers\\plans\\2026-08-31-agent-first-runner-plan.md

## Preflight scan

| Item | Shared surface | Finding | Ruling |
|---|---|---|---|
| Task 1 ↔ Task 3 | accounts, workflow inputs | Task 1 removes plaintext secrets; Task 3 consumes account identity only | Compatible; credential resolution remains outside runner |
| Task 2 ↔ Task 3 | state/evidence/provider lifecycle | Task 2 defines states; Task 3 emits provider transitions | Task 3 must use Task 2 state names and journal events |
| Task 3 ↔ Task 4 | workflow, WeCom start | Task 3 blocks start without intent; Task 4 adds checkpoint | Checkpoint precedes authorization and no implicit tap |
| Task 4 ↔ Task 5 | account/run API | Dashboard must call same guarded path | No dashboard-specific bypass |
| Task 1 | account files/tests | Tests cover schema and secret absence | Consistent |
| Task 2 | new kernel files/tests | Tests cover replay/drift/transitions | Consistent |
| Task 3 | provider/workflow/runner | Tests cover stop and ordering | Consistent |
| Task 4 | WeCom/checkpoint | Tests cover fingerprint and switching guards | Consistent |
| Task 5 | dashboard | Tests cover localhost/token/path restrictions | Consistent |
| Task 6 | release | Depends on all prior tasks and authorized device | Consistent |

Ruling: Worktree Git index is sandbox-blocked, so implementation continues in the preserved branch `codex/ide-wip-preserve`; the separate worktree remains read-only reference material. Cost if wrong: branch isolation is weaker, but all changes stay on a non-main local branch and are reviewed before any push.

Task 1: fix round 1/5 (4 addressed, 1 deferred to Task 5 — dashboard binding/token/path/keep-gps; commits cc14357..6124c50)
Task 1: complete (commits cc14357..6124c50, review clean for task scope; Task 5 carries dashboard safety finding)
Task 2: implementation complete pending review (commit 3f1bf95)
Task 2: fix round 1/5 (3 addressed, 0 open; commits 3f1bf95..ea7c1f6)
Task 2: fix round 2/5 (1 addressed, 0 open; commits ea7c1f6..56429a8)
Task 2: fix round 3/5 (2 addressed, 0 open; commits 56429a8..33e7452)
Task 2: fix round 4/5 (1 addressed, 0 open; commits 33e7452..d883251)
Task 2: complete (commits 3f1bf95..d883251, review clean)
