"""Справочник почтовых индексов swisstopo (Amtliches Ortschaftenverzeichnis).

Нужен для привязки станций к кантонам. Загрузка идемпотентна по хэшу архива:
неизменившийся файл не обрабатывается повторно.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile

from evadvisor import http, rawstore
from evadvisor.config import source_cfg, timeout
from evadvisor.db import session
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
SOURCE = "postal"


def _parse(data: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(name).decode("utf-8-sig")
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        share = (r.get("Adressenanteil") or "").replace("%", "").strip()
        rows.append({
            "zip_id": int(r["ZIP_ID"]),
            "postal_code": r["PLZ4"].strip(),
            "locality": r["Ortschaftsname"].strip(),
            "municipality": r["Gemeindename"].strip(),
            "canton_code": r["Kantonskürzel"].strip(),
            "address_share": float(share) if share else None,
            "lon": float(r["E"]),
            "lat": float(r["N"]),
        })
    return rows


def run() -> None:
    cfg = source_cfg(SOURCE)
    with start_run(SOURCE, {"url": cfg["url"]}) as ctx:
        resp = http.get(cfg["url"], timeout(SOURCE))
        ctx.http_status = resp.status_code
        sha = rawstore.sha256_bytes(resp.content)
        if rawstore.already_loaded(SOURCE, sha):
            ctx.skip("postal directory unchanged")
        rawstore.save(ctx, resp.content, f"ortschaftenverzeichnis_{sha[:12]}.csv.zip")
        rows = _parse(resp.content)
        ctx.rows_received = len(rows)
        with session("engineer") as conn:
            cantons = {r["canton_code"] for r in conn.execute("SELECT canton_code FROM core.canton").fetchall()}
            good = [r for r in rows if r["canton_code"] in cantons]
            ctx.rows_rejected = len(rows) - len(good)   # Лихтенштейн (FL) и прочие не-кантоны
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO core.postal_code
                         (zip_id, postal_code, locality, municipality, canton_code, address_share, lat, lon)
                       VALUES (%(zip_id)s, %(postal_code)s, %(locality)s, %(municipality)s, %(canton_code)s,
                               %(address_share)s, %(lat)s, %(lon)s)
                       ON CONFLICT (zip_id) DO UPDATE SET
                         postal_code = EXCLUDED.postal_code, locality = EXCLUDED.locality,
                         municipality = EXCLUDED.municipality, canton_code = EXCLUDED.canton_code,
                         address_share = EXCLUDED.address_share, lat = EXCLUDED.lat, lon = EXCLUDED.lon""",
                    good,
                )
            ctx.rows_written = len(good)
