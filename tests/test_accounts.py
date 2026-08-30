"""Tests for accounts.py — credential file loading and ordering."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from android_runner.accounts import Account, load_accounts, ordered_enterprises


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "accounts.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_accounts
# ---------------------------------------------------------------------------

def test_load_minimal(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
            credential_ref: "env:ACCOUNT_A"
    """)
    accounts = load_accounts(f)
    assert len(accounts) == 1
    assert accounts[0].enterprise == "企业A"
    assert accounts[0].phone == "13800000001"
    assert accounts[0].credential_ref == "env:ACCOUNT_A"
    assert accounts[0].current is False


def test_load_multiple(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
            credential_ref: "env:A"
            current: true
          - enterprise: "企业B"
            phone: "13800000002"
            credential_ref: "env:B"
          - enterprise: "企业C"
            phone: "13800000003"
            credential_ref: "env:C"
    """)
    accounts = load_accounts(f)
    assert len(accounts) == 3
    assert accounts[0].current is True
    assert accounts[1].current is False


def test_load_without_credential_ref_is_allowed(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
    """)
    assert load_accounts(f)[0].credential_ref is None


def test_load_empty_enterprise_raises(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: ""
            phone: "13800000001"
            credential_ref: "env:A"
    """)
    with pytest.raises(ValueError, match="enterprise"):
        load_accounts(f)


def test_load_empty_accounts_list_raises(tmp_path):
    f = write_yaml(tmp_path, "accounts: []\n")
    with pytest.raises(ValueError, match="must not be empty"):
        load_accounts(f)


def test_load_missing_top_level_key_raises(tmp_path):
    f = write_yaml(tmp_path, "something: []\n")
    with pytest.raises(ValueError, match="accounts"):
        load_accounts(f)


def test_load_multiple_current_flags_raises(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
            credential_ref: "env:A"
            current: true
          - enterprise: "企业B"
            phone: "13800000002"
            credential_ref: "env:B"
            current: true
    """)
    with pytest.raises(ValueError, match="at most one"):
        load_accounts(f)


def test_load_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_accounts(Path("nonexistent_accounts.yaml"))


def test_load_strips_whitespace_from_enterprise(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "  企业A  "
            phone: "  13800000001  "
            credential_ref: "env:A"
    """)
    accounts = load_accounts(f)
    assert accounts[0].enterprise == "企业A"
    assert accounts[0].phone == "13800000001"


# ---------------------------------------------------------------------------
# ordered_enterprises
# ---------------------------------------------------------------------------

def test_ordered_no_anchor():
    accounts = [
        Account("企业A", "1", "env:A"),
        Account("企业B", "2", "env:B"),
        Account("企业C", "3", "env:C"),
    ]
    assert ordered_enterprises(accounts) == ["企业A", "企业B", "企业C"]


def test_ordered_current_flag_rotates_to_front():
    accounts = [
        Account("企业A", "1", "env:A"),
        Account("企业B", "2", "env:B", current=True),
        Account("企业C", "3", "env:C"),
    ]
    assert ordered_enterprises(accounts) == ["企业B", "企业C", "企业A"]


def test_ordered_start_kwarg_overrides_current_flag():
    accounts = [
        Account("企业A", "1", "env:A", current=True),
        Account("企业B", "2", "env:B"),
        Account("企业C", "3", "env:C"),
    ]
    assert ordered_enterprises(accounts, start="企业C") == ["企业C", "企业A", "企业B"]


def test_ordered_unknown_start_ignored():
    accounts = [
        Account("企业A", "1", "env:A"),
        Account("企业B", "2", "env:B"),
    ]
    assert ordered_enterprises(accounts, start="不存在") == ["企业A", "企业B"]


def test_plaintext_credential_key_is_rejected_without_secret_in_error(tmp_path):
    secret = "super-secret-value"
    f = write_yaml(tmp_path, f"""
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
            password: "{secret}"
    """)
    with pytest.raises(ValueError) as exc:
        load_accounts(f)
    assert secret not in str(exc.value)
    assert "credential_ref" in str(exc.value)


def test_passwd_key_is_rejected(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
            passwd: "secret"
    """)
    with pytest.raises(ValueError, match="credential_ref"):
        load_accounts(f)


def test_credential_ref_is_optional_and_repr_has_no_password(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
    """)
    account = load_accounts(f)[0]
    assert account.credential_ref is None
    assert "password" not in repr(account).lower()
