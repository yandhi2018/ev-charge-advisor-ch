"""Снимок текущих статусов точек (EVSEStatus).

Источник отдаёт только текущий срез без времени. Снимок привязывается к 5-минутному
слоту; если слот уже загружен, запуск пропускается без обращения к источнику
(идемпотентность). Вызывается приложением при поиске и по расписанию.
"""

from __future__ import annotations

import gzip
import logging
from datetime import UTC, datetime, timedelta

from evadvisor import http, rawstore
from evadvisor.config import source_cfg, timeout
from evadvisor.db import connect, session
from evadvisor.ingestion.common import copy_rows, register_schema
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
SOURCE = "evse_status"


def current_slot(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)


def latest_slot() -> datetime | None:
    with connect("engineer") as conn:
        row = conn.execute("SELECT max(slot_ts) AS s FROM core.status_snapshot").fetchone()
    return row["s"] if row else None


def slot_loaded(slot: datetime) -> bool:
    with connect("engineer") as conn:
        row = conn.execute(
            """SELECT 1 FROM ops.load_run WHERE source = %s AND status = 'success'
                AND params->>'slot' = %s LIMIT 1""",
            (SOURCE, slot.isoformat()),
        ).fetchone()
    return row is not None


def run(max_age_minutes: int | None = None) -> datetime | None:
    """Загрузить снимок текущего слота. Возвращает слот последнего доступного снимка.

    max_age_minutes: если последний снимок не старше этого значения — запрос не выполняется.
    """
    slot = current_slot()
    if max_age_minutes is not None:
        last = latest_slot()
        if last and datetime.now(UTC) - last <= timedelta(minutes=max_age_minutes):
            return last
    cfg = source_cfg(SOURCE)
    with start_run(SOURCE, {"slot": slot.isoformat()}) as ctx:
        if slot_loaded(slot):
            ctx.skip(f"slot {slot.isoformat()} already loaded")
        resp = http.get(cfg["url"], timeout(SOURCE))
        ctx.http_status = resp.status_code
        payload = resp.json()
        rawstore.save(ctx, gzip.compress(resp.content), f"status_{slot:%Y%m%dT%H%M}Z.json.gz",
                      subdir=f"{slot:%Y-%m-%d}", content_sha=rawstore.sha256_bytes(resp.content))
        rows = [
            (ctx.run_id, slot, rec.get("EvseID"), rec.get("EVSEStatus"), op.get("OperatorID"))
            for op in payload.get("EVSEStatuses") or []
            for rec in op.get("EVSEStatusRecord") or []
        ]
        ctx.rows_received = len(rows)
        with session("engineer") as conn:
            register_schema(conn, ctx, (op for op in payload.get("EVSEStatuses") or []))
            copy_rows(conn, "stg.evse_status", ["run_id", "slot_ts", "evse_id", "status", "operator_id"],
                      ["uuid", "timestamptz", "text", "text", "text"], rows)
            conn.execute("CALL core.sp_apply_status_snapshot(%s)", (ctx.run_id,))
            conn.execute("CALL mart.sp_refresh_live_occupancy(%s)", (slot - timedelta(hours=2),))
    return latest_slot()
