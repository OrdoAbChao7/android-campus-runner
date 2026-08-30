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

