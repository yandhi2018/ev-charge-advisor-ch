"""Погода Open-Meteo по опорным точкам 26 кантонов.

Три вида данных (core.weather_hourly.kind):
- actual        — фактическая погода (Archive API), публикуется с задержкой ~3 дня;
- hist_forecast — архив прогнозов (Historical Forecast API): то, что было известно заранее;
                  используется на тесте моделей вместо факта, чтобы не завышать качество;
- forecast      — текущий прогноз (Forecast API) для приложения водителя.

Инкрементальность: для actual и hist_forecast запрашивается период от последнего
загруженного часа кантона; ошибка по кантону не останавливает остальные (status = partial).
Идемпотентность: (canton_code, hour_utc, kind) — вставка с обновлением.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from evadvisor import http, rawstore
from evadvisor.config import settings, source_cfg, timeout
from evadvisor.db import connect, session
from evadvisor.ingestion.common import copy_rows
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
SOURCE = "weather"
VAR_MAP = {"temperature_2m": 4, "precipitation": 5, "snowfall": 6, "wind_speed_10m": 7}
COLUMNS = ["run_id", "canton_code", "hour_utc", "kind", "temperature_c", "precipitation_mm",
           "snowfall_cm", "wind_kmh"]
TYPES = ["uuid", "text", "timestamptz", "text", "float4", "float4", "float4", "float4"]


def _last_hour(canton: str, kind: str) -> datetime | None:
    with connect("engineer") as conn:
        row = conn.execute(
            "SELECT max(hour_utc) AS h FROM core.weather_hourly WHERE canton_code = %s AND kind = %s",
            (canton, kind),
        ).fetchone()
    return row["h"] if row else None


def _rows(run_id, canton: str, kind: str, payload: dict) -> list[list]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    out = []
    for i, t in enumerate(times):
        row = [run_id, canton, datetime.fromisoformat(t).replace(tzinfo=UTC), kind, None, None, None, None]
        for var, pos in VAR_MAP.items():
            values = hourly.get(var) or []
            row[pos] = values[i] if i < len(values) else None
        out.append(row)
    return out


def _periods(start: date, end: date, chunk_days: int):
    while start <= end:
        stop = min(end, start + timedelta(days=chunk_days - 1))
        yield start, stop
        start = stop + timedelta(days=1)


def _load_history(kind: str) -> None:
    cfg = source_cfg(SOURCE)
    url = cfg["archive_url"] if kind == "actual" else cfg["historical_forecast_url"]
    end = date.today() - timedelta(days=cfg["publication_delay_days"] if kind == "actual" else 1)
    backfill = date.fromisoformat(cfg["backfill_from"])
    with start_run(SOURCE, {"kind": kind, "end": end.isoformat()}) as ctx:
        received = 0
        for code, point in settings()["cantons"].items():
            last = _last_hour(code, kind)
            start = (last + timedelta(hours=1)).date() if last else backfill
            if start > end:
                continue
            try:
                for p_from, p_to in _periods(start, end, cfg["chunk_days"]):
                    resp = http.get(url, timeout(SOURCE), params={
                        "latitude": point["lat"], "longitude": point["lon"],
                        "start_date": p_from.isoformat(), "end_date": p_to.isoformat(),
                        "hourly": ",".join(cfg["hourly_variables"]), "timezone": "UTC",
                        "wind_speed_unit": "kmh",
                    })
                    payload = resp.json()
                    rawstore.save_json(ctx, payload, f"{kind}_{code}_{p_from}_{p_to}", subdir=kind)
                    rows = _rows(ctx.run_id, code, kind, payload)
                    received += len(rows)
                    with session("engineer") as conn:
                        copy_rows(conn, "stg.weather_hourly", COLUMNS, TYPES, rows)
            except Exception as exc:  # один кантон не останавливает остальные
                log.warning("weather %s %s: %s", kind, code, exc)
                ctx.errors.append(f"{code}: {exc}")
        ctx.rows_received = received
        if received == 0 and not ctx.errors:
            ctx.skip("weather history is up to date")
        with session("engineer") as conn:
            conn.execute("CALL core.sp_apply_weather(%s)", (ctx.run_id,))


def load_forecast(max_age_minutes: int | None = None) -> None:
    """Текущий прогноз на 2 суток (и вчерашний день) для всех кантонов."""
    cfg = source_cfg(SOURCE)
    if max_age_minutes is not None:
        with connect("engineer") as conn:
            row = conn.execute(
                "SELECT max(loaded_at) AS t FROM core.weather_hourly WHERE kind = 'forecast'").fetchone()
        if row and row["t"] and datetime.now(UTC) - row["t"] <= timedelta(minutes=max_age_minutes):
            return
    cantons = settings()["cantons"]
    with start_run(SOURCE, {"kind": "forecast"}) as ctx:
        resp = http.get(cfg["forecast_url"], timeout(SOURCE), params={
            "latitude": ",".join(str(p["lat"]) for p in cantons.values()),
            "longitude": ",".join(str(p["lon"]) for p in cantons.values()),
            "hourly": ",".join(cfg["hourly_variables"]), "timezone": "UTC",
            "past_days": 1, "forecast_days": 2, "wind_speed_unit": "kmh",
        })
        payload = resp.json()
        payload = payload if isinstance(payload, list) else [payload]
        rawstore.save_json(ctx, payload, "forecast", subdir="forecast")
        rows = []
        for code, item in zip(cantons.keys(), payload, strict=True):
            rows += _rows(ctx.run_id, code, "forecast", item)
        ctx.rows_received = len(rows)
        with session("engineer") as conn:
            copy_rows(conn, "stg.weather_hourly", COLUMNS, TYPES, rows)
            conn.execute("CALL core.sp_apply_weather(%s)", (ctx.run_id,))


def run(kind: str = "all") -> None:
    kinds = ["actual", "hist_forecast", "forecast"] if kind == "all" else [kind]
    for k in kinds:
        if k == "forecast":
            load_forecast()
        else:
            _load_history(k)
