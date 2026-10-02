"""Применение модели B в рекомендателе: загрузка артефактов и прогноз по признакам на момент запроса."""

from __future__ import annotations

import json
import logging
import pickle
from functools import lru_cache
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from evadvisor.config import artifacts_dir
from evadvisor.models.availability import CANTONS, FEATURES, STATUS_CODES
from evadvisor.models.common import calendar_features

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _artifacts() -> tuple[lgb.Booster, object, dict] | None:
    art = Path(artifacts_dir())
    try:
        booster = lgb.Booster(model_file=str(art / "availability_lgbm.txt"))
        with open(art / "availability_isotonic.pkl", "rb") as f:
            iso = pickle.load(f)
        meta = json.loads((art / "availability_meta.json").read_text(encoding="utf-8"))
        return booster, iso, meta
    except (OSError, lgb.basic.LightGBMError) as exc:
        log.warning("модель доступности не найдена (%s): используется исторический профиль", exc)
        return None


def model_version() -> str:
    a = _artifacts()
    return f"B3_lightgbm@{a[2]['trained']}" if a else "B1_profile"


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """df: evse_id, canton_code, power_class, power_kw, status_now, n_total_other, n_free_other,
    delta_min, t0, p_free_evse, p_free_group, temp_c, precip_mm, snow_cm."""
    out = df.copy()
    arrival = pd.to_datetime(out["t0"], utc=True) + pd.to_timedelta(out["delta_min"], unit="m")
    cal = calendar_features(arrival, out["canton_code"])
    for c in cal.columns:
        out[c] = cal[c].to_numpy()
    out["status_code"] = out["status_now"].map(STATUS_CODES).fillna(4).astype("int8")
    out["class_dc"] = (out["power_class"] == "DC").astype("int8")
    out["share_free_other"] = np.where(out["n_total_other"] > 0,
                                       out["n_free_other"] / out["n_total_other"].clip(lower=1), np.nan)
    out["canton_cat"] = out["canton_code"].map({c: i for i, c in enumerate(CANTONS)}).fillna(-1).astype("int16")
    return out


def predict(df: pd.DataFrame) -> np.ndarray:
    if df.empty:
        return np.array([])
    f = build_features(df)
    art = _artifacts()
    if art is None:
        return f["p_free_evse"].fillna(f["p_free_group"]).fillna(0.75).to_numpy()
    booster, iso, _ = art
    raw = booster.predict(f[FEATURES].astype(float))
    return np.clip(iso.predict(raw), 0.0, 1.0)
