"""Load and validate the accounts credential file (config/accounts.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Account:
    enterprise: str
    phone: str
    password: str
    current: bool = False

    def __post_init__(self) -> None:
        if not self.enterprise:
            raise ValueError("account 'enterprise' must not be empty")
        if not self.phone:
            raise ValueError("account 'phone' must not be empty")
        if not self.password:
            raise ValueError("account 'password' must not be empty")


def load_accounts(path: str | Path) -> list[Account]:
    """Parse *path* and return a list of :class:`Account` objects.

    The file must be YAML with a top-level ``accounts`` list.  Each entry
    must have ``enterprise``, ``phone``, and ``password`` keys.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValueError
        If the file structure is invalid or a required field is missing.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list):
        raise ValueError(f"{path}: top-level 'accounts' list is required")

    accounts: list[Account] = []
    for i, entry in enumerate(raw["accounts"]):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: accounts[{i}] must be a mapping")
        missing = [k for k in ("enterprise", "phone", "password") if not entry.get(k)]
        if missing:
            raise ValueError(f"{path}: accounts[{i}] missing required fields: {missing}")
        accounts.append(Account(
            enterprise=str(entry["enterprise"]).strip(),
            phone=str(entry["phone"]).strip(),
            password=str(entry["password"]),
            current=bool(entry.get("current", False)),
        ))

    if not accounts:
        raise ValueError(f"{path}: 'accounts' list must not be empty")

    current_flags = [a for a in accounts if a.current]
    if len(current_flags) > 1:
        raise ValueError(f"{path}: at most one account may have current: true")

    return accounts


def ordered_enterprises(accounts: list[Account], *, start: str | None = None) -> list[str]:
    """Return enterprise names in run order.

    If *start* is given (or one account has ``current: true``), that account
    is placed first and the rest follow in their original order.
    """
    names = [a.enterprise for a in accounts]
    anchor = start or next((a.enterprise for a in accounts if a.current), None)
    if anchor and anchor in names:
        idx = names.index(anchor)
        names = names[idx:] + names[:idx]
    return names
