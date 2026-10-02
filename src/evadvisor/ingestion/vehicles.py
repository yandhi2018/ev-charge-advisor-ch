"""Каталог электромобилей: Open EV Data (KilowattApp, MIT с указанием источника) + собственный CSV.

Из каталога берутся только легковые автомобили. Собственный каталог
(config/vehicles_manual.csv) имеет приоритет при совпадении модели.
"""

from __future__ import annotations

import csv
import logging

from evadvisor import http, rawstore
from evadvisor.config import ROOT, source_cfg, timeout
from evadvisor.db import session
from evadvisor.ingestion.common import copy_rows, register_schema
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
SOURCE = "vehicles"
COLUMNS = ["run_id", "source", "ext_id", "brand", "model", "variant", "release_year", "battery_kwh",
           "ac_max_kw", "dc_max_kw", "plugs"]
TYPES = ["uuid", "text", "text", "text", "text", "text", "int4", "numeric", "numeric", "numeric", "text[]"]


def _num(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_open_ev_data(run_id, payload: dict) -> list[list]:
    rows = []
    for v in payload.get("data") or []:
        if v.get("vehicle_type", "car") != "car":
            continue
        ac = v.get("ac_charger") or {}
        dc = v.get("dc_charger") or {}
        ports = sorted(set((ac.get("ports") or []) + (dc.get("ports") or [])))
        rows.append([
            run_id, "open-ev-data", v.get("id"), (v.get("brand") or "").strip(), (v.get("model") or "").strip(),
            (v.get("variant") or "").strip(), v.get("release_year"), _num(v.get("usable_battery_size")),
            _num(ac.get("max_power")), _num(dc.get("max_power")) if dc else None, ports,
        ])
    return rows


def parse_manual(run_id) -> list[list]:
    path = ROOT / source_cfg(SOURCE)["manual_csv"]
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([
                run_id, "manual", None, r["brand"].strip(), r["model"].strip(), r["variant"].strip(),
                int(r["release_year"]) if r["release_year"] else None, _num(r["battery_kwh"]),
                _num(r["ac_max_kw"]), _num(r["dc_max_kw"]),
                [p.strip() for p in r["ports"].split("|") if p.strip()],
            ])
    return rows


def run() -> None:
    cfg = source_cfg(SOURCE)
    with start_run(SOURCE, {"url": cfg["open_ev_data_url"], "manual": cfg["manual_csv"]}) as ctx:
        resp = http.get(cfg["open_ev_data_url"], timeout(SOURCE))
        ctx.http_status = resp.status_code
        manual_bytes = (ROOT / cfg["manual_csv"]).read_bytes()
        sha = rawstore.sha256_bytes(resp.content + manual_bytes)
        if rawstore.already_loaded(SOURCE, sha):
            ctx.skip("vehicle catalogue unchanged")
        rawstore.save(ctx, resp.content, f"open_ev_data_{sha[:12]}.json", content_sha=sha)
        payload = resp.json()
        rows = parse_open_ev_data(ctx.run_id, payload) + parse_manual(ctx.run_id)
        ctx.rows_received = len(rows)
        with session("engineer") as conn:
            register_schema(conn, ctx, payload.get("data") or [])
            copy_rows(conn, "stg.vehicle", COLUMNS, TYPES, rows)
            conn.execute("CALL core.sp_apply_vehicles(%s)", (ctx.run_id,))
