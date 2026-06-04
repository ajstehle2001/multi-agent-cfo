"""Structured logging with correlation IDs.

Every log record automatically carries:
  - run_id: unique 12-char ID per scheduler invocation
  - client: ticker symbol when inside a per-client pipeline (or None)

Correlation context is propagated via `contextvars.ContextVar` so we
don't have to thread IDs through every function signature. Use the
`run_context` and `client_context` context managers to set/reset.

Output strategy:
  - Human-readable lines to stderr (so demos stay watchable).
  - JSONL records to logs/run-<timestamp>.jsonl (so machine tooling
    can parse without string matching).

The interactive CFO memo display and approve/reject prompt remain as
print() to stdout — those are UI, not logs, and shouldn't be mixed
with operational telemetry.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# --- Correlation context ----------------------------------------------------

_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
_client_var: ContextVar[str | None] = ContextVar("client", default=None)


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    """Bind a run_id to the current execution context."""
    token = _run_id_var.set(run_id)
    try:
        yield
    finally:
        _run_id_var.reset(token)


@contextmanager
def client_context(ticker: str) -> Iterator[None]:
    """Bind a client ticker to the current execution context."""
    token = _client_var.set(ticker)
    try:
        yield
    finally:
        _client_var.reset(token)


# --- Filter & formatter -----------------------------------------------------

class CorrelationFilter(logging.Filter):
    """Inject run_id and client ticker into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get()
        record.client = _client_var.get()
        return True


# Standard LogRecord attributes we don't want to duplicate as 'extra' fields.
_STANDARD_RECORD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
})


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record (JSONL).

    The schema is stable so downstream tooling (grep/jq/pandas) can
    parse without regex. Any extra={...} kwargs passed to the logger
    are merged into the record.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
                  .isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "client": getattr(record, "client", None),
        }
        for key, value in record.__dict__.items():
            if (
                key not in _STANDARD_RECORD_ATTRS
                and key not in payload
                and not key.startswith("_")
            ):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class HumanFormatter(logging.Formatter):
    """Compact human-readable line with correlation context.

    Format: HH:MM:SS LEVEL [run=xxxx client=COST] message
    The run_id is truncated to 8 chars for readability — full ID
    remains in the JSONL log if precise correlation is needed.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)\
                     .strftime("%H:%M:%SZ")
        run_id = getattr(record, "run_id", None)
        client = getattr(record, "client", None)
        ctx_parts = []
        if run_id:
            ctx_parts.append(f"run={run_id[:8]}")
        if client:
            ctx_parts.append(f"client={client}")
        ctx = f"[{' '.join(ctx_parts)}] " if ctx_parts else ""
        msg = record.getMessage()
        return f"{ts} {record.levelname:<7} {ctx}{msg}"


# --- Configuration entry point ---------------------------------------------

def configure_logging(
    log_dir: Path = Path("logs"),
    stderr_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> Path:
    """Wire up dual-output logging. Returns the JSONL file path.

    Safe to call multiple times — clears any existing handlers on the
    root logger before attaching new ones (avoids duplicate output if
    a caller re-initialises).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"run-{timestamp}.jsonl"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    human = logging.StreamHandler(sys.stderr)
    human.setLevel(stderr_level)
    human.setFormatter(HumanFormatter())
    human.addFilter(CorrelationFilter())
    root.addHandler(human)

    json_handler = logging.FileHandler(log_path, encoding="utf-8")
    json_handler.setLevel(file_level)
    json_handler.setFormatter(JsonFormatter())
    json_handler.addFilter(CorrelationFilter())
    root.addHandler(json_handler)

    return log_path