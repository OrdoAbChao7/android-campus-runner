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
            password: "pass1"
    """)
    accounts = load_accounts(f)
    assert len(accounts) == 1
    assert accounts[0].enterprise == "企业A"
    assert accounts[0].phone == "13800000001"
    assert accounts[0].password == "pass1"
    assert accounts[0].current is False


def test_load_multiple(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
            password: "p1"
            current: true
          - enterprise: "企业B"
            phone: "13800000002"
            password: "p2"
          - enterprise: "企业C"
            phone: "13800000003"
            password: "p3"
    """)
    accounts = load_accounts(f)
    assert len(accounts) == 3
    assert accounts[0].current is True
    assert accounts[1].current is False


def test_load_missing_required_field_raises(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: "企业A"
            phone: "13800000001"
    """)
    with pytest.raises(ValueError, match="password"):
        load_accounts(f)


def test_load_empty_enterprise_raises(tmp_path):
    f = write_yaml(tmp_path, """
        accounts:
          - enterprise: ""
            phone: "13800000001"
            password: "p1"
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
            password: "p1"
            current: true
          - enterprise: "企业B"
            phone: "13800000002"
            password: "p2"
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
            password: "p1"
    """)
    accounts = load_accounts(f)
    assert accounts[0].enterprise == "企业A"
    assert accounts[0].phone == "13800000001"


# ---------------------------------------------------------------------------
# ordered_enterprises
# ---------------------------------------------------------------------------

def test_ordered_no_anchor():
    accounts = [
        Account("企业A", "1", "p"),
        Account("企业B", "2", "p"),
        Account("企业C", "3", "p"),
    ]
    assert ordered_enterprises(accounts) == ["企业A", "企业B", "企业C"]


def test_ordered_current_flag_rotates_to_front():
    accounts = [
        Account("企业A", "1", "p"),
        Account("企业B", "2", "p", current=True),
        Account("企业C", "3", "p"),
    ]
    assert ordered_enterprises(accounts) == ["企业B", "企业C", "企业A"]


def test_ordered_start_kwarg_overrides_current_flag():
    accounts = [
        Account("企业A", "1", "p", current=True),
        Account("企业B", "2", "p"),
        Account("企业C", "3", "p"),
    ]
    assert ordered_enterprises(accounts, start="企业C") == ["企业C", "企业A", "企业B"]


def test_ordered_unknown_start_ignored():
    accounts = [
        Account("企业A", "1", "p"),
        Account("企业B", "2", "p"),
    ]
    assert ordered_enterprises(accounts, start="不存在") == ["企业A", "企业B"]
