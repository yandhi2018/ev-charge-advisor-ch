"""Справочник зарядных точек EVSEData (ich-tanke-strom.ch / BFE, формат OICP).

Инкрементальность: выгрузка с тем же хэшем, что у последней успешной, не обрабатывается.
Изменения конкретных точек выявляются процедурой core.sp_apply_evse_data по хэшу
значимых полей и сохраняются в SCD2-историю триггером.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from evadvisor import http, rawstore
from evadvisor.config import source_cfg, timeout
from evadvisor.db import session
from evadvisor.ingestion.common import copy_rows, register_schema
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
SOURCE = "evse_data"

COLUMNS = [
    "run_id", "evse_id", "operator_id", "operator_name", "station_ext_id", "pool_ext_id", "station_name",
    "street", "postal_code", "city", "country", "lat", "lon", "accessibility", "access_location",
    "is_open_24h", "plugs", "power_kw", "power_type", "auth_modes", "last_update", "record_hash",
]
TYPES = [
    "uuid", "text", "text", "text", "text", "text", "text",
    "text", "text", "text", "text", "float8", "float8", "text", "text",
    "bool", "text[]", "numeric", "text", "text[]", "timestamptz", "bpchar",
]
POWER_TYPES = {"AC_1_PHASE", "AC_3_PHASE", "DC"}


def _coords(rec: dict) -> tuple[float | None, float | None]:
    geo = rec.get("GeoCoordinates") or {}
    google = geo.get("Google")
    try:
        if google:
            lat, lon = google.replace(",", " ").split()[:2]
            return float(lat), float(lon)
        dec = geo.get("DecimalDegree")
        if dec:
            return float(dec["Latitude"]), float(dec["Longitude"])
    except (ValueError, KeyError, TypeError):
        pass
    return None, None


def _power(rec: dict) -> tuple[float | None, str | None]:
    """Максимальная мощность среди ChargingFacilities точки и её тип тока."""
    best_kw, best_type = None, None
    for cf in rec.get("ChargingFacilities") or []:
        try:
            kw = float(cf.get("power") or cf.get("Power") or 0) or None
        except (TypeError, ValueError):
            kw = None
        ptype = (cf.get("powertype") or cf.get("PowerType") or "").upper() or None
        if ptype not in POWER_TYPES:
            ptype = None
        if kw is not None and kw > 1000:   # встречаются ватты вместо киловатт
            kw = kw / 1000
        if best_kw is None or (kw or 0) > best_kw:
            best_kw, best_type = kw, ptype or best_type
        elif best_type is None:
            best_type = ptype
    return best_kw, best_type


def _name(rec: dict) -> str | None:
    names = {n.get("lang"): n.get("value") for n in rec.get("ChargingStationNames") or [] if isinstance(n, dict)}
    for lang in ("de", "fr", "it", "en"):
        if names.get(lang):
            return names[lang].strip()
    return None


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def to_row(run_id, operator: dict, rec: dict) -> list[Any]:
    addr = rec.get("Address") or {}
    lat, lon = _coords(rec)
    power_kw, power_type = _power(rec)
    plugs = sorted({p for p in rec.get("Plugs") or [] if p})
    significant = {
        "op": operator.get("OperatorID"), "station": rec.get("ChargingStationId"),
        "street": addr.get("Street"), "plz": addr.get("PostalCode"), "city": addr.get("City"),
        "lat": round(lat, 6) if lat else None, "lon": round(lon, 6) if lon else None,
        "plugs": plugs, "kw": power_kw, "type": power_type,
        "access": rec.get("Accessibility"), "h24": rec.get("IsOpen24Hours"),
    }
    record_hash = hashlib.sha256(json.dumps(significant, sort_keys=True, default=str).encode()).hexdigest()
    return [
        run_id, (rec.get("EvseID") or "").strip() or None, operator.get("OperatorID"),
        operator.get("OperatorName"), rec.get("ChargingStationId"),
        rec.get("ChargingPoolID") or rec.get("ChargingPoolId"), _name(rec),
        (addr.get("Street") or "").strip() or None, (addr.get("PostalCode") or "").strip() or None,
        (addr.get("City") or "").strip() or None, addr.get("Country"), lat, lon,
        rec.get("Accessibility"), rec.get("AccessibilityLocation"),
        bool(rec.get("IsOpen24Hours")) if rec.get("IsOpen24Hours") is not None else None,
        plugs, power_kw, power_type, rec.get("AuthenticationModes") or [],
        _ts(rec.get("lastUpdate")), record_hash,
    ]


def iter_records(payload: dict):
    for operator in payload.get("EVSEData") or []:
        for rec in operator.get("EVSEDataRecord") or []:
            yield operator, rec


def run() -> None:
    cfg = source_cfg(SOURCE)
    with start_run(SOURCE, {"url": cfg["url"]}) as ctx:
        resp = http.get(cfg["url"], timeout(SOURCE))
        ctx.http_status = resp.status_code
        sha = rawstore.sha256_bytes(resp.content)
        if rawstore.already_loaded(SOURCE, sha):
            ctx.skip("EVSEData unchanged since last successful load")
        rawstore.save(ctx, gzip.compress(resp.content), f"evse_data_{sha[:12]}.json.gz", content_sha=sha)
        payload = resp.json()
        records = list(iter_records(payload))
        ctx.rows_received = len(records)
        with session("engineer") as conn:
            register_schema(conn, ctx, (r for _, r in records))
            copy_rows(conn, "stg.evse_data", COLUMNS, TYPES, (to_row(ctx.run_id, op, r) for op, r in records))
            conn.execute("CALL core.sp_apply_evse_data(%s)", (ctx.run_id,))
        log.info("EVSEData: %s records", len(records))
