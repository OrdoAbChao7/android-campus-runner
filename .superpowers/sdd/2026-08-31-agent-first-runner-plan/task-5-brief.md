# Task 5 brief — local-only authenticated Dashboard

Read this first; it is the complete requirements for Task 5.

Harden `dashboard/app.py` and tests:

- Bind only to `127.0.0.1`, debug disabled, and do not emit `Access-Control-Allow-Origin: *`.
- Require a non-empty local control token on all mutating endpoints (`POST /api/accounts`, `POST /api/config`, `POST /api/run/start`, `POST /api/run/stop`) using constant-time comparison. Read token from environment or an ignored local config, never commit/log/return it.
- Continue rejecting and never returning/storing `password`/`passwd`; validate enterprise, phone, and credential-ref length/characters and duplicate enterprises.
- Accept route and account identifiers, not arbitrary filesystem paths. Resolve route basenames only under the configured routes directory; reject traversal, absolute paths, nested paths, nonexistent files, unsupported extensions, and invalid routes without writing configuration.
- Do not accept arbitrary executable/working-directory/command templates from requests. Use only preconfigured typed provider/ADB adapters. Missing valid per-account RunIntent must remain fail-closed before subprocess/provider/UI work.
- Add tests for unauthenticated mutations, localhost/CORS, path confinement, input validation, and no subprocess invocation without authorization.

Run dashboard-focused tests and `pytest -q`, commit focused changes, and append `task-5-report.md`. Do not push.
