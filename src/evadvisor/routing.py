"""Время в пути: по прямой с коэффициентом извилистости или маршрутизатор OSRM (если включён)."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import requests

from evadvisor.config import settings

log = logging.getLogger(__name__)


def eta_minutes(distance_km: np.ndarray) -> np.ndarray:
    cfg = settings()["recommender"]
    road = distance_km * cfg["detour_factor"]
    speed = np.where(road <= cfg["near_threshold_km"], cfg["speed_kmh_near"], cfg["speed_kmh_far"])
    return road / speed * 60


def road_distance_km(distance_km: np.ndarray) -> np.ndarray:
    return distance_km * settings()["recommender"]["detour_factor"]


def osrm_table(lat: float, lon: float, stations: pd.DataFrame) -> pd.DataFrame | None:
    """Время и расстояние по дорогам от водителя до станций (OSRM table). None — OSRM выключен/недоступен."""
    cfg = settings()["routing"]
    if cfg.get("engine") != "osrm" or stations.empty:
        return None
    coords = ";".join([f"{lon},{lat}"] + [f"{r.lon},{r.lat}" for r in stations.itertuples()])
    try:
        resp = requests.get(f"{cfg['osrm_url']}/table/v1/driving/{coords}",
                            params={"sources": "0", "annotations": "duration,distance"}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        dur = np.array(data["durations"][0][1:], dtype=float) / 60
        dist = np.array(data["distances"][0][1:], dtype=float) / 1000
        return pd.DataFrame({"eta_min": dur, "distance_km": dist}, index=stations["station_id"].to_numpy())
    except Exception as exc:
        log.warning("OSRM недоступен (%s): используется расстояние по прямой", exc)
        return None


def osrm_route(lat: float, lon: float, lat2: float, lon2: float) -> dict | None:
    """Геометрия маршрута для отображения на карте (GeoJSON LineString)."""
    cfg = settings()["routing"]
    if cfg.get("engine") != "osrm":
        return None
    try:
        resp = requests.get(f"{cfg['osrm_url']}/route/v1/driving/{lon},{lat};{lon2},{lat2}",
                            params={"overview": "full", "geometries": "geojson"}, timeout=5)
        resp.raise_for_status()
        route = resp.json()["routes"][0]
        return {"geometry": route["geometry"], "duration_min": route["duration"] / 60,
                "distance_km": route["distance"] / 1000}
    except Exception as exc:
        log.warning("OSRM route: %s", exc)
        return None
