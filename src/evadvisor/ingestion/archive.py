"""Архив статусов 2024–2025 (Swiss-public-charging-dataset, первоисточник BFE / opendata.swiss).

Поток на один месяц:
  raw   data/raw/archive/YYYY-MM_charging.7z           (как в источнике, sha256 в ops.raw_manifest)
  stg   data/lake/stg/status_5min/month=YYYY-MM/        (5-минутная сетка, дедупликация)
  core  data/lake/core/status_episode/month=YYYY-MM/    (эпизоды постоянного статуса)
  mart  PostgreSQL mart.occupancy_hourly, mart.archive_coverage; профили — после всех месяцев

Инкрементальность: обрабатываются только месяцы без успешного запуска с тем же хэшем файла.
Идемпотентность: витрины месяца перед записью удаляются (DELETE + INSERT в транзакции).
Тяжёлые вычисления выполняет DuckDB по Parquet; в PostgreSQL попадают только агрегаты.
"""

from __future__ import annotations

import csv
import io
import logging
import shutil
from datetime import date
from pathlib import Path

import duckdb
import py7zr

from evadvisor import http, rawstore
from evadvisor.config import ROOT, data_dir, lake_dir, raw_dir, source_cfg, timeout
from evadvisor.db import connect, session
from evadvisor.ingestion.common import copy_rows
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
SOURCE = "archive"
SQL_DIR = ROOT / "db" / "duckdb"
N_PARTS = 8


def months(cfg: dict) -> list[str]:
    y, m = map(int, cfg["months_from"].split("-"))
    y2, m2 = map(int, cfg["months_to"].split("-"))
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def duck() -> duckdb.DuckDBPyConnection:
    tmp = data_dir() / "duckdb_tmp"
    tmp.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"SET temp_directory = '{tmp.as_posix()}'")
    con.execute("SET memory_limit = '6GB'")
    con.execute("SET preserve_insertion_order = false")
    return con


def _sql(name: str, **params: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8").format(**params)


def _download(url: str, dest: Path) -> None:
    session_ = http.make_session()
    with session_.get(url, stream=True, timeout=timeout(SOURCE)) as resp:
        resp.raise_for_status()
        part = dest.with_suffix(dest.suffix + ".part")
        with open(part, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
        part.replace(dest)


def load_details() -> None:
    """ChargingStationDetails (февраль 2026) → core.archive_evse вместе с текущим справочником."""
    cfg = source_cfg(SOURCE)
    with start_run("archive_details", {"url": cfg["details_url"]}) as ctx:
        resp = http.get(cfg["details_url"], timeout(SOURCE))
        ctx.http_status = resp.status_code
        sha = rawstore.sha256_bytes(resp.content)
        with connect("engineer") as conn:
            n_evse = conn.execute("SELECT count(*) AS n FROM core.evse").fetchone()["n"]
            built = conn.execute(
                """SELECT max(r.finished_at) AS t FROM ops.load_run r WHERE r.source = 'archive_details'
                    AND r.status = 'success' AND r.content_hash = %s""", (sha,)).fetchone()["t"]
            last_ref = conn.execute(
                """SELECT max(finished_at) AS t FROM ops.load_run
                    WHERE source = 'evse_data' AND status IN ('success', 'partial')""").fetchone()["t"]
        if built and (last_ref is None or last_ref < built) and n_evse > 0:
            ctx.skip("archive EVSE metadata is up to date")
        rawstore.save(ctx, resp.content, f"ChargingStationDetails_{sha[:12]}.csv", content_sha=sha)
        rows = []
        for r in csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))):
            try:
                lat, lon = (float(x) for x in (r.get("GeoCoordinates") or "").split()[:2])
            except ValueError:
                lat = lon = None
            try:
                kw = float(r.get("ChargingFacility_power") or "") or None
            except ValueError:
                kw = None
            rows.append([ctx.run_id, (r.get("EvseID") or "").strip() or None, lat, lon,
                         (r.get("PostalCode") or "").strip() or None, kw,
                         (r.get("ChargingFacility_powertype") or "").strip() or None])
        ctx.rows_received = len(rows)
        with session("engineer") as conn:
            copy_rows(conn, "stg.archive_details",
                      ["run_id", "evse_id", "lat", "lon", "postal_code", "power_kw", "power_type"],
                      ["uuid", "text", "float8", "float8", "text", "numeric", "text"], rows)
            conn.execute("CALL core.sp_build_archive_evse(%s)", (ctx.run_id,))


def _dim(con: duckdb.DuckDBPyConnection) -> None:
    with connect("engineer") as conn:
        rows = conn.execute("SELECT evse_id, canton_code, power_class FROM core.archive_evse").fetchall()
    import pandas as pd

    con.register("dim_df", pd.DataFrame(rows, columns=["evse_id", "canton_code", "power_class"]))
    con.execute("CREATE OR REPLACE TABLE dim AS SELECT * FROM dim_df")


def _month_done(month: str, sha: str) -> bool:
    with connect("engineer") as conn:
        row = conn.execute(
            """SELECT 1 FROM ops.load_run WHERE source = %s AND status IN ('success', 'partial')
                AND params->>'month' = %s AND content_hash = %s LIMIT 1""",
            (SOURCE, month, sha)).fetchone()
    return row is not None


def _copy_parquet_to_pg(con, conn, parquet: Path, table: str, columns: list[str],
                        select: list[str] | None = None) -> int:
    """Перенос результата DuckDB в PostgreSQL через CSV-поток COPY.

    columns — целевые колонки таблицы; select — выражения DuckDB в том же порядке (по умолчанию = columns).
    """
    csv_path = parquet.with_suffix(".csv")
    exprs = ", ".join(select or columns)
    con.execute(f"COPY (SELECT {exprs} FROM read_parquet('{parquet.as_posix()}')) "
                f"TO '{csv_path.as_posix()}' (HEADER false)")
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv)") as cp, \
                open(csv_path, "rb") as f:
            while block := f.read(1 << 20):
                cp.write(block)
        n = cur.rowcount
    csv_path.unlink(missing_ok=True)
    return n


def process_month(month: str, force: bool = False) -> None:
    cfg = source_cfg(SOURCE)
    url = f"{cfg['base_url']}/{cfg['file_pattern'].format(month=month)}"
    with start_run(SOURCE, {"month": month, "url": url}) as ctx:
        dest = raw_dir(SOURCE) / f"{month}_charging.7z"
        if not dest.exists():
            _download(url, dest)
        sha = rawstore.sha256_file(dest)
        if not force and _month_done(month, sha):
            ctx.skip(f"month {month} already processed")
        rawstore.register(ctx, dest, sha)
        if dest.stat().st_size == 0:
            ctx.status = "partial"
            ctx.errors.append(f"source file for {month} is empty")
            _write_coverage(ctx, month, None)
            return

        work = data_dir() / "tmp" / month
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        with py7zr.SevenZipFile(dest) as z:
            z.extractall(work)
        src = next(work.rglob("*.parquet"))

        stg_dir = lake_dir("stg", "status_5min", f"month={month}")
        epi_dir = lake_dir("core", "status_episode", f"month={month}")
        for d in (stg_dir, epi_dir):            # повторная обработка месяца перезаписывает его целиком
            for f in d.glob("*.parquet"):
                f.unlink()
        agg_dir = lake_dir("mart_parts", f"month={month}")
        occ, prof, cov = (agg_dir / "occupancy.parquet", agg_dir / "profile_parts.parquet",
                          agg_dir / "coverage.parquet")

        con = duck()
        ctx.rows_received = con.sql(f"SELECT count(*) FROM read_parquet('{src.as_posix()}')").fetchone()[0]
        for part in range(N_PARTS):   # части по хэшу EvseID: ограничивает память DuckDB
            stg_part = stg_dir / f"part-{part}.parquet"
            con.execute(_sql("01_status_5min.sql", src=src.as_posix(), dst=stg_part.as_posix(),
                             part=str(part), nparts=str(N_PARTS)))
            con.execute(_sql("02_status_episode.sql", src=stg_part.as_posix(),
                             dst=(epi_dir / f"part-{part}.parquet").as_posix()))
        stg_glob = (stg_dir / "part-*.parquet").as_posix()
        ctx.rows_written = con.sql(f"SELECT count(*) FROM read_parquet('{stg_glob}')").fetchone()[0]
        ctx.rows_duplicate = ctx.rows_received - ctx.rows_written
        _dim(con)
        con.execute(_sql("03_month_marts.sql", src=stg_glob, occ=occ.as_posix(),
                         prof=prof.as_posix(), cov=cov.as_posix()))

        first = date.fromisoformat(f"{month}-01")
        with session("engineer") as conn:
            conn.execute("DELETE FROM mart.occupancy_hourly WHERE source = 'archive' "
                         "AND hour_utc >= %s AND hour_utc < (%s::date + interval '1 month')", (first, first))
            cols = ["canton_code", "power_class", "hour_utc", "n_evse", "avg_available", "avg_occupied",
                    "avg_out_of_service", "avg_unknown", "occupancy_rate", "oos_rate", "slots_observed"]
            _copy_parquet_to_pg(con, conn, occ, "mart.occupancy_hourly", cols + ["source"],
                                select=[c if c != "hour_utc" else "strftime(hour_utc, '%Y-%m-%d %H:%M:%S+00')"
                                        for c in cols] + ["'archive'"])
        _write_coverage(ctx, month, con.sql(f"SELECT * FROM read_parquet('{cov.as_posix()}')").fetchone())
        con.close()
        shutil.rmtree(work, ignore_errors=True)
        if month in cfg.get("known_partial_months", []):
            ctx.errors.append(f"{month} is known to be incomplete in the source")


def _write_coverage(ctx, month: str, row) -> None:
    first = date.fromisoformat(f"{month}-01")
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    expected = (nxt - first).days * 288
    n_rows, n_slots, n_evse, share_geo, share_unknown = row if row else (0, 0, 0, None, None)
    with session("engineer") as conn:
        conn.execute(
            """INSERT INTO mart.archive_coverage
                 (month, n_rows, n_slots, expected_slots, n_evse, share_with_geo, share_unknown, run_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (month) DO UPDATE SET n_rows = EXCLUDED.n_rows, n_slots = EXCLUDED.n_slots,
                 expected_slots = EXCLUDED.expected_slots, n_evse = EXCLUDED.n_evse,
                 share_with_geo = EXCLUDED.share_with_geo, share_unknown = EXCLUDED.share_unknown,
                 run_id = EXCLUDED.run_id""",
            (first, n_rows, n_slots, expected, n_evse, share_geo, share_unknown, ctx.run_id))


def build_profiles() -> None:
    """Профили доступности по всем обработанным месяцам → mart.evse_profile, mart.profile_group."""
    parts = sorted(lake_dir("mart_parts").glob("month=*/profile_parts.parquet"))
    if not parts:
        return
    with start_run("archive_profiles", {"months": [p.parent.name for p in parts]}) as ctx:
        con = duck()
        _dim(con)
        glob = (lake_dir("mart_parts") / "month=*" / "profile_parts.parquet").as_posix()
        out = lake_dir("mart_parts")
        con.execute(f"""
            COPY (SELECT evse_id, dow, hour_local, sum(n_free) / sum(n_obs) AS p_free, sum(n_obs)::INTEGER AS n_obs
                    FROM read_parquet('{glob}') GROUP BY ALL HAVING sum(n_obs) >= 12)
            TO '{(out / 'evse_profile.parquet').as_posix()}' (FORMAT parquet)""")
        con.execute(f"""
            COPY (SELECT d.canton_code, d.power_class, p.dow, p.hour_local,
                         sum(p.n_free) / sum(p.n_obs) AS p_free,
                         sum(p.n_occ) / nullif(sum(p.n_free + p.n_occ), 0) AS p_occupied,
                         sum(p.n_obs) AS n_obs
                    FROM read_parquet('{glob}') p JOIN dim d USING (evse_id)
                   WHERE d.canton_code IS NOT NULL AND d.power_class IS NOT NULL
                   GROUP BY ALL)
            TO '{(out / 'profile_group.parquet').as_posix()}' (FORMAT parquet)""")
        with session("engineer") as conn:
            conn.execute("TRUNCATE mart.evse_profile")
            conn.execute("TRUNCATE mart.profile_group")
            ctx.rows_written = _copy_parquet_to_pg(
                con, conn, out / "evse_profile.parquet", "mart.evse_profile",
                ["evse_id", "dow", "hour_local", "p_free", "n_obs"])
            _copy_parquet_to_pg(
                con, conn, out / "profile_group.parquet", "mart.profile_group",
                ["canton_code", "power_class", "dow", "hour_local", "p_free", "p_occupied", "n_obs"],
                select=["canton_code", "power_class", "dow", "hour_local", "p_free",
                        "coalesce(p_occupied, 0)", "n_obs"])
        con.close()


def run(month: str | None = None, force: bool = False) -> None:
    cfg = source_cfg(SOURCE)
    load_details()
    todo = [month] if month else months(cfg)
    failed = []
    for m in todo:
        try:
            process_month(m, force=force)
        except Exception as exc:
            log.error("archive %s: %s", m, exc)
            failed.append(m)
    build_profiles()
    if failed:
        raise RuntimeError(f"archive months failed: {', '.join(failed)}")
