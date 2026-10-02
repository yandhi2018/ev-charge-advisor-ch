"""Общие функции загрузчиков: массовая запись в staging и реестр структуры источников."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any

import psycopg

from evadvisor.runlog import RunContext

log = logging.getLogger(__name__)


def copy_rows(conn: psycopg.Connection, table: str, columns: Sequence[str], types: Sequence[str],
              rows: Iterable[Sequence[Any]]) -> int:
    """COPY строк в таблицу; types — имена типов PostgreSQL для корректной передачи массивов и NULL."""
    n = 0
    cols = ", ".join(columns)
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN") as cp:
        cp.set_types(list(types))
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


def field_paths(obj: Any, prefix: str = "") -> set[str]:
    """Набор путей полей JSON (без индексов массивов): описывает структуру источника."""
    paths: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= field_paths(value, path)
    elif isinstance(obj, list):
        for item in obj[:50]:
            paths |= field_paths(item, prefix + "[]")
    return paths


def register_schema(conn: psycopg.Connection, ctx: RunContext, records: Iterable[dict]) -> bool:
    """Записать структуру выгрузки в ops.source_schema. True — структура новая (изменение схемы источника)."""
    paths: set[str] = set()
    for i, rec in enumerate(records):
        paths |= field_paths(rec)
        if i > 2000:
            break
    fields = sorted(paths)
    digest = hashlib.sha256("\n".join(fields).encode()).hexdigest()
    known = conn.execute("SELECT count(*) AS n FROM ops.source_schema WHERE source = %s", (ctx.source,)).fetchone()
    cur = conn.execute(
        """INSERT INTO ops.source_schema (source, fields_hash, fields, first_run_id)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (source, fields_hash) DO UPDATE SET last_seen = now()
           RETURNING (xmax = 0) AS inserted""",
        (ctx.source, digest, json.dumps(fields), ctx.run_id),
    )
    inserted = bool(cur.fetchone()["inserted"])
    if inserted and known["n"] > 0:
        log.warning("Источник %s изменил структуру полей (hash %s)", ctx.source, digest[:12])
        ctx.errors.append("source schema changed")
    return inserted
