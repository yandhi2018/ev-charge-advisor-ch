"""Прогноз B: вероятность того, что зарядная точка будет свободна через Δ = 5…60 минут.

Цель: y = 1, если статус точки в момент t0 + Δ — Available (Unknown к приезду — 0). t0 — момент запроса.
Разбиение по времени (config.yaml: models.availability): обучение → валидация (калибровка) → тест.
Признаки — только доступные приложению в момент запроса (снимок API не содержит времени
смены статуса, поэтому длительность текущего статуса не используется).
Модели:
  B0 «статус не изменится» — 0,95, если точка свободна сейчас, иначе 0,05;
  B1 исторический профиль точки (день недели × час прибытия), запасной — профиль кантона и класса;
  B2 марковская модель переходов: P(Available через Δ | статус сейчас, класс, интервал часов);
  B3 LightGBM с изотонической калибровкой; признак p_markov — прогноз B2 (стекинг).
Профили и таблица переходов считаются только по обучающему окну. Валидационное окно делится пополам:
первая половина — ранняя остановка и калибровка B3, вторая — выбор рабочей модели (B2 или B3) для
рекомендателя. Тестовое окно в выборе не участвует.
"""

from __future__ import annotations

import json
import logging
import pickle
from datetime import date
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from evadvisor.config import ROOT, artifacts_dir, lake_dir, settings
from evadvisor.db import connect, session
from evadvisor.ingestion.archive import duck
from evadvisor.models.common import (
    calendar_features,
    calibration_table,
    classification_metrics,
    frame_hash,
    load_weather,
    register_model,
    save_metrics,
    seed,
    weather_wide,
)
from evadvisor.runlog import start_run

log = logging.getLogger(__name__)
TASK = "availability"
STATUS_CODES = {"Available": 0, "Occupied": 1, "OutOfService": 2, "Reserved": 3, "Unknown": 4}
FEATURES = ["status_code", "delta_min", "hour_local", "dow", "is_weekend", "is_holiday", "class_dc",
            "power_kw", "n_total_other", "n_free_other", "share_free_other", "p_free_evse", "p_free_group",
            "temp_c", "precip_mm", "snow_cm", "canton_cat", "p_markov"]
CANTONS = sorted(settings()["cantons"].keys())


# ---------------------------------------------------------------- выборка
def _dim() -> pd.DataFrame:
    with connect("engineer") as conn:
        rows = conn.execute(
            """SELECT evse_id, canton_code, power_class, power_kw::float AS power_kw, lat, lon
                 FROM core.archive_evse WHERE canton_code IS NOT NULL AND power_class IS NOT NULL""").fetchall()
    dim = pd.DataFrame(rows)
    dim["canton_code"] = dim["canton_code"].str.strip()
    dim["grp"] = dim["lat"].round(4).astype(str) + "," + dim["lon"].round(4).astype(str)
    return dim


def _months(d_from: str, d_to: str) -> list[str]:
    return [p.strftime("%Y-%m") for p in pd.period_range(d_from[:7], d_to[:7], freq="M")]


def build_samples(force: bool = False) -> pd.DataFrame:
    cfg = settings()["models"]["availability"]
    out_dir = lake_dir("mart_parts", "availability_samples")
    dim = _dim()
    con = duck()
    con.register("dim_df", dim[["evse_id", "grp", "canton_code", "power_class"]])
    con.execute("CREATE OR REPLACE TABLE dim AS SELECT * FROM dim_df")
    template = (ROOT / "db" / "duckdb" / "04_availability_sample.sql").read_text(encoding="utf-8")
    share_bp = int(round(cfg["sample_share"] * 10000))
    for month in _months(cfg["train_from"], cfg["test_to"]):
        src_dir = lake_dir("stg", "status_5min", f"month={month}")
        dst = out_dir / f"{month}.parquet"
        if not list(src_dir.glob("part-*.parquet")):
            log.warning("нет 5-минутных данных за %s — пропуск", month)
            continue
        if dst.exists() and not force:
            continue
        log.info("выборка B: %s", month)
        con.execute(template.format(src=(src_dir / "part-*.parquet").as_posix(), dst=dst.as_posix(),
                                    share_bp=share_bp))
    con.close()
    files = sorted(out_dir.glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["t0"] = pd.to_datetime(df["t0"], utc=True)
    return df.merge(dim[["evse_id", "canton_code", "power_class", "power_kw"]], on="evse_id", how="inner")


# ---------------------------------------------------------------- признаки
def train_profiles(months: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Профили по месяцам обучающего окна (частичные суммы из обработки архива)."""
    parts = [lake_dir("mart_parts", f"month={m}") / "profile_parts.parquet" for m in months]
    parts = [p for p in parts if p.exists()]
    prof = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    evse = prof.groupby(["evse_id", "dow", "hour_local"], as_index=False)[["n_free", "n_obs"]].sum()
    evse = evse[evse["n_obs"] >= 12]
    evse["p_free_evse"] = evse["n_free"] / evse["n_obs"]
    dim = _dim()[["evse_id", "canton_code", "power_class"]]
    grp = prof.merge(dim, on="evse_id").groupby(["canton_code", "power_class", "dow", "hour_local"],
                                                 as_index=False)[["n_free", "n_obs"]].sum()
    grp["p_free_group"] = grp["n_free"] / grp["n_obs"]
    return (evse[["evse_id", "dow", "hour_local", "p_free_evse"]],
            grp[["canton_code", "power_class", "dow", "hour_local", "p_free_group"]])


def make_features(df: pd.DataFrame, evse_prof: pd.DataFrame, grp_prof: pd.DataFrame,
                  weather: pd.DataFrame | None) -> pd.DataFrame:
    """Признаки по моменту прибытия t0 + Δ. Используется и при обучении, и в рекомендателе."""
    out = df.copy()
    out["arrival_utc"] = out["t0"] + pd.to_timedelta(out["delta_min"], unit="m")
    cal = calendar_features(out["arrival_utc"], out["canton_code"])
    for c in cal.columns:
        out[c] = cal[c].to_numpy()
    out["status_code"] = out["status_now"].map(STATUS_CODES).fillna(4).astype("int8")
    out["class_dc"] = (out["power_class"] == "DC").astype("int8")
    out["share_free_other"] = np.where(out["n_total_other"] > 0,
                                       out["n_free_other"] / out["n_total_other"].clip(lower=1), np.nan)
    out = out.drop(columns=["p_free_evse", "p_free_group"], errors="ignore")
    out = out.merge(evse_prof, on=["evse_id", "dow", "hour_local"], how="left")
    out = out.merge(grp_prof, on=["canton_code", "power_class", "dow", "hour_local"], how="left")
    out["canton_cat"] = out["canton_code"].map({c: i for i, c in enumerate(CANTONS)}).fillna(-1).astype("int16")
    cols = ["temp_c", "precip_mm", "snow_cm", "wind_kmh"]
    out = out.drop(columns=cols, errors="ignore")
    if weather is not None and not weather.empty:
        out["hour_utc"] = out["arrival_utc"].dt.floor("h")
        out = out.merge(weather, on=["canton_code", "hour_utc"], how="left").drop(columns="hour_utc")
    for c in cols:
        if c not in out:
            out[c] = np.nan
    return out


# ---------------------------------------------------------------- модели
def predict_b0(f: pd.DataFrame) -> np.ndarray:
    return np.where(f["status_code"] == 0, 0.95, 0.05)


def predict_b1(f: pd.DataFrame) -> np.ndarray:
    return f["p_free_evse"].fillna(f["p_free_group"]).fillna(0.75).to_numpy()


def fit_markov(train: pd.DataFrame) -> pd.DataFrame:
    """Эмпирические вероятности перехода в Available; level 1 — подробная, level 0 — запасная таблица."""
    t = train.assign(hour_bucket=train["hour_local"] // 4)
    full = t.groupby(["status_code", "class_dc", "hour_bucket", "delta_min"])["y"].agg(["mean", "count"])
    full = full[full["count"] >= 50]["mean"].rename("p_markov").reset_index().assign(level=1)
    coarse = t.groupby(["status_code", "delta_min"])["y"].mean().rename("p_markov").reset_index().assign(level=0)
    return pd.concat([full, coarse], ignore_index=True)


def predict_b2(f: pd.DataFrame, table: pd.DataFrame) -> np.ndarray:
    keys = ["status_code", "class_dc", "hour_bucket", "delta_min"]
    g = f.assign(hour_bucket=f["hour_local"] // 4)[keys]
    full = table[table["level"] == 1][keys + ["p_markov"]]
    coarse = table[table["level"] == 0][["status_code", "delta_min", "p_markov"]].rename(
        columns={"p_markov": "p_coarse"})
    g = g.merge(full, on=keys, how="left").merge(coarse, on=["status_code", "delta_min"], how="left")
    return g["p_markov"].fillna(g["p_coarse"]).fillna(0.75).to_numpy()


def fit_b3(train: pd.DataFrame, valid: pd.DataFrame) -> tuple[lgb.LGBMClassifier, IsotonicRegression]:
    model = lgb.LGBMClassifier(n_estimators=2000, learning_rate=0.05, num_leaves=127, min_child_samples=200,
                               subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                               random_state=seed(), verbose=-1)
    model.fit(train[FEATURES], train["y"], eval_X=(valid[FEATURES],), eval_y=(valid["y"],),
              callbacks=[lgb.early_stopping(50, verbose=False)])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    iso.fit(model.predict_proba(valid[FEATURES])[:, 1], valid["y"])
    return model, iso


def _split(df: pd.DataFrame, a: str, b: str) -> pd.DataFrame:
    lo = pd.Timestamp(a, tz="UTC")
    hi = pd.Timestamp(b, tz="UTC") + pd.Timedelta(days=1)
    return df[(df["t0"] >= lo) & (df["t0"] < hi)]


def evaluate(force_samples: bool = False) -> dict:
    cfg = settings()["models"]["availability"]
    with start_run("model_availability", {k: cfg[k] for k in cfg}) as ctx:
        samples = build_samples(force_samples)
        ctx.rows_received = len(samples)
        evse_prof, grp_prof = train_profiles(_months(cfg["train_from"], cfg["train_to"]))
        weather = load_weather(("actual", "hist_forecast"))
        w_act, w_fc = weather_wide(weather, "actual"), weather_wide(weather, "hist_forecast")
        train = make_features(_split(samples, cfg["train_from"], cfg["train_to"]), evse_prof, grp_prof, w_act)
        valid = make_features(_split(samples, cfg["valid_from"], cfg["valid_to"]), evse_prof, grp_prof, w_fc)
        test = make_features(_split(samples, cfg["test_from"], cfg["test_to"]), evse_prof, grp_prof, w_fc)
        if len(train) > 4_000_000:
            train = train.sample(4_000_000, random_state=seed())
        log.info("B: train %s, valid %s, test %s", len(train), len(valid), len(test))

        markov = fit_markov(train)
        for df in (train, valid, test):
            df["p_markov"] = predict_b2(df, markov)
        mid = (pd.Timestamp(cfg["valid_from"]) + (pd.Timestamp(cfg["valid_to"]) - pd.Timestamp(cfg["valid_from"])) / 2)
        calib = valid[valid["t0"] < mid.tz_localize("UTC")]
        select = valid[valid["t0"] >= mid.tz_localize("UTC")]
        model, iso = fit_b3(train, calib)
        # выбор рабочей модели по второй половине валидации (тест не используется)
        sel_scores = {
            "B2_markov": classification_metrics(select["y"].to_numpy(), select["p_markov"].to_numpy())["brier"],
            "B3_lightgbm": classification_metrics(
                select["y"].to_numpy(), iso.predict(model.predict_proba(select[FEATURES])[:, 1]))["brier"],
        }
        selected = min(sel_scores, key=sel_scores.get)
        for name, brier in sel_scores.items():
            save_metrics(TASK, name, 1, {"brier": brier}, "selection_window")
        log.info("выбор модели на валидации: %s → %s", sel_scores, selected)
        y = test["y"].to_numpy()
        preds = {
            "B0_persistence": predict_b0(test),
            "B1_profile": predict_b1(test),
            "B2_markov": predict_b2(test, markov),
            "B3_lightgbm": iso.predict(model.predict_proba(test[FEATURES])[:, 1]),
        }
        summary = {}
        with session("engineer") as conn:
            conn.execute("DELETE FROM mart.calibration_bin WHERE model_name = ANY(%s)", (list(preds),))
        for name, p in preds.items():
            m = classification_metrics(y, p)
            summary[name] = m
            save_metrics(TASK, name, 1, m)
            for status, code in STATUS_CODES.items():
                mask = (test["status_code"] == code).to_numpy()
                if mask.sum() > 100:
                    save_metrics(TASK, name, 1, classification_metrics(y[mask], p[mask]), f"status={status}")
            for lo, hi in ((5, 15), (20, 35), (40, 60)):
                mask = test["delta_min"].between(lo, hi).to_numpy()
                save_metrics(TASK, name, 1, classification_metrics(y[mask], p[mask]), f"delta={lo}-{hi}")
            with session("engineer") as conn:
                for row in calibration_table(y, p):
                    conn.execute("INSERT INTO mart.calibration_bin (model_name, bin, p_mean, y_mean, n) "
                                 "VALUES (%s, %s, %s, %s, %s)", (name, row["bin"], row["p_mean"], row["y_mean"],
                                                                row["n"]))
            log.info("%s: brier %.4f auc %.3f ece %.4f", name, m["brier"], m["auc"], m["ece"])

        # Важность признаков (для интерпретации в отчёте)
        importance = dict(zip(FEATURES, model.booster_.feature_importance("gain").round(1).tolist(), strict=True))
        save_metrics(TASK, "B3_lightgbm", 1, {f"gain_{k}": v for k, v in importance.items()}, "importance")

        # Артефакты для рекомендателя: модель, калибратор, таблица переходов, профили обучения не нужны —
        # в работе используются профили по всей истории (mart.evse_profile / mart.profile_group).
        art = Path(artifacts_dir())
        model.booster_.save_model(str(art / "availability_lgbm.txt"))
        with open(art / "availability_isotonic.pkl", "wb") as f:
            pickle.dump(iso, f)
        markov.to_parquet(art / "availability_markov.parquet")
        (art / "availability_meta.json").write_text(json.dumps({
            "features": FEATURES, "cantons": CANTONS, "status_codes": STATUS_CODES,
            "trained": date.today().isoformat(), "selected": selected, "selection_brier": sel_scores,
            "metrics": summary}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        register_model(TASK, selected, {"features": FEATURES, "best_iteration": model.best_iteration_,
                                        "selection_brier": sel_scores},
                       summary[selected], str(art / "availability_lgbm.txt"), frame_hash(samples.head(100000)))
        ctx.rows_written = len(test)
        return summary
