"""Рекомендатель зарядных станций для водителя (раздел 5 DESIGN.md).

1. candidates     совместимые активные точки в радиусе (функция БД core.fn_compatible_evses)
2. compatibility  жёсткие ограничения: разъём, свой кабель для розетки, ток, фильтры водителя
3. power          эффективная мощность = min(мощность точки, предел автомобиля по AC/DC)
4. eta            расстояние по прямой × коэффициент извилистости / средняя скорость (или OSRM)
5. availability   модель B: вероятность свободной точки к прибытию; P_станции = 1 − Π(1 − pᵢ)
6. scoring        ожидаемое время T = путь + (1 − P)·штраф ожидания + 60·E / P_eff
7. explain        пояснение из слагаемых T
Запрос и выдача журналируются (координаты огрублены до 0,01°).
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from evadvisor import routing
from evadvisor.config import settings
from evadvisor.db import connect
from evadvisor.models import serving

log = logging.getLogger(__name__)


@dataclass
class Request:
    vehicle_id: int
    lat: float
    lon: float
    energy_kwh: float = 20.0
    dc_only: bool = False
    open_24h: bool = False
    public_only: bool = False
    has_cable: bool = True
    radius_km: float | None = None


@dataclass
class Result:
    request_id: str
    vehicle: dict
    status_age_min: float | None
    temperature_c: float | None
    radius_km: float
    items: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _refresh_sources(warnings: list[str]) -> None:
    """Свежий снимок статусов (не чаще раза в 5 минут) и прогноз погоды (раз в час)."""
    cfg = settings()
    try:
        from evadvisor.ingestion import evse_status

        evse_status.run(max_age_minutes=cfg["sources"]["evse_status"]["cache_minutes"])
    except Exception as exc:
        log.warning("снимок статусов не получен: %s", exc)
        warnings.append("Не удалось получить текущие статусы — используется последний снимок или профиль.")
    try:
        from evadvisor.ingestion import weather

        weather.load_forecast(max_age_minutes=cfg["sources"]["weather"]["forecast_cache_minutes"])
    except Exception as exc:
        log.warning("прогноз погоды не получен: %s", exc)


def _candidates(conn, req: Request, radius: float) -> pd.DataFrame:
    rows = conn.execute(
        """SELECT c.evse_id, c.station_id, c.distance_km, c.plug_codes, c.power_class,
                  c.power_kw::float AS power_kw, c.effective_kw::float AS effective_kw,
                  s.name, s.street, s.postal_code, s.city, s.canton_code, s.lat, s.lon,
                  s.is_open_24h, s.accessibility, o.name AS operator
             FROM core.fn_compatible_evses(%s, %s, %s, %s, %s) c
             JOIN core.station s ON s.station_id = c.station_id
             JOIN core.operator o ON o.operator_id = s.operator_id""",
        (req.vehicle_id, req.lat, req.lon, radius, req.has_cable),
    ).fetchall()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if req.dc_only:
        df = df[df["power_class"] == "DC"]
    if req.open_24h:
        df = df[df["is_open_24h"].fillna(False)]
    if req.public_only:
        df = df[df["accessibility"].isin(["Free publicly accessible", "Paying publicly accessible"])]
    df = df[df["effective_kw"].notna() | (df["power_class"] == "AC")]
    df["canton_code"] = df["canton_code"].str.strip()
    return df


def _station_status(conn, station_ids: list[int]) -> tuple[pd.DataFrame, datetime | None]:
    rows = conn.execute(
        """WITH last AS (SELECT max(slot_ts) AS slot FROM core.status_snapshot)
           SELECT e.evse_id, e.station_id, ss.status, ss.slot_ts
             FROM core.evse e
             LEFT JOIN core.status_snapshot ss ON ss.evse_id = e.evse_id
                  AND ss.slot_ts = (SELECT slot FROM last)
                  AND (SELECT slot FROM last) > now() - interval '3 hours'
            WHERE e.station_id = ANY(%s) AND e.is_active""",
        (station_ids,),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["evse_id", "station_id", "status", "slot_ts"])
    slot = df["slot_ts"].dropna().max() if not df.empty else None
    return df, (None if pd.isna(slot) else slot)


def _profiles(conn, evse_ids: list[str], keys: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ep = pd.DataFrame(
        conn.execute(
            "SELECT evse_id, dow, hour_local, p_free AS p_free_evse FROM mart.evse_profile WHERE evse_id = ANY(%s)",
            (evse_ids,),
        ).fetchall(),
        columns=["evse_id", "dow", "hour_local", "p_free_evse"],
    )
    gp = pd.DataFrame(
        conn.execute(
            """SELECT trim(canton_code) AS canton_code, power_class, dow, hour_local, p_free AS p_free_group
             FROM mart.profile_group WHERE canton_code = ANY(%s)""",
            (keys["canton_code"].dropna().unique().tolist(),),
        ).fetchall(),
        columns=["canton_code", "power_class", "dow", "hour_local", "p_free_group"],
    )
    return ep, gp


def _weather(conn, cantons: list[str], hours: list[datetime]) -> pd.DataFrame:
    rows = conn.execute(
        """SELECT DISTINCT ON (canton_code, hour_utc) trim(canton_code) AS canton_code, hour_utc,
                  temperature_c AS temp_c, precipitation_mm AS precip_mm, snowfall_cm AS snow_cm
             FROM core.weather_hourly
            WHERE canton_code = ANY(%s) AND hour_utc = ANY(%s)
            ORDER BY canton_code, hour_utc, CASE kind WHEN 'forecast' THEN 0 WHEN 'actual' THEN 1 ELSE 2 END""",
        (cantons, hours),
    ).fetchall()
    return pd.DataFrame(rows, columns=["canton_code", "hour_utc", "temp_c", "precip_mm", "snow_cm"])


def _first(*values):
    """Первое непустое значение (NaN и пустые строки пропускаются)."""
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return values[-1]


def _explain(row: pd.Series, vehicle: dict, energy: float, cold: bool) -> str:
    plug_names = {
        "TYPE2_SOCKET": "Type 2 (свой кабель)",
        "TYPE2_CABLE": "Type 2",
        "TYPE1_CABLE": "Type 1",
        "CCS2": "CCS",
        "CCS1": "CCS1",
        "CHADEMO": "CHAdeMO",
        "TESLA": "Tesla",
    }
    plugs = ", ".join(plug_names.get(p, p) for p in row["plugs"])
    limit = vehicle["dc_max_kw"] if row["power_class"] == "DC" else vehicle["ac_max_kw"]
    parts = [
        f"Подходит разъём {plugs}.",
        f"Мощность точки {row['power_kw']:.0f} кВт, ваш автомобиль примет до {limit:.0f} кВт "
        f"→ около {row['effective_kw']:.0f} кВт."
        if pd.notna(row["power_kw"]) and pd.notna(row["effective_kw"])
        else "Мощность точки не указана оператором — время зарядки оценено по минимальной для этого типа.",
        f"{row['distance_km']:.1f} км, ~{row['eta_min']:.0f} мин в пути.",
    ]
    if row["n_now_known"]:
        parts.append(
            f"Сейчас свободно {row['n_free_now']} из {row['n_total']}; "
            f"к приезду свободна хотя бы одна подходящая точка с вероятностью {row['p_station']:.0%}."
        )
    else:
        parts.append(f"Текущий статус неизвестен; по истории вероятность свободной точки {row['p_station']:.0%}.")
    parts.append(f"{energy:.0f} кВт·ч займут ~{row['charge_min']:.0f} мин. Итого ≈ {row['total_min']:.0f} мин.")
    if cold and row["power_class"] == "DC":
        parts.append("В мороз быстрая зарядка обычно медленнее заявленной мощности.")
    return " ".join(parts)


def recommend(req: Request, log_request: bool = True) -> Result:
    cfg = settings()["recommender"]
    warnings: list[str] = []
    _refresh_sources(warnings)
    now = datetime.now(UTC)
    with connect("driver") as conn:
        vehicle = conn.execute(
            """SELECT vehicle_id, brand, model, variant, release_year, ac_max_kw::float AS ac_max_kw,
                      dc_max_kw::float AS dc_max_kw FROM core.vehicle WHERE vehicle_id = %s""",
            (req.vehicle_id,),
        ).fetchone()
        if vehicle is None:
            raise ValueError("Автомобиль не найден в каталоге")
        radius = req.radius_km or cfg["radius_km"]
        cand = _candidates(conn, req, radius)
        # мало станций — расширяем радиус (до radius_max_km)
        while (0 if cand.empty else cand["station_id"].nunique()) < cfg["min_candidates"] and radius < cfg[
            "radius_max_km"
        ]:
            radius = min(radius * 2, cfg["radius_max_km"])
            cand = _candidates(conn, req, radius)
        result = Result(str(uuid.uuid4()), vehicle, None, None, radius, warnings=warnings)
        if cand.empty:
            warnings.append(f"В радиусе {radius:.0f} км нет совместимых станций с учётом фильтров.")
            return result

        # Время в пути: сначала по прямой для отбора, затем (если включено) уточнение маршрутизатором
        cand["eta_min"] = routing.eta_minutes(cand["distance_km"].to_numpy())
        stations = cand.groupby("station_id")["distance_km"].min().sort_values().head(cfg["top_n"] * 6).index.tolist()
        cand = cand[cand["station_id"].isin(stations)].copy()
        osrm = routing.osrm_table(req.lat, req.lon, cand.drop_duplicates("station_id")[["station_id", "lat", "lon"]])
        if osrm is not None:
            cand["eta_min"] = cand["station_id"].map(osrm["eta_min"]).fillna(cand["eta_min"])
            cand["distance_km"] = cand["station_id"].map(osrm["distance_km"]).fillna(cand["distance_km"])

        status, slot = _station_status(conn, stations)
        result.status_age_min = (now - slot).total_seconds() / 60 if slot is not None else None
        if result.status_age_min is None:
            warnings.append("Нет свежего снимка статусов: вероятность рассчитана по истории занятости.")
        elif result.status_age_min > cfg["status_max_age_min"]:
            warnings.append(f"Статусы получены {result.status_age_min:.0f} мин назад.")
        counts = (
            status.assign(free=status["status"].eq("Available"))
            .groupby("station_id")
            .agg(
                n_total=("evse_id", "size"),
                n_free_now=("free", "sum"),
                n_now_known=("status", lambda s: int(s.notna().sum())),
            )
        )
        cand = cand.merge(status[["evse_id", "status"]], on="evse_id", how="left")
        cand = cand.merge(counts, left_on="station_id", right_index=True, how="left")
        cand["status_now"] = cand["status"].fillna("Unknown")
        cand["n_total_other"] = cand["n_total"] - 1
        cand["n_free_other"] = cand["n_free_now"] - cand["status_now"].eq("Available").astype(int)
        cand["delta_min"] = (np.ceil(cand["eta_min"] / 5) * 5).clip(5, 60).astype(int)
        cand["t0"] = now

        arrival_hours = sorted(
            {
                (now + pd.Timedelta(minutes=int(d))).replace(minute=0, second=0, microsecond=0)
                for d in cand["delta_min"].unique()
            }
        )
        local = pd.to_datetime(cand["t0"], utc=True) + pd.to_timedelta(cand["delta_min"], unit="m")
        local = local.dt.tz_convert("Europe/Zurich")
        cand["dow"], cand["hour_local"] = local.dt.dayofweek.astype(int), local.dt.hour.astype(int)
        ep, gp = _profiles(conn, cand["evse_id"].tolist(), cand)
        cand = cand.merge(ep, on=["evse_id", "dow", "hour_local"], how="left")
        cand = cand.merge(gp, on=["canton_code", "power_class", "dow", "hour_local"], how="left")
        w = _weather(conn, cand["canton_code"].dropna().unique().tolist(), arrival_hours)
        cand["hour_utc"] = (
            pd.to_datetime(cand["t0"], utc=True) + pd.to_timedelta(cand["delta_min"], unit="m")
        ).dt.floor("h")
        cand = cand.merge(w, on=["canton_code", "hour_utc"], how="left")
        cand["p_free"] = serving.predict(cand.drop(columns=["dow", "hour_local"]))
        if not w.empty:
            result.temperature_c = float(w["temp_c"].mean())

        # Агрегация по станции
        def station_row(g: pd.DataFrame) -> pd.Series:
            best = g.sort_values("effective_kw", ascending=False, na_position="last").iloc[0]
            eff = g["effective_kw"].max()
            return pd.Series(
                {
                    "name": _first(best["name"], best["street"], best["city"], "Станция"),
                    "street": best["street"],
                    "postal_code": best["postal_code"],
                    "city": best["city"],
                    "lat": best["lat"],
                    "lon": best["lon"],
                    "operator": best["operator"],
                    "is_open_24h": best["is_open_24h"],
                    "accessibility": best["accessibility"],
                    "distance_km": float(g["distance_km"].min()),
                    "eta_min": float(g["eta_min"].min()),
                    "power_class": best["power_class"],
                    "power_kw": float(best["power_kw"]) if pd.notna(best["power_kw"]) else None,
                    "effective_kw": float(eff) if pd.notna(eff) else None,
                    "plugs": sorted({p for ps in g["plug_codes"] for p in ps}),
                    "n_compatible": len(g),
                    "n_total": int(g["n_total"].max()),
                    "n_free_now": int(g["n_free_now"].max()),
                    "n_now_known": int(g["n_now_known"].max()),
                    "p_station": float(1 - np.prod(1 - g["p_free"].to_numpy())),
                    "points": g[["evse_id", "power_class", "power_kw", "status_now", "p_free"]].to_dict("records"),
                }
            )

        st = cand.groupby("station_id").apply(station_row, include_groups=False).reset_index()
        eff = st["effective_kw"].fillna(st["power_class"].map({"AC": 3.7, "DC": 22.0}))
        st["charge_min"] = 60 * req.energy_kwh / eff
        st["total_min"] = st["eta_min"] + (1 - st["p_station"]) * cfg["wait_penalty_min"] + st["charge_min"]
        st = st.sort_values("total_min").head(cfg["top_n"]).reset_index(drop=True)
        cold = result.temperature_c is not None and result.temperature_c < cfg["cold_warning_c"]
        st["explanation"] = [_explain(r, vehicle, req.energy_kwh, cold) for _, r in st.iterrows()]
        result.items = [
            {**{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}, "rank": i + 1}
            for i, r in enumerate(st.to_dict("records"))
        ]

        if log_request:
            _log(conn, req, result, slot)
    return result


def _log(conn, req: Request, result: Result, slot) -> None:
    """Журнал запросов: координаты огрублены до 0,01° (~1 км) — персональные данные не хранятся."""
    try:
        conn.execute(
            """INSERT INTO core.recommendation_request (request_id, vehicle_id, lat_round, lon_round, energy_kwh,
                       filters, status_slot_ts, model_version)
               VALUES (%s, %s, round(%s::numeric, 2), round(%s::numeric, 2), %s, %s, %s, %s)""",
            (
                result.request_id,
                req.vehicle_id,
                req.lat,
                req.lon,
                req.energy_kwh,
                psycopg_json(
                    {
                        "dc_only": req.dc_only,
                        "open_24h": req.open_24h,
                        "public_only": req.public_only,
                        "has_cable": req.has_cable,
                        "radius_km": result.radius_km,
                    }
                ),
                slot,
                serving.model_version(),
            ),
        )
        for item in result.items:
            conn.execute(
                """INSERT INTO core.recommendation_item (request_id, rank, station_id, distance_km, eta_min, p_free,
                          effective_kw, expected_total_min) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    result.request_id,
                    item["rank"],
                    int(item["station_id"]),
                    round(item["distance_km"], 2),
                    round(item["eta_min"], 1),
                    round(item["p_station"], 3),
                    item["effective_kw"],
                    round(item["total_min"], 1),
                ),
            )
        conn.commit()
    except Exception as exc:  # журнал не должен ломать выдачу водителю
        conn.rollback()
        log.warning("журнал рекомендаций: %s", exc)


def psycopg_json(obj: dict):
    from psycopg.types.json import Jsonb

    return Jsonb(obj)
