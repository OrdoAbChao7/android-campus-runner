"""Append-only, secret-safe evidence artifacts for a supervised run."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any


_SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|authorization|cookie|credential|api[-_ ]?key|access[-_ ]?key",
    re.IGNORECASE,
)
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_REDACTED = "[REDACTED]"
_BEARER_VALUE = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_NAMED_SECRET_VALUE = re.compile(
    r"\b(?P<label>password|passwd|secret|token|authorization|cookie|credential|api[-_ ]?key|access[-_ ]?key)"
    r"\s*(?P<separator>[:=])\s*[^\s,;]+",
    re.IGNORECASE,
)
_SPACED_SECRET_VALUE = re.compile(
    r"\b(?P<label>secret|token|credential)\s+[^\s,;]+",
    re.IGNORECASE,
)


def _sanitize_text(value: str) -> str:
    value = _BEARER_VALUE.sub(f"Bearer {_REDACTED}", value)
    value = _NAMED_SECRET_VALUE.sub(
        lambda match: f"{match.group('label')}{match.group('separator')}{_REDACTED}",
        value,
    )
    return _SPACED_SECRET_VALUE.sub(lambda match: f"{match.group('label')} {_REDACTED}", value)


def sanitize_evidence(value: Any) -> Any:
    """Return a JSON-compatible value with secret-bearing fields redacted."""
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if _SENSITIVE_KEY.search(str(key)) else sanitize_evidence(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_evidence(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return _sanitize_text(value)
    if value is None or isinstance(value, int | float | bool):
        return value
    return _sanitize_text(str(value))


class EvidenceWriter:
    """Writes sanitized JSONL events and immutable JSON snapshots below one run id."""

    def __init__(self, log_root: Path, run_id: str) -> None:
        if not _SAFE_NAME.fullmatch(run_id):
            raise ValueError("run_id must be a simple file name")
        self.run_dir = Path(log_root) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self.run_dir / "events.jsonl"
        self._lock = Lock()

    def append_event(self, event: str, payload: dict[str, object] | None = None) -> None:
        if not event:
            raise ValueError("event is required")
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "payload": sanitize_evidence(payload or {}),
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self._events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")

    def write_snapshot(self, name: str, payload: dict[str, object]) -> Path:
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError("snapshot name must be a simple file name")
        path = self.run_dir / f"{name}.json"
        document = {
            "captured_at": datetime.now(UTC).isoformat(),
            "payload": sanitize_evidence(payload),
        }
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return path
