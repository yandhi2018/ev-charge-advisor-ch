"""Общие функции моделей: календарь, погода, метрики, запись результатов."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from functools import lru_cache

import holidays
import numpy as np
import pandas as pd

from evadvisor.config import settings
from evadvisor.db import connect, session

TZ = "Europe/Zurich"


@lru_cache(maxsize=64)
def _holidays(canton: str | None, years: tuple[int, ...]) -> frozenset[date]:
    h = holidays.Switzerland(years=list(years), subdiv=canton) if canton else holidays.Switzerland(years=list(years))
    return frozenset(h.keys())


def is_holiday(local_dates: pd.Series, canton: pd.Series | str | None = None) -> np.ndarray:
    """Праздник Швейцарии (с учётом кантона, если задан)."""
    years = tuple(sorted({d.year for d in local_dates})) or (2025,)
    if canton is None or isinstance(canton, str):
        hd = _holidays(canton, years)
        return np.fromiter((d in hd for d in local_dates), dtype=np.int8, count=len(local_dates))
    out = np.zeros(len(local_dates), dtype=np.int8)
    for c in pd.unique(canton):
        mask = (canton == c).to_numpy()
        hd = _holidays(c, years)
        out[mask] = [d in hd for d in local_dates[mask]]
    return out


def calendar_features(ts_utc: pd.Series, canton: pd.Series | None = None) -> pd.DataFrame:
    local = pd.to_datetime(ts_utc, utc=True).dt.tz_convert(TZ)
    return pd.DataFrame({
        "hour_local": local.dt.hour.astype("int8"),
        "dow": local.dt.dayofweek.astype("int8"),
        "is_weekend": (local.dt.dayofweek >= 5).astype("int8"),
        "is_holiday": is_holiday(local.dt.date, canton),
    }, index=ts_utc.index)


def load_weather(kinds: tuple[str, ...] = ("actual", "hist_forecast", "forecast")) -> pd.DataFrame:
    with connect("analyst") as conn:
        rows = conn.execute(
            """SELECT canton_code, hour_utc, kind, temperature_c, precipitation_mm, snowfall_cm, wind_kmh
                 FROM core.weather_hourly WHERE kind = ANY(%s)""", (list(kinds),)).fetchall()
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["canton_code", "hour_utc", "kind", "temperature_c", "precipitation_mm",
                                     "snowfall_cm", "wind_kmh"])
    df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)
    df["canton_code"] = df["canton_code"].str.strip()
    return df


def weather_wide(weather: pd.DataFrame, kind: str) -> pd.DataFrame:
    w = weather[weather["kind"] == kind].drop(columns="kind")
    return w.rename(columns={"temperature_c": "temp_c", "precipitation_mm": "precip_mm",
                             "snowfall_cm": "snow_cm", "wind_kmh": "wind_kmh"})


# ---------------------------------------------------------------- метрики
def regression_metrics(y: np.ndarray, p: np.ndarray, y_naive: np.ndarray | None = None) -> dict[str, float]:
    err = p - y
    out = {"mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2))), "n": float(len(y))}
    if y_naive is not None:
        mae_naive = float(np.mean(np.abs(y_naive - y)))
        out["mase"] = out["mae"] / mae_naive if mae_naive > 0 else float("nan")
    return out


def classification_metrics(y: np.ndarray, p: np.ndarray, bins: int = 10) -> dict[str, float]:
    from sklearn.metrics import log_loss, roc_auc_score

    p = np.clip(p, 1e-4, 1 - 1e-4)
    out = {
        "brier": float(np.mean((p - y) ** 2)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else float("nan"),
        "base_rate": float(np.mean(y)),
        "n": float(len(y)),
    }
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(p[m].mean() - y[m].mean())
    out["ece"] = float(ece)
    return out


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    return [{"bin": b, "p_mean": float(p[idx == b].mean()), "y_mean": float(y[idx == b].mean()),
             "n": int((idx == b).sum())} for b in range(bins) if (idx == b).any()]


def save_metrics(task: str, model: str, fold: int, metrics: dict[str, float], slice_: str = "all") -> None:
    with session("engineer") as conn:
        for metric, value in metrics.items():
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            conn.execute(
                """INSERT INTO mart.model_metric (task, model_name, fold, metric, slice, value)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (task, model_name, fold, metric, slice)
                   DO UPDATE SET value = EXCLUDED.value, computed_at = now()""",
                (task, model, fold, metric, slice_, float(value)))


def register_model(task: str, name: str, params: dict, metrics: dict, artifact: str, data_hash: str,
                   activate: bool = True) -> None:
    version = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d%H%M")
    with session("engineer") as conn:
        if activate:
            conn.execute("UPDATE ops.model_registry SET is_active = false WHERE task = %s", (task,))
        conn.execute(
            """INSERT INTO ops.model_registry (task, model_name, version, data_hash, params, metrics,
                                               artifact_path, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (task, model_name, version) DO UPDATE SET metrics = EXCLUDED.metrics,
                 artifact_path = EXCLUDED.artifact_path, is_active = EXCLUDED.is_active""",
            (task, name, version, data_hash, json.dumps(params, default=str), json.dumps(metrics, default=str),
             artifact, activate))


def frame_hash(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


def seed() -> int:
    return int(settings()["models"]["random_seed"])
