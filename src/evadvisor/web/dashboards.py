"""Данные и графики дашбордов. Каждый элемент отвечает на один вопрос пользователя (DESIGN.md, 6.4–6.5).

Предметный дашборд читает витрины под ролью аналитика, операционный — служебный слой под ролью инженера.
Цвета — эталонная проверенная палитра (категориальные слоты в фиксированном порядке, статусные цвета
только для состояний и всегда с подписью).
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from evadvisor.db import fetch_all, fetch_one

SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]           # категориальные слоты 1–3
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]   # последовательная шкала
STATUS = {"success": "#0ca30c", "partial": "#fab219", "skipped": "#898781", "failed": "#d03b3b",
          "running": "#2a78d6"}
INK, MUTED, GRID = "#52514e", "#898781", "rgba(137,135,129,0.25)"
DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

pio.templates["evadvisor"] = go.layout.Template(layout=go.Layout(
    font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=13, color=INK),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", colorway=SERIES,
    xaxis=dict(gridcolor=GRID, linecolor=MUTED, zeroline=False, tickfont=dict(color=MUTED)),
    yaxis=dict(gridcolor=GRID, linecolor=MUTED, zeroline=False, tickfont=dict(color=MUTED)),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=INK)),
    margin=dict(l=48, r=16, t=36, b=40), hoverlabel=dict(font_size=13), bargap=0.25,
))
pio.templates.default = "evadvisor"


def _html(fig: go.Figure, height: int = 320) -> str:
    fig.update_layout(height=height)
    return pio.to_html(fig, full_html=False, include_plotlyjs=False,
                       config={"displaylogo": False, "responsive": True,
                               "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]})


def _empty(text: str) -> str:
    return f'<p class="empty">{text}</p>'


# ====================================================================== предметный
def subject(period: str = "90", canton: str | None = None, power_class: str | None = None) -> dict:
    bounds = fetch_one("SELECT min(hour_utc) AS lo, max(hour_utc) AS hi FROM mart.occupancy_hourly "
                       "WHERE source = 'archive'")
    cantons = [r["canton_code"].strip() for r in fetch_all("SELECT canton_code FROM core.canton ORDER BY 1")]
    ctx = {"period": period, "canton": canton or "", "power_class": power_class or "", "cantons": cantons,
           "periods": [("30", "30 дней"), ("90", "90 дней"), ("365", "Год"), ("all", "Весь архив")]}
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

    # KPI: средняя загрузка, пиковый час, доля неисправных — по выбранному срезу
    kpi = fetch_one(f"""
        SELECT sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occ,
               sum(avg_out_of_service) / nullif(sum(n_evse), 0) AS oos,
               avg(n_evse) AS n_evse
          FROM mart.occupancy_hourly WHERE {w_c}""", params)
    peak = fetch_one(f"""
        SELECT extract(hour FROM hour_utc AT TIME ZONE 'Europe/Zurich')::int AS h,
               sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occ
          FROM mart.occupancy_hourly WHERE {w_c} GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 1""", params)
    ctx["kpi"] = {"occ": kpi["occ"], "oos": kpi["oos"], "peak_hour": peak["h"] if peak else None,
                  "peak_occ": peak["occ"] if peak else None}

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
                fig.add_bar(x=order, y=d["occ"], name="AC — медленная" if pc == "AC" else "DC — быстрая",
                            marker_color=SERIES[i], hovertemplate="%{x}: %{y:.1%}<extra>" + pc + "</extra>")
        fig.update_layout(barmode="group", yaxis_tickformat=".0%", yaxis_title="Загрузка")
        ctx["fig_ranking"] = _html(fig)

    # 2. Когда пики? — тепловая карта день недели × час
    hm = pd.DataFrame(fetch_all(f"""
        SELECT (extract(isodow FROM hour_utc AT TIME ZONE 'Europe/Zurich')::int - 1) AS dow,
               extract(hour FROM hour_utc AT TIME ZONE 'Europe/Zurich')::int AS h,
               sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occ
          FROM mart.occupancy_hourly WHERE {w_c} GROUP BY 1, 2""", params))
    if not hm.empty:
        z = hm.pivot(index="dow", columns="h", values="occ").reindex(range(7))
        fig = go.Figure(go.Heatmap(z=z.values, x=[f"{h:02d}" for h in z.columns], y=DOW,
                                   colorscale=[[i / (len(SEQ) - 1), c] for i, c in enumerate(SEQ)],
                                   xgap=2, ygap=2, colorbar=dict(tickformat=".0%", title=""),
                                   hovertemplate="%{y}, %{x}:00 — %{z:.1%}<extra></extra>"))
        fig.update_layout(yaxis_autorange="reversed", xaxis_title="Час (местное время)")
        ctx["fig_heatmap"] = _html(fig, 300)

    # 3. Насколько точен прогноз на сутки? — факт и прогноз (последний тестовый фолд, горизонт 6 ч)
    fc_canton, fc_class = canton or "ZH", power_class or "AC"
    fc = pd.DataFrame(fetch_all("""
        WITH f AS (SELECT max(fold) AS fold FROM mart.forecast_canton WHERE fold > 0)
        SELECT model_name, origin_utc + make_interval(hours => horizon_h) AS t, y_pred, y_true
          FROM mart.forecast_canton
         WHERE fold = (SELECT fold FROM f) AND horizon_h = 6 AND canton_code = %s AND power_class = %s
           AND origin_utc > (SELECT max(origin_utc) FROM mart.forecast_canton WHERE fold = (SELECT fold FROM f))
                            - interval '21 days'
         ORDER BY t""", (fc_canton, fc_class)))
    ctx["fc_label"] = f"{fc_canton}, {fc_class}"
    if not fc.empty:
        fig = go.Figure()
        fact = fc[fc["model_name"] == fc["model_name"].iloc[0]]
        fig.add_scatter(x=fact["t"], y=fact["y_true"], name="Факт", mode="lines",
                        line=dict(color=SERIES[0], width=2))
        for i, (m, label) in enumerate([("A2_lightgbm", "Прогноз LightGBM"), ("A0_seasonal_naive",
                                                                              "Неделю назад (база)")], start=1):
            d = fc[fc["model_name"] == m]
            fig.add_scatter(x=d["t"], y=d["y_pred"], name=label, mode="lines",
                            line=dict(color=SERIES[i], width=2, dash="solid" if i == 1 else "dot"))
        fig.update_layout(yaxis_tickformat=".0%", hovermode="x unified")
        ctx["fig_forecast"] = _html(fig)

    # 4. Влияет ли погода? — загрузка по интервалам температуры
    wt = pd.DataFrame(fetch_all(f"""
        SELECT width_bucket(w.temperature_c, -15, 35, 10) AS b,
               sum(o.avg_occupied) / nullif(sum(o.avg_available + o.avg_occupied), 0) AS occ,
               count(*) AS n
          FROM mart.occupancy_hourly o
          JOIN core.weather_hourly w ON w.canton_code = o.canton_code AND w.hour_utc = o.hour_utc
                                    AND w.kind = 'actual'
         WHERE {w_c.replace('source', 'o.source').replace('hour_utc', 'o.hour_utc')
                .replace('power_class', 'o.power_class').replace('canton_code', 'o.canton_code')}
         GROUP BY 1 HAVING count(*) > 50 ORDER BY 1""", params))
    if not wt.empty:
        def bucket(b: int) -> str:
            if b < 1:
                return "< −15 °C"
            if b > 10:
                return "> 35 °C"
            return f"{-15 + (b - 1) * 5}…{-10 + (b - 1) * 5} °C"

        labels = [bucket(b) for b in wt["b"]]
        fig = go.Figure(go.Bar(x=labels, y=wt["occ"], marker_color=SERIES[0], customdata=wt["n"],
                               hovertemplate="%{x}: %{y:.1%} (часов: %{customdata})<extra></extra>"))
        fig.update_layout(yaxis_tickformat=".0%", yaxis_title="Загрузка")
        ctx["fig_weather"] = _html(fig, 280)

    # 5. Где неисправности мешают спросу? — доля неисправных точек по кантонам
    oos = pd.DataFrame(fetch_all(f"""
        SELECT trim(canton_code) AS canton, sum(avg_out_of_service) / nullif(sum(n_evse), 0) AS oos
          FROM mart.occupancy_hourly WHERE {w_all} GROUP BY 1 ORDER BY 2 DESC""", params))
    if not oos.empty:
        fig = go.Figure(go.Bar(x=oos["canton"], y=oos["oos"], marker_color=SERIES[0],
                               hovertemplate="%{x}: %{y:.1%}<extra></extra>"))
        fig.update_layout(yaxis_tickformat=".0%", yaxis_title="Доля OutOfService")
        ctx["fig_oos"] = _html(fig, 280)

    # 6. Можно ли доверять прогнозу? — метрики моделей
    ctx["metrics_canton"] = fetch_all("""
        SELECT model_name, avg(value) FILTER (WHERE metric = 'mae') AS mae,
               avg(value) FILTER (WHERE metric = 'rmse') AS rmse, avg(value) FILTER (WHERE metric = 'mase') AS mase,
               count(DISTINCT fold) AS folds
          FROM mart.model_metric WHERE task = 'canton' AND slice = 'all' GROUP BY 1 ORDER BY 1""")
    ctx["metrics_availability"] = fetch_all("""
        SELECT model_name, max(value) FILTER (WHERE metric = 'brier') AS brier,
               max(value) FILTER (WHERE metric = 'auc') AS auc, max(value) FILTER (WHERE metric = 'ece') AS ece,
               max(value) FILTER (WHERE metric = 'logloss') AS logloss
          FROM mart.model_metric WHERE task = 'availability' AND slice = 'all' GROUP BY 1 ORDER BY 1""")
    ctx["metrics_horizon"] = fetch_all("""
        SELECT slice, avg(value) FILTER (WHERE model_name = 'A0_seasonal_naive') AS a0,
               avg(value) FILTER (WHERE model_name = 'A1_profile') AS a1,
               avg(value) FILTER (WHERE model_name = 'A2_lightgbm') AS a2
          FROM mart.model_metric WHERE task = 'canton' AND metric = 'mae' AND slice LIKE 'h=%%'
         GROUP BY 1 ORDER BY replace(slice, 'h=', '')::int""")
    return ctx


# ====================================================================== операционный
SLA_HOURS = {"evse_data": 48, "weather": 30, "evse_status": 24, "vehicles": 24 * 30, "postal": 24 * 90,
             "archive": 24 * 365, "quality": 48}


def operational(days: int = 14, source: str | None = None, severity: str | None = None) -> dict:
    role = "engineer"
    ctx = {"days": days, "source": source or "", "severity": severity or ""}
    sources = [r["source"] for r in fetch_all("SELECT DISTINCT source FROM ops.load_run ORDER BY 1", role=role)]
    ctx["sources"] = sources

    # 1. Всё ли загрузилось и насколько свежие данные? — светофор по источникам
    last = fetch_all("""
        SELECT DISTINCT ON (source) source, status, started_at, finished_at, rows_received, rows_written,
               rows_rejected, left(error_text, 200) AS error_text,
               extract(epoch FROM now() - coalesce(finished_at, started_at)) / 3600 AS age_h
          FROM ops.load_run ORDER BY source, started_at DESC""", role=role)
    ok = {r["source"]: r for r in fetch_all("""
        SELECT source, max(finished_at) AS last_ok,
               extract(epoch FROM now() - max(finished_at)) / 3600 AS ok_age_h
          FROM ops.load_run WHERE status IN ('success', 'partial') GROUP BY 1""", role=role)}
    for r in last:
        o = ok.get(r["source"], {})
        r["last_ok"], r["ok_age_h"] = o.get("last_ok"), o.get("ok_age_h")
        sla = SLA_HOURS.get(r["source"].split("_")[0] if r["source"] not in SLA_HOURS else r["source"])
        r["sla_h"] = sla
        r["fresh"] = (r["ok_age_h"] is not None and sla is not None and r["ok_age_h"] <= sla)
    ctx["sources_last"] = last

    # 2. Нет ли аномалий объёма? — строки по запускам
    w, p = ["started_at > now() - make_interval(days => %(d)s)"], {"d": days}
    if source:
        w.append("source = %(s)s")
        p["s"] = source
    runs = pd.DataFrame(fetch_all(f"""
        SELECT source, started_at, status, rows_received, rows_written
          FROM ops.load_run WHERE {' AND '.join(w)} ORDER BY started_at""", p, role=role))
    if not runs.empty:
        fig = go.Figure()
        for st in ["success", "partial", "skipped", "failed"]:
            d = runs[runs["status"] == st]
            if not d.empty:
                fig.add_scatter(x=d["started_at"], y=d["rows_received"], mode="markers", name=st,
                                marker=dict(size=9, color=STATUS[st], line=dict(width=2, color="#fcfcfb")),
                                customdata=d[["source", "rows_written"]],
                                hovertemplate="%{customdata[0]}<br>%{x}<br>получено %{y:,}, записано "
                                              "%{customdata[1]:,}<extra>" + st + "</extra>")
        fig.update_layout(yaxis_title="Строк получено", yaxis_type="log")
        ctx["fig_runs"] = _html(fig, 300)
    ctx["runs_table"] = fetch_all(f"""
        SELECT run_id, source, status, started_at, finished_at, rows_received, rows_written, rows_duplicate,
               rows_rejected, raw_path, left(error_text, 160) AS error_text
          FROM ops.load_run WHERE {' AND '.join(w)} ORDER BY started_at DESC LIMIT 40""", p, role=role)

    # 3. Где дыры в истории? — полнота архива по месяцам
    cov = pd.DataFrame(fetch_all("""
        SELECT month, n_slots::float / expected_slots AS slots, share_with_geo, share_unknown, n_evse
          FROM mart.archive_coverage ORDER BY month""", role=role))
    if not cov.empty:
        fig = go.Figure()
        labels = [f"{m:%Y-%m}" for m in cov["month"]]
        for i, (col, name) in enumerate([("slots", "Полнота 5-минутных слотов"),
                                         ("share_with_geo", "Точки с кантоном и классом"),
                                         ("share_unknown", "Доля статуса Unknown")]):
            fig.add_scatter(x=labels, y=cov[col], name=name, mode="lines+markers",
                            line=dict(color=SERIES[i], width=2), marker=dict(size=8))
        fig.update_layout(yaxis_tickformat=".0%", hovermode="x unified")
        ctx["fig_coverage"] = _html(fig, 300)

    # 4. Что сломано и где? — последние результаты проверок качества
    sv = "AND c.severity = %(sev)s" if severity else ""
    ctx["dq_latest"] = fetch_all(f"""
        SELECT DISTINCT ON (c.check_id) c.check_id, c.check_type, c.severity, c.layer, c.table_name,
               c.description, r.passed, r.failed_rows, r.checked_at, r.sample::text AS sample
          FROM ops.dq_check c LEFT JOIN ops.dq_result r ON r.check_id = c.check_id
         WHERE true {sv}
         ORDER BY c.check_id, r.checked_at DESC NULLS LAST""", {"sev": severity}, role=role)
    trend = pd.DataFrame(fetch_all("""
        SELECT date_trunc('hour', r.checked_at) AS t, c.check_type,
               count(*) FILTER (WHERE NOT r.passed) AS failed, count(*) AS total
          FROM ops.dq_result r JOIN ops.dq_check c USING (check_id)
         WHERE r.checked_at > now() - make_interval(days => %(d)s) GROUP BY 1, 2 ORDER BY 1""",
        {"d": days}, role=role))
    if not trend.empty:
        t = trend.groupby("t")[["failed", "total"]].sum().reset_index()
        fig = go.Figure(go.Bar(x=t["t"], y=t["total"] - t["failed"], name="Пройдено", marker_color=STATUS["success"]))
        fig.add_bar(x=t["t"], y=t["failed"], name="Не пройдено", marker_color=STATUS["failed"])
        fig.update_layout(barmode="stack", yaxis_title="Проверок")
        ctx["fig_dq"] = _html(fig, 260)

    # 5. Не поменял ли источник формат? — журнал структур
    ctx["schemas"] = fetch_all("""
        SELECT source, left(fields_hash, 12) AS hash, jsonb_array_length(fields) AS n_fields, first_seen, last_seen
          FROM ops.source_schema ORDER BY source, first_seen DESC""", role=role)

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
                                 "WHERE model_name IN ('B3_lightgbm', 'B1_profile') ORDER BY model_name, bin",
                                 role=role))
    if not cal.empty:
        fig = go.Figure()
        fig.add_scatter(x=[0, 1], y=[0, 1], mode="lines", name="Идеальная калибровка",
                        line=dict(color=MUTED, width=1, dash="dot"), hoverinfo="skip")
        for i, (m, label) in enumerate([("B3_lightgbm", "LightGBM + изотоническая"), ("B1_profile", "Профиль")]):
            d = cal[cal["model_name"] == m]
            fig.add_scatter(x=d["p_mean"], y=d["y_mean"], name=label, mode="lines+markers",
                            line=dict(color=SERIES[i], width=2), marker=dict(size=8), customdata=d["n"],
                            hovertemplate="прогноз %{x:.0%} → факт %{y:.0%} (n=%{customdata:,})<extra></extra>")
        fig.update_layout(xaxis_tickformat=".0%", yaxis_tickformat=".0%", xaxis_title="Предсказанная вероятность",
                          yaxis_title="Доля свободных")
        ctx["fig_calibration"] = _html(fig, 320)
    ctx["rec_log"] = fetch_one("""
        SELECT count(*) AS n, count(*) FILTER (WHERE requested_at > now() - interval '1 day') AS n_day,
               avg(n_results) AS avg_results, max(requested_at) AS last FROM core.recommendation_request""",
                               role=role)
    return ctx
