"""Прогноз A: почасовая загрузка точек по кантону и классу мощности на 1–24 ч.

Схема валидации — rolling origin с расширяющимся окном (config.yaml: models.canton.folds):
обучение до train_to, зазор 7 дней, тест [test_from, test_to]. Защита от утечек:
- признаки строятся только из значений не позже момента прогноза t0
  (лаги цели относительно t0, значения t−24 ч и t−168 ч для h ≤ 24 ч лежат до t0);
- профиль «кантон × класс × день недели × час» считается только по обучающему окну фолда;
- на тесте используется архивный прогноз погоды (hist_forecast), а не фактическая погода;
- ранняя остановка LightGBM — по хвосту обучающего окна, тест в подборе не участвует.
Модели: A0 сезонный наивный (неделю назад), A1 профиль, A2 LightGBM.
"""

from __future__ import annotations

import logging
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from evadvisor.config import artifacts_dir, settings
from evadvisor.db import connect, session
from evadvisor.models.common import (
    calendar_features,
    frame_hash,
    load_weather,
    register_model,
    regression_metrics,
    save_metrics,
    seed,
    weather_wide,
)
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
TASK = "canton"
FEATURES = ["lag0", "lag1", "lag2", "mean24", "y_tm24", "y_tm168", "profile", "h", "hour_local", "dow",
            "is_weekend", "is_holiday", "temp_c", "precip_mm", "snow_cm", "wind_kmh", "canton_cat", "class_cat"]
ORIGIN_STEP_H = 6


def load_series() -> pd.DataFrame:
    with connect("analyst") as conn:
        rows = conn.execute(
            """SELECT canton_code, power_class, hour_utc, occupancy_rate FROM mart.occupancy_hourly
                WHERE source = 'archive' AND occupancy_rate IS NOT NULL AND slots_observed >= 6""").fetchall()
    df = pd.DataFrame(rows)
    df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True)
    df["canton_code"] = df["canton_code"].str.strip()
    return df


def build_dataset(series: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """Строки (серия, t0, h) с признаками, известными в момент t0, и целью y(t0 + h)."""
    frames = []
    grid = pd.date_range(series["hour_utc"].min(), series["hour_utc"].max(), freq="h", tz="UTC")
    for (canton, cls), g in series.groupby(["canton_code", "power_class"]):
        y = g.set_index("hour_utc")["occupancy_rate"].reindex(grid).to_numpy(dtype=float)
        n = len(y)
        mean24 = pd.Series(y).rolling(24, min_periods=12).mean().to_numpy()
        origins = np.where((grid.hour % ORIGIN_STEP_H == 0) & ~np.isnan(y))[0]
        origins = origins[origins >= 168]
        for h in horizons:
            o = origins[origins + h < n]
            t = o + h
            ok = ~np.isnan(y[t])
            o, t = o[ok], t[ok]

            def at(idx, y=y):
                out = np.full(len(idx), np.nan)
                valid = idx >= 0
                out[valid] = y[idx[valid]]
                return out

            frames.append(pd.DataFrame({
                "canton_code": canton, "power_class": cls,
                "origin_utc": grid[o], "target_utc": grid[t], "h": h,
                "lag0": y[o], "lag1": at(o - 1), "lag2": at(o - 2), "mean24": mean24[o],
                "y_tm24": at(t - 24), "y_tm168": at(t - 168), "y": y[t],
            }))
    df = pd.concat(frames, ignore_index=True)
    cal = calendar_features(df["target_utc"], df["canton_code"])
    df = pd.concat([df, cal], axis=1)
    df["canton_cat"] = df["canton_code"].astype("category").cat.codes.astype("int16")
    df["class_cat"] = (df["power_class"] == "DC").astype("int8")
    return df


def add_profile(df: pd.DataFrame, series: pd.DataFrame, train_to: pd.Timestamp) -> pd.DataFrame:
    s = series[series["hour_utc"] <= train_to].copy()
    cal = calendar_features(s["hour_utc"])
    s["dow"], s["hour_local"] = cal["dow"], cal["hour_local"]
    prof = (s.groupby(["canton_code", "power_class", "dow", "hour_local"])["occupancy_rate"]
            .mean().rename("profile").reset_index())
    out = df.drop(columns=["profile"], errors="ignore").merge(
        prof, on=["canton_code", "power_class", "dow", "hour_local"], how="left")
    return out


def add_weather(df: pd.DataFrame, weather: pd.DataFrame, kind: str) -> pd.DataFrame:
    w = weather_wide(weather, kind).rename(columns={"hour_utc": "target_utc"})
    cols = ["temp_c", "precip_mm", "snow_cm", "wind_kmh"]
    return df.drop(columns=cols, errors="ignore").merge(w, on=["canton_code", "target_utc"], how="left")


def fit_lgbm(train: pd.DataFrame) -> lgb.LGBMRegressor:
    cut = train["target_utc"].max() - pd.Timedelta(days=28)   # хвост обучения — для ранней остановки
    tr, va = train[train["target_utc"] <= cut], train[train["target_utc"] > cut]
    model = lgb.LGBMRegressor(n_estimators=1500, learning_rate=0.05, num_leaves=63, min_child_samples=50,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              random_state=seed(), verbose=-1)
    model.fit(tr[FEATURES], tr["y"], eval_set=[(va[FEATURES], va["y"])],
              callbacks=[lgb.early_stopping(50, verbose=False)])
    return model


def _save_forecasts(test: pd.DataFrame, fold: int, preds: dict[str, np.ndarray]) -> None:
    from evadvisor.ingestion.common import copy_rows

    with session("engineer") as conn:
        conn.execute("DELETE FROM mart.forecast_canton WHERE fold = %s AND model_name = ANY(%s)",
                     (fold, list(preds)))
        for name, p in preds.items():
            rows = zip([name] * len(test), [fold] * len(test), test["origin_utc"], test["h"],
                       test["canton_code"], test["power_class"], np.clip(p, 0, 1), test["y"], strict=True)
            copy_rows(conn, "mart.forecast_canton",
                      ["model_name", "fold", "origin_utc", "horizon_h", "canton_code", "power_class", "y_pred",
                       "y_true"],
                      ["text", "int2", "timestamptz", "int2", "text", "text", "float4", "float4"], rows)


def evaluate() -> dict:
    cfg = settings()["models"]["canton"]
    with start_run("model_canton", {"folds": cfg["folds"]}) as ctx:
        series = load_series()
        weather = load_weather(("actual", "hist_forecast"))
        data = build_dataset(series, cfg["horizons_h"])
        ctx.rows_received = len(data)
        summary = {}
        for k, fold in enumerate(cfg["folds"], start=1):
            train_to = pd.Timestamp(fold["train_to"], tz="UTC") + pd.Timedelta(hours=23)
            test_from = pd.Timestamp(fold["test_from"], tz="UTC")
            test_to = pd.Timestamp(fold["test_to"], tz="UTC") + pd.Timedelta(hours=23)
            d = add_profile(data, series, train_to)
            train = add_weather(d[d["target_utc"] <= train_to], weather, "actual")
            test = d[(d["origin_utc"] >= test_from) & (d["target_utc"] <= test_to)]
            test_fc = add_weather(test, weather, "hist_forecast")
            test_act = add_weather(test, weather, "actual")
            if train.empty or test.empty:
                log.warning("fold %s: нет данных (train %s, test %s)", k, len(train), len(test))
                continue
            model = fit_lgbm(train)
            a0 = test_fc["y_tm168"].fillna(test_fc["profile"]).to_numpy()
            preds = {
                "A0_seasonal_naive": a0,
                "A1_profile": test_fc["profile"].fillna(test_fc["lag0"]).to_numpy(),
                "A2_lightgbm": model.predict(test_fc[FEATURES]),
            }
            y = test_fc["y"].to_numpy()
            for name, p in preds.items():
                m = regression_metrics(y, p, a0)
                save_metrics(TASK, name, k, m)
                summary.setdefault(name, []).append(m)
                for h in cfg["horizons_h"]:
                    mask = (test_fc["h"] == h).to_numpy()
                    save_metrics(TASK, name, k, regression_metrics(y[mask], p[mask], a0[mask]), f"h={h}")
                for cls in ("AC", "DC"):
                    mask = (test_fc["power_class"] == cls).to_numpy()
                    save_metrics(TASK, name, k, regression_metrics(y[mask], p[mask], a0[mask]), f"class={cls}")
            # Абляция: насколько завышается качество, если на тесте подставить фактическую погоду
            save_metrics(TASK, "A2_lightgbm", k,
                         regression_metrics(y, model.predict(test_act[FEATURES]), a0), "weather=actual")
            _save_forecasts(test_fc, k, preds)
            log.info("fold %s: %s", k, {n: round(v[-1]["mae"], 4) for n, v in summary.items()})

        # Итоговая модель на всех данных — для прогноза в дашборде
        last = data["target_utc"].max()
        full = add_weather(add_profile(data, series, last), weather, "actual")
        model = fit_lgbm(full)
        path = Path(artifacts_dir()) / "canton_lgbm.txt"
        model.booster_.save_model(str(path))
        mean = {n: {m: float(np.mean([f[m] for f in v])) for m in ("mae", "rmse", "mase")} for n, v in summary.items()}
        register_model(TASK, "A2_lightgbm", {"features": FEATURES, "best_iteration": model.best_iteration_},
                       mean.get("A2_lightgbm", {}), str(path), frame_hash(series))
        ctx.rows_written = len(full)
        return mean
