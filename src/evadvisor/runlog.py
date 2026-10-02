"""Журнал загрузок ops.load_run.

Строка создаётся в начале запуска (status = running) и обновляется в конце —
так на неё могут ссылаться строки stg/core, записанные во время запуска.
"""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from evadvisor.db import connect

log = logging.getLogger(__name__)


class SkipRun(Exception):
    """Запуск не нужен (данные уже загружены); фиксируется как status = skipped."""


@dataclass
class RunContext:
    source: str
    params: dict[str, Any]
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: str | None = None
    http_status: int | None = None
    attempts: int | None = None
    content_hash: str | None = None
    raw_path: str | None = None
    rows_received: int | None = None
    rows_written: int | None = None
    rows_duplicate: int | None = None
    rows_rejected: int | None = None
    errors: list[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        raise SkipRun(reason)


@contextmanager
def start_run(source: str, params: dict[str, Any] | None = None) -> Iterator[RunContext]:
    ctx = RunContext(source=source, params=params or {})
    with connect("engineer", autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ops.load_run (run_id, source, params) VALUES (%s, %s, %s)",
            (ctx.run_id, source, json.dumps(ctx.params, ensure_ascii=False, default=str)),
        )
    error_text: str | None = None
    try:
        yield ctx
        status = ctx.status or ("partial" if ctx.errors else "success")
        if ctx.errors:
            error_text = "; ".join(ctx.errors)[:4000]
    except SkipRun as exc:
        status, error_text = "skipped", str(exc)
    except BaseException as exc:
        status = "failed"
        error_text = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"[:4000]
        _finish(ctx, status, error_text)
        raise
    _finish(ctx, status, error_text)
    log.info("%s %s: %s", source, ctx.run_id, status)


def _finish(ctx: RunContext, status: str, error_text: str | None) -> None:
    with connect("engineer", autocommit=True) as conn:
        conn.execute(
            """
            UPDATE ops.load_run SET
                status = %(status)s, finished_at = now(),
                http_status = coalesce(%(http_status)s, http_status),
                attempts = coalesce(%(attempts)s, attempts),
                content_hash = coalesce(%(content_hash)s, content_hash),
                raw_path = coalesce(%(raw_path)s, raw_path),
                rows_received = coalesce(%(rows_received)s, rows_received),
                rows_written = coalesce(%(rows_written)s, rows_written),
                rows_duplicate = coalesce(%(rows_duplicate)s, rows_duplicate),
                rows_rejected = coalesce(%(rows_rejected)s, rows_rejected),
                error_text = %(error_text)s
            WHERE run_id = %(run_id)s
            """,
            {
                "status": status, "http_status": ctx.http_status, "attempts": ctx.attempts,
                "content_hash": ctx.content_hash, "raw_path": ctx.raw_path,
                "rows_received": ctx.rows_received, "rows_written": ctx.rows_written,
                "rows_duplicate": ctx.rows_duplicate, "rows_rejected": ctx.rows_rejected,
                "error_text": error_text, "run_id": ctx.run_id,
            },
        )
