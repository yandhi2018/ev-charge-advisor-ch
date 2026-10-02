"""Бэктест рекомендателя на тестовом периоде модели B (факт — архив статусов).

Для N случайных запросов (место рядом со случайной станцией, автомобиль, момент t0 в тестовом периоде)
сравниваются стратегии выбора станции:
  nearest       — ближайшая совместимая станция;
  nearest_free  — ближайшая станция, где совместимая точка свободна сейчас;
  model         — ранжирование рекомендателя (ожидаемое время с вероятностью модели B).
Hit@1 — у станции на первом месте к моменту прибытия t0 + ETA свободна хотя бы одна совместимая точка
(по фактическим статусам архива). Hit@3 — хотя бы у одной из трёх первых.
Признаки модели строятся только из данных на момент t0 и профилей обучающего окна.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from evadvisor.config import lake_dir, settings
from evadvisor.db import connect
from evadvisor.ingestion.archive import duck
from evadvisor.models import serving
from evadvisor.models.availability import _months, train_profiles
from evadvisor.models.common import load_weather, save_metrics, seed, weather_wide
from evadvisor.routing import eta_minutes
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
VEHICLE_PROFILES = {   # типовые автомобили: разъёмы и пределы мощности
    "ccs_car": {"plugs": {"CCS2", "TYPE2_CABLE", "TYPE2_SOCKET"}, "ac": 11.0, "dc": 150.0},
    "ac_only": {"plugs": {"TYPE2_CABLE", "TYPE2_SOCKET"}, "ac": 11.0, "dc": None},
}


def _haversine(lat1, lon1, lat2, lon2):
    r = np.radians
    a = np.sin(r(lat2 - lat1) / 2) ** 2 + np.cos(r(lat1)) * np.cos(r(lat2)) * np.sin(r(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def _evse_frame() -> pd.DataFrame:
    with connect("engineer") as conn:
        rows = conn.execute(
            """SELECT e.evse_id, e.station_id, e.power_class, e.power_kw::float AS power_kw, trim(s.canton_code) AS
                      canton_code, s.lat, s.lon, array_agg(ep.plug_code) AS plugs
                 FROM core.evse e JOIN core.station s USING (station_id) JOIN core.evse_plug ep USING (evse_id)
                WHERE s.canton_code IS NOT NULL AND e.power_class IS NOT NULL
                GROUP BY e.evse_id, e.station_id, s.lat, s.lon, s.canton_code""").fetchall()
    return pd.DataFrame(rows)


def run(n_requests: int = 1500, radius_km: float = 15, energy_kwh: float = 20) -> dict:
    cfg_b = settings()["models"]["availability"]
    cfg_r = settings()["recommender"]
    rng = np.random.default_rng(seed())
    with start_run("backtest", {"n": n_requests, "radius_km": radius_km}) as ctx:
        evse = _evse_frame()
        months = _months(cfg_b["test_from"], cfg_b["test_to"])
        globs = [(lake_dir("stg", "status_5min", f"month={m}") / "part-*.parquet").as_posix() for m in months]
        con = duck()
        con.register("ev", evse[["evse_id"]])
        con.execute(f"""CREATE TABLE st AS SELECT s.evse_id, s.slot_ts, s.status
                          FROM read_parquet({globs}) s SEMI JOIN ev USING (evse_id)""")
        slots = con.sql("SELECT DISTINCT slot_ts FROM st ORDER BY 1").df()["slot_ts"].to_numpy()
        known = set(con.sql("SELECT DISTINCT evse_id FROM st").df()["evse_id"])
        evse = evse[evse["evse_id"].isin(known)].reset_index(drop=True)
        log.info("backtest: точек с историей %s, слотов %s", len(evse), len(slots))
        evse_prof, grp_prof = train_profiles(_months(cfg_b["train_from"], cfg_b["train_to"]))
        w = weather_wide(load_weather(("hist_forecast",)), "hist_forecast")
        stations = evse.drop_duplicates("station_id")[["station_id", "lat", "lon"]].to_numpy()

        hits = {k: [] for k in ("nearest", "nearest_free", "model")}
        hits3 = {k: [] for k in hits}
        for _ in range(n_requests):
            s = stations[rng.integers(len(stations))]
            lat, lon = s[1] + rng.normal(0, 0.03), s[2] + rng.normal(0, 0.04)   # ~3 км от станции
            vp = VEHICLE_PROFILES[rng.choice(list(VEHICLE_PROFILES))]
            t0 = pd.Timestamp(slots[rng.integers(len(slots) - 13)])
            d = _haversine(lat, lon, evse["lat"].to_numpy(), evse["lon"].to_numpy())
            cand = evse[(d <= radius_km) & evse["plugs"].apply(lambda p, vp=vp: bool(set(p) & vp["plugs"]))
                        & ((evse["power_class"] == "AC") | (vp["dc"] is not None))].copy()
            if cand["station_id"].nunique() < 3:
                continue
            cand["distance_km"] = d[cand.index]
            cand["eta_min"] = eta_minutes(cand["distance_km"].to_numpy())
            cand["delta_min"] = (np.ceil(cand["eta_min"] / 5) * 5).clip(5, 60).astype(int)
            now = con.execute("SELECT evse_id, status FROM st WHERE slot_ts = ?", [t0]).df()
            cand = cand.merge(now.rename(columns={"status": "status_now"}), on="evse_id", how="left")
            cand["status_now"] = cand["status_now"].fillna("Unknown")
            allst = evse[evse["station_id"].isin(cand["station_id"])][["evse_id", "station_id"]].merge(
                now, on="evse_id", how="left")
            cnt = allst.assign(f=allst["status"].eq("Available")).groupby("station_id").agg(
                n_total=("evse_id", "size"), n_free=("f", "sum"))
            cand = cand.merge(cnt, left_on="station_id", right_index=True)
            cand["n_total_other"] = cand["n_total"] - 1
            cand["n_free_other"] = cand["n_free"] - cand["status_now"].eq("Available").astype(int)
            cand["t0"] = t0.tz_localize("UTC") if t0.tzinfo is None else t0
            arrival = cand["t0"] + pd.to_timedelta(cand["delta_min"], unit="m")
            local = arrival.dt.tz_convert("Europe/Zurich")
            cand["dow"], cand["hour_local"] = local.dt.dayofweek, local.dt.hour
            cand = cand.merge(evse_prof, on=["evse_id", "dow", "hour_local"], how="left")
            cand = cand.merge(grp_prof, on=["canton_code", "power_class", "dow", "hour_local"], how="left")
            cand["hour_utc"] = arrival.dt.floor("h")
            cand = cand.merge(w[["canton_code", "hour_utc", "temp_c", "precip_mm", "snow_cm"]],
                              on=["canton_code", "hour_utc"], how="left")
            cand["p_free"] = serving.predict(cand.drop(columns=["dow", "hour_local"]))
            limit = np.where(cand["power_class"] == "DC", vp["dc"] or 0, vp["ac"])
            default = np.where(cand["power_class"] == "DC", 22.0, 3.7)   # мощность не указана оператором
            cand["eff_kw"] = np.minimum(cand["power_kw"].fillna(pd.Series(default, index=cand.index)), limit)
            # факт: статус каждой точки в слот прибытия
            arr_slots = (t0 + pd.to_timedelta(cand["delta_min"], unit="m")).unique().tolist()
            fact = con.execute("SELECT evse_id, slot_ts, status FROM st WHERE slot_ts IN (SELECT unnest(?))",
                               [arr_slots]).df()
            cand["arr_slot"] = t0 + pd.to_timedelta(cand["delta_min"], unit="m")
            cand = cand.merge(fact.rename(columns={"slot_ts": "arr_slot", "status": "status_arr"}),
                              on=["evse_id", "arr_slot"], how="left")
            g = cand.groupby("station_id").agg(
                distance_km=("distance_km", "min"), eta_min=("eta_min", "min"),
                free_now=("status_now", lambda s: (s == "Available").any()),
                free_arr=("status_arr", lambda s: (s == "Available").any()),
                p_station=("p_free", lambda p: 1 - np.prod(1 - p.to_numpy())),
                eff=("eff_kw", "max"))
            g["total"] = g["eta_min"] + (1 - g["p_station"]) * cfg_r["wait_penalty_min"] + 60 * energy_kwh / g["eff"]
            orders = {
                "nearest": g.sort_values("distance_km"),
                "nearest_free": pd.concat([g[g["free_now"]].sort_values("distance_km"),
                                           g[~g["free_now"]].sort_values("distance_km")]),
                "model": g.sort_values("total"),
            }
            for k, o in orders.items():
                hits[k].append(bool(o["free_arr"].iloc[0]))
                hits3[k].append(bool(o["free_arr"].head(3).any()))
        con.close()
        out = {}
        for k in hits:
            m = {"hit_at_1": float(np.mean(hits[k])), "hit_at_3": float(np.mean(hits3[k])), "n": float(len(hits[k]))}
            save_metrics("recommender", k, 1, m)
            out[k] = m
        ctx.rows_written = len(hits["model"])
        log.info("backtest: %s", out)
        return out
