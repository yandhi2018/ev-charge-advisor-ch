"""Данные и графики дашбордов. Каждый элемент отвечает на один вопрос пользователя (DESIGN.md, 6.4–6.5).

Предметный дашборд читает витрины под ролью аналитика, операционный — служебный слой под ролью инженера.
Цвета — проверенная палитра: категориальные слоты в фиксированном порядке (meta.slot — номер слота,
в браузере цвет подставляется из токенов темы), статусные цвета — только для состояний и всегда с подписью.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from evadvisor.db import fetch_all, fetch_one

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]           # категориальные слоты 1–3 (светлая тема)
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]   # последовательная шкала
STATUS = {"success": "#0ca30c", "partial": "#fab219", "skipped": "#898781", "failed": "#d03b3b",
          "running": "#2a78d6"}
DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

MODEL_NAMES = {
    "A0_seasonal_naive": "Как неделю назад (базовая)",
    "A1_profile": "Средний профиль",
    "A2_lightgbm": "LightGBM (рабочая)",
    "B0_persistence": "«Статус не изменится» (базовая)",
    "B1_profile": "История занятости точки",
    "B2_markov": "Марковская модель переходов",
    "B3_lightgbm": "LightGBM + переходы",
    "nearest": "Ближайшая подходящая",
    "nearest_free": "Ближайшая, свободная сейчас",
    "model": "Рекомендатель системы",
}
SOURCE_NAMES = {
    "archive": "Архив статусов 2024–2025", "archive_details": "Метаданные точек архива",
    "archive_profiles": "Профили доступности", "evse_data": "Справочник зарядных точек",
    "evse_status": "Текущие статусы точек", "weather": "Погода Open-Meteo", "vehicles": "Каталог автомобилей",
    "postal": "Почтовые индексы", "quality": "Проверки качества", "model_canton": "Модель загрузки (A)",
    "model_availability": "Модель «точка свободна» (B)", "backtest": "Бэктест рекомендателя",
}
STATUS_RU = {"success": ("готово", "good"), "partial": ("частично", "warn"), "skipped": ("без изменений", "neutral"),
             "failed": ("ошибка", "bad"), "running": ("выполняется", "run")}
CHECK_TYPES = {"completeness": "полнота", "uniqueness": "уникальность", "validity": "допустимость", "type": "тип",
               "range": "диапазон", "referential": "ссылочная целостность", "freshness": "актуальность"}

pio.templates["evadvisor"] = go.layout.Template(layout=go.Layout(
    font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=13, color="#52514e"),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", colorway=SERIES,
    xaxis=dict(gridcolor="#e1e0d9", linecolor="#c9c8c0", zeroline=False, automargin=True),
    yaxis=dict(gridcolor="#e1e0d9", linecolor="#c9c8c0", zeroline=False, automargin=True),
    legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, font=dict(size=12)),
    margin=dict(l=8, r=8, t=28, b=8), hoverlabel=dict(font_size=13), bargap=0.25,
))
pio.templates.default = "evadvisor"


def _html(fig: go.Figure, height: int = 320) -> str:
    fig.update_layout(height=height)
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, default_width="100%",
                       config={"displaylogo": False, "responsive": True, "displayModeBar": False})


def model_name(code: str) -> str:
    return MODEL_NAMES.get(code, code)


# ====================================================================== предметный
def subject(period: str = "90", canton: str | None = None, power_class: str | None = None) -> dict:
    bounds = fetch_one("SELECT min(hour_utc) AS lo, max(hour_utc) AS hi FROM mart.occupancy_hourly "
                       "WHERE source = 'archive'")
    cantons = [(r["code"], r["name"]) for r in
               fetch_all("SELECT trim(canton_code) AS code, name FROM core.canton ORDER BY name")]
    ctx = {"period": period, "canton": canton or "", "power_class": power_class or "", "cantons": cantons,
           "periods": [("30", "Последние 30 дней"), ("90", "Последние 90 дней"), ("365", "Последний год"),
                       ("all", "Весь архив")]}
    if not bounds or bounds["hi"] is None:
        ctx["empty"] = True
        return ctx
    hi = bounds["hi"]
    lo = bounds["lo"] if period == "all" else hi - timedelta(days=int(period))
    ctx["range_text"] = f"{lo:%d.%m.%Y} – {hi:%d.%m.%Y}"
    where, params = ["source = 'archive'", "hour_utc > %(lo)s", "hour_utc <= %(hi)s"], {"lo": lo, "hi": hi}
    if power_class:
        where.append("power_class = %(pc)s")
        params["pc"] = power_class
    w_all = " AND ".join(where)
    w_c = w_all + (" AND canton_code = %(c)s" if canton else "")
    if canton:
        params["c"] = canton
    ctx["scope"] = (dict(cantons).get(canton, canton) if canton else "вся Швейцария") + (
        {"AC": ", обычная зарядка", "DC": ", быстрая зарядка"}.get(power_class, ""))

    kpi = fetch_one(f"""
        SELECT sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occ,
               sum(avg_out_of_service) / nullif(sum(n_evse), 0) AS oos, avg(n_evse) AS n_evse
          FROM mart.occupancy_hourly WHERE {w_c}""", params)
    peak = fetch_one(f"""
        SELECT extract(hour FROM hour_utc AT TIME ZONE 'Europe/Zurich')::int AS h,
               sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occ
          FROM mart.occupancy_hourly WHERE {w_c} GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 1""", params)
    mae = fetch_one("""SELECT avg(value) FILTER (WHERE model_name = 'A2_lightgbm') AS a2,
                              avg(value) FILTER (WHERE model_name = 'A0_seasonal_naive') AS a0
                         FROM mart.model_metric WHERE task = 'canton' AND metric = 'mae' AND slice = 'all'""")
    ctx["kpi"] = {"occ": kpi["occ"], "oos": kpi["oos"], "n_evse": kpi["n_evse"],
                  "peak_hour": peak["h"] if peak else None, "peak_occ": peak["occ"] if peak else None,
                  "mae": mae["a2"] if mae else None, "mae_base": mae["a0"] if mae else None}

    # 1. Где загрузка выше всего? — рейтинг кантонов
    rows = pd.DataFrame(fetch_all(f"""
        SELECT trim(canton_code) AS canton, power_class,
               sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occ
          FROM mart.occupancy_hourly WHERE {w_all} GROUP BY 1, 2""", params))
    if not rows.empty:
        order = rows.groupby("canton")["occ"].mean().sort_values(ascending=False).index.tolist()
        fig = go.Figure()
        for i, pc in enumerate(["AC", "DC"]):
            d = rows[rows["power_class"] == pc].set_index("canton").reindex(order)
            if d["occ"].notna().any():
                fig.add_bar(x=order, y=d["occ"], name="Обычная (AC)" if pc == "AC" else "Быстрая (DC)",
                            marker_color=SERIES[i], meta={"slot": i},
                            hovertemplate="%{x}: %{y:.1%}<extra>" + pc + "</extra>")
        fig.update_layout(barmode="group", yaxis_tickformat=".0%", xaxis_tickangle=0, bargap=0.3)
        ctx["fig_ranking"] = _html(fig, 300)

    # 2. Когда пики? — тепловая карта день недели × час
    hm = pd.DataFrame(fetch_all(f"""
        SELECT (extract(isodow FROM hour_utc AT TIME ZONE 'Europe/Zurich')::int - 1) AS dow,
               extract(hour FROM hour_utc AT TIME ZONE 'Europe/Zurich')::int AS h,
               sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occ
          FROM mart.occupancy_hourly WHERE {w_c} GROUP BY 1, 2""", params))
    if not hm.empty:
        z = hm.pivot(index="dow", columns="h", values="occ").reindex(range(7))
        fig = go.Figure(go.Heatmap(z=z.values, x=[f"{h}:00" for h in z.columns], y=DOW,
                                   colorscale=[[i / (len(SEQ) - 1), c] for i, c in enumerate(SEQ)],
                                   xgap=2, ygap=2, colorbar=dict(tickformat=".0%", thickness=12, outlinewidth=0),
                                   hovertemplate="%{y}, %{x} — загрузка %{z:.1%}<extra></extra>"))
        fig.update_layout(yaxis_autorange="reversed", xaxis=dict(dtick=3, showgrid=False), yaxis_showgrid=False)
        ctx["fig_heatmap"] = _html(fig, 280)

    # 3. Насколько точен прогноз? — факт и прогноз (последний тестовый фолд, горизонт 6 ч)
    fc_canton, fc_class = canton or "ZH", power_class or "AC"
    fc = pd.DataFrame(fetch_all("""
        WITH f AS (SELECT max(fold) AS fold FROM mart.forecast_canton WHERE fold > 0)
        SELECT model_name, origin_utc + make_interval(hours => horizon_h) AS t, y_pred, y_true
          FROM mart.forecast_canton
         WHERE fold = (SELECT fold FROM f) AND horizon_h = 6 AND canton_code = %s AND power_class = %s
           AND origin_utc > (SELECT max(origin_utc) FROM mart.forecast_canton WHERE fold = (SELECT fold FROM f))
                            - interval '21 days'
         ORDER BY t""", (fc_canton, fc_class)))
    kind = "быстрая" if fc_class == "DC" else "обычная"
    ctx["fc_label"] = f"{dict(cantons).get(fc_canton, fc_canton)}, {kind} зарядка"
    if not fc.empty:
        fig = go.Figure()
        fact = fc[fc["model_name"] == fc["model_name"].iloc[0]]
        fig.add_scatter(x=fact["t"], y=fact["y_true"], name="Факт", mode="lines", meta={"slot": 0},
                        line=dict(color=SERIES[0], width=2))
        for i, (m, label) in enumerate([("A2_lightgbm", "Прогноз модели"), ("A0_seasonal_naive",
                                                                            "Как неделю назад")], start=1):
            d = fc[fc["model_name"] == m]
            fig.add_scatter(x=d["t"], y=d["y_pred"], name=label, mode="lines", meta={"slot": i},
                            line=dict(color=SERIES[i], width=2, dash="solid" if i == 1 else "dot"))
        fig.update_layout(yaxis_tickformat=".0%", hovermode="x unified")
        ctx["fig_forecast"] = _html(fig, 300)

    # 4. Влияет ли погода? — загрузка по интервалам температуры
    wt = pd.DataFrame(fetch_all(f"""
        SELECT width_bucket(w.temperature_c, -15, 35, 10) AS b,
               sum(o.avg_occupied) / nullif(sum(o.avg_available + o.avg_occupied), 0) AS occ, count(*) AS n
          FROM mart.occupancy_hourly o
          JOIN core.weather_hourly w ON w.canton_code = o.canton_code AND w.hour_utc = o.hour_utc
                                    AND w.kind = 'actual'
         WHERE {w_c.replace('source', 'o.source').replace('hour_utc', 'o.hour_utc')
                .replace('power_class', 'o.power_class').replace('canton_code', 'o.canton_code')}
         GROUP BY 1 HAVING count(*) > 50 ORDER BY 1""", params))
    if not wt.empty:
        def bucket(b: int) -> str:
            if b < 1:
                return "ниже −15°"
            if b > 10:
                return "выше 35°"
            return f"{-15 + (b - 1) * 5}…{-10 + (b - 1) * 5}°"

        fig = go.Figure(go.Bar(x=[bucket(b) for b in wt["b"]], y=wt["occ"], marker_color=SERIES[0], meta={"slot": 0},
                               customdata=wt["n"],
                               hovertemplate="%{x}: %{y:.1%} (часов: %{customdata:,})<extra></extra>"))
        fig.update_layout(yaxis_tickformat=".0%", bargap=0.35)
        ctx["fig_weather"] = _html(fig, 260)

    # 5. Где неисправности мешают спросу? — доля неисправных точек
    oos = pd.DataFrame(fetch_all(f"""
        SELECT trim(canton_code) AS canton, sum(avg_out_of_service) / nullif(sum(n_evse), 0) AS oos
          FROM mart.occupancy_hourly WHERE {w_all} GROUP BY 1 ORDER BY 2 DESC""", params))
    if not oos.empty:
        fig = go.Figure(go.Bar(x=oos["canton"], y=oos["oos"], marker_color=SERIES[0], meta={"slot": 0},
                               hovertemplate="%{x}: %{y:.1%} точек не работает<extra></extra>"))
        fig.update_layout(yaxis_tickformat=".0%", bargap=0.3)
        ctx["fig_oos"] = _html(fig, 260)

    # 6. Можно ли доверять прогнозу? — метрики моделей
    ctx["metrics_canton"] = [{**r, "label": model_name(r["model_name"])} for r in fetch_all("""
        SELECT model_name, avg(value) FILTER (WHERE metric = 'mae') AS mae,
               avg(value) FILTER (WHERE metric = 'rmse') AS rmse, avg(value) FILTER (WHERE metric = 'mase') AS mase,
               count(DISTINCT fold) AS folds
          FROM mart.model_metric WHERE task = 'canton' AND slice = 'all' GROUP BY 1 ORDER BY mae""")]
    ctx["metrics_availability"] = [{**r, "label": model_name(r["model_name"])} for r in fetch_all("""
        SELECT model_name, max(value) FILTER (WHERE metric = 'brier') AS brier,
               max(value) FILTER (WHERE metric = 'auc') AS auc, max(value) FILTER (WHERE metric = 'ece') AS ece
          FROM mart.model_metric WHERE task = 'availability' AND slice = 'all' GROUP BY 1 ORDER BY brier""")]
    # рабочая модель — лучшая на окне выбора (витрина доступна аналитику; ops.model_registry — нет, роль не расширяем)
    sel = fetch_one("""SELECT model_name FROM mart.model_metric
                        WHERE task = 'availability' AND slice = 'selection_window' AND metric = 'brier'
                        ORDER BY value LIMIT 1""")
    ctx["availability_selected"] = sel["model_name"] if sel else None
    ctx["metrics_recommender"] = [{**r, "label": model_name(r["model_name"])} for r in fetch_all("""
        SELECT model_name, max(value) FILTER (WHERE metric = 'hit_at_1') AS hit1,
               max(value) FILTER (WHERE metric = 'hit_at_3') AS hit3, max(value) FILTER (WHERE metric = 'n') AS n
          FROM mart.model_metric WHERE task = 'recommender' GROUP BY 1
         ORDER BY array_position(ARRAY['nearest', 'nearest_free', 'model'], model_name)""")]
    ctx["metrics_horizon"] = fetch_all("""
        SELECT replace(slice, 'h=', '')::int AS h,
               avg(value) FILTER (WHERE model_name = 'A0_seasonal_naive') AS a0,
               avg(value) FILTER (WHERE model_name = 'A2_lightgbm') AS a2
          FROM mart.model_metric WHERE task = 'canton' AND metric = 'mae' AND slice LIKE 'h=%%'
         GROUP BY 1 ORDER BY 1""")
    return ctx


# ====================================================================== операционный
SLA_HOURS = {"evse_data": 48, "weather": 30, "evse_status": 24, "vehicles": 24 * 30, "postal": 24 * 90,
             "archive": 24 * 365, "quality": 48, "model": 24 * 35, "backtest": 24 * 35}


def _ago(hours: float | None) -> str:
    if hours is None:
        return "никогда"
    if hours < 1:
        return f"{max(1, round(hours * 60))} мин назад"
    if hours < 48:
        return f"{hours:.0f} ч назад"
    return f"{hours / 24:.0f} дн. назад"


def operational(days: int = 14, source: str | None = None, severity: str | None = None) -> dict:
    role = "engineer"
    ctx = {"days": days, "source": source or "", "severity": severity or "", "source_names": SOURCE_NAMES}
    ctx["sources"] = [r["source"] for r in fetch_all("SELECT DISTINCT source FROM ops.load_run ORDER BY 1", role=role)]

    # 1. Всё ли загрузилось и насколько свежие данные? — плитки по источникам
    last = fetch_all("""
        SELECT DISTINCT ON (source) source, status, started_at, finished_at, rows_received, rows_written,
               rows_rejected, left(error_text, 220) AS error_text
          FROM ops.load_run ORDER BY source, started_at DESC""", role=role)
    ok = {r["source"]: r for r in fetch_all("""
        SELECT source, extract(epoch FROM now() - max(finished_at)) / 3600 AS ok_age_h
          FROM ops.load_run WHERE status IN ('success', 'partial') GROUP BY 1""", role=role)}
    tiles = []
    for r in last:
        age = ok.get(r["source"], {}).get("ok_age_h")
        sla = SLA_HOURS.get(r["source"], SLA_HOURS.get(r["source"].split("_")[0]))
        label, cls = STATUS_RU.get(r["status"], (r["status"], "neutral"))
        fresh = age is not None and sla is not None and age <= sla
        tiles.append({**r, "name": SOURCE_NAMES.get(r["source"], r["source"]), "status_label": label,
                      "status_cls": cls, "age": _ago(age), "fresh": fresh, "sla": sla,
                      "stale": sla is not None and not fresh and r["status"] != "running"})
    order = {"bad": 0, "run": 1, "warn": 2, "neutral": 3, "good": 4}
    ctx["tiles"] = sorted(tiles, key=lambda t: (order.get(t["status_cls"], 9), t["name"]))
    ctx["running"] = [t for t in tiles if t["status"] == "running"]
    ctx["n_problems"] = sum(1 for t in tiles if t["status_cls"] == "bad" or t["stale"])

    # 2. Нет ли аномалий объёма? — строки по запускам
    w, p = ["started_at > now() - make_interval(days => %(d)s)"], {"d": days}
    if source:
        w.append("source = %(s)s")
        p["s"] = source
    runs = pd.DataFrame(fetch_all(f"""
        SELECT source, started_at, status, rows_received, rows_written
          FROM ops.load_run WHERE {' AND '.join(w)} AND rows_received > 0 ORDER BY started_at""", p, role=role))
    if not runs.empty:
        fig = go.Figure()
        for st in ["success", "partial", "failed"]:
            d = runs[runs["status"] == st]
            if not d.empty:
                fig.add_scatter(x=d["started_at"], y=d["rows_received"], mode="markers", name=STATUS_RU[st][0],
                                marker=dict(size=10, color=STATUS[st], line=dict(width=1.5, color="#ffffff")),
                                customdata=[[SOURCE_NAMES.get(s, s), wr]
                                            for s, wr in zip(d["source"], d["rows_written"], strict=True)],
                                hovertemplate="%{customdata[0]}<br>%{x|%d.%m %H:%M}<br>получено %{y:,}, "
                                              "записано %{customdata[1]:,}<extra></extra>")
        fig.update_layout(yaxis_type="log", yaxis_title=None)
        ctx["fig_runs"] = _html(fig, 280)
    ctx["runs_table"] = [{**r, "name": SOURCE_NAMES.get(r["source"], r["source"]),
                          "status_label": STATUS_RU.get(r["status"], (r["status"], "neutral"))}
                         for r in fetch_all(f"""
        SELECT run_id, source, status, started_at, finished_at, rows_received, rows_written, rows_duplicate,
               rows_rejected, left(error_text, 160) AS error_text
          FROM ops.load_run WHERE {' AND '.join(w)} ORDER BY started_at DESC LIMIT 40""", p, role=role)]

    # 3. Где дыры в истории? — полнота архива по месяцам
    cov = pd.DataFrame(fetch_all("""
        SELECT month, n_slots::float / expected_slots AS slots, share_with_geo, share_unknown
          FROM mart.archive_coverage ORDER BY month""", role=role))
    if not cov.empty:
        fig = go.Figure()
        labels = [f"{m:%m.%Y}" for m in cov["month"]]
        for i, (col, name) in enumerate([("slots", "Полнота наблюдений"), ("share_with_geo", "Точки с кантоном"),
                                         ("share_unknown", "Статус «неизвестно»")]):
            fig.add_scatter(x=labels, y=cov[col], name=name, mode="lines+markers", meta={"slot": i},
                            line=dict(color=SERIES[i], width=2), marker=dict(size=7))
        fig.update_layout(yaxis_tickformat=".0%", hovermode="x unified", xaxis_type="category", xaxis_nticks=8)
        ctx["fig_coverage"] = _html(fig, 280)

    # 4. Что сломано и где? — последние результаты проверок качества
    sv = "AND c.severity = %(sev)s" if severity else ""
    dq = fetch_all(f"""
        SELECT DISTINCT ON (c.check_id) c.check_id, c.check_type, c.severity, c.table_name,
               c.description, r.passed, r.failed_rows, r.checked_at, r.sample::text AS sample
          FROM ops.dq_check c LEFT JOIN ops.dq_result r ON r.check_id = c.check_id
         WHERE true {sv}
         ORDER BY c.check_id, r.checked_at DESC NULLS LAST""", {"sev": severity}, role=role)
    for d in dq:
        d["type_ru"] = CHECK_TYPES.get(d["check_type"], d["check_type"])
        d["state"] = ("none" if d["passed"] is None else "ok" if d["passed"]
                      else "error" if d["severity"] == "error" else "warn")
    ctx["dq"] = sorted(dq, key=lambda d: ({"error": 0, "warn": 1, "none": 2, "ok": 3}[d["state"]], d["check_id"]))
    ctx["dq_counts"] = {s: sum(1 for d in dq if d["state"] == s) for s in ("ok", "warn", "error", "none")}
    ctx["dq_last"] = max((d["checked_at"] for d in dq if d["checked_at"]), default=None)

    # 5. Не поменял ли источник формат? — журнал структур
    ctx["schemas"] = [{**r, "name": SOURCE_NAMES.get(r["source"], r["source"])} for r in fetch_all("""
        SELECT source, left(fields_hash, 12) AS hash, jsonb_array_length(fields) AS n_fields, first_seen, last_seen,
               count(*) OVER (PARTITION BY source) AS versions
          FROM ops.source_schema ORDER BY source, first_seen DESC""", role=role)]

    # 6. Как меняется сеть? — изменения справочника точек (SCD2) и карантин
    ctx["scd2"] = fetch_all("""
        SELECT date_trunc('day', valid_from)::date AS day, change_type, count(*) AS n
          FROM core.evse_history WHERE valid_from > now() - make_interval(days => %(d)s)
         GROUP BY 1, 2 ORDER BY 1 DESC, 2""", {"d": days}, role=role)
    ctx["quarantine"] = fetch_all("""
        SELECT table_name, reason, count(*) AS n, max(created_at) AS last
          FROM ops.quarantine WHERE created_at > now() - make_interval(days => %(d)s)
         GROUP BY 1, 2 ORDER BY 3 DESC""", {"d": days}, role=role)

    # 7. Не деградировала ли модель? — диаграмма надёжности и журнал рекомендаций
    cal = pd.DataFrame(fetch_all("SELECT model_name, p_mean, y_mean, n FROM mart.calibration_bin "
                                 "WHERE model_name IN ('B2_markov', 'B0_persistence') ORDER BY model_name, bin",
                                 role=role))
    if not cal.empty:
        fig = go.Figure()
        fig.add_scatter(x=[0, 1], y=[0, 1], mode="lines", name="Идеально",
                        line=dict(color="#898781", width=1, dash="dot"), hoverinfo="skip")
        for i, m in enumerate(["B2_markov", "B0_persistence"]):
            d = cal[cal["model_name"] == m]
            fig.add_scatter(x=d["p_mean"], y=d["y_mean"], name=model_name(m), mode="lines+markers", meta={"slot": i},
                            line=dict(color=SERIES[i], width=2), marker=dict(size=8), customdata=d["n"],
                            hovertemplate="предсказано %{x:.0%} → было свободно %{y:.0%} "
                                          "(n=%{customdata:,})<extra></extra>")
        fig.update_layout(xaxis_tickformat=".0%", yaxis_tickformat=".0%", xaxis_title="Предсказанная вероятность",
                          yaxis_title="Фактическая доля свободных")
        ctx["fig_calibration"] = _html(fig, 300)
    ctx["rec_log"] = fetch_one("""
        SELECT count(*) AS n, count(*) FILTER (WHERE requested_at > now() - interval '1 day') AS n_day,
               max(requested_at) AS last FROM core.recommendation_request""", role=role)
    return ctx
