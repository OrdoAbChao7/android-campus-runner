# Task 1 report — secure account model

## Status

Complete. Account runtime objects now carry only `enterprise`, `phone`, an
optional `credential_ref`, and `current`; they never carry plaintext
passwords. The production YAML loader rejects plaintext credential keys and
does not include secret values in validation errors. Enterprise/phone
validation, current-account ordering, and duplicate-current validation remain
intact.

## Commit

`cc143571e67fc72c91b0dd54750a5e1f68c3c4a8` —
`security: replace plaintext account passwords with refs`

## Tests/output

- `pytest -q tests/test_accounts.py tests/test_multi_account.py` — **27 passed**
- `pytest -q` — **52 passed**

Added coverage for optional `credential_ref`, rejection of both plaintext
credential key spellings, and absence of password text from account repr/error
output.

## Concerns

`credential_ref` is intentionally only a reference; resolving secrets for a
login flow is outside this task and must not be implemented by persisting or
logging the secret.
