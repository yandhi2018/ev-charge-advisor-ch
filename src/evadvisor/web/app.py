"""FastAPI-приложение. Каждый раздел работает под своей ролью БД:
водитель — app_driver, предметный дашборд — app_analyst, операционный и lineage — app_engineer.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from evadvisor import lineage, routing
from evadvisor.config import settings, source_cfg, timeout
from evadvisor.db import fetch_all, fetch_one
from evadvisor.recommender import Request as RecRequest
from evadvisor.recommender import recommend
from evadvisor.web import dashboards

log = logging.getLogger(__name__)
BASE = Path(__file__).parent
app = FastAPI(title="EV Charge Advisor CH", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


# ---------------------------------------------------------------- страницы
@app.get("/", response_class=HTMLResponse)
def driver_page(request: Request):
    cfg = settings()["recommender"]
    return templates.TemplateResponse(request, "driver.html", {
        "energy_options": cfg["energy_options_kwh"], "default_energy": cfg["default_energy_kwh"],
        "routing_engine": settings()["routing"]["engine"], "page": "driver"})


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, period: str = "90", canton: str = "", power_class: str = ""):
    ctx = dashboards.subject(period=period, canton=canton or None, power_class=power_class or None)
    return templates.TemplateResponse(request, "analytics.html", {**ctx, "page": "analytics"})


@app.get("/operations", response_class=HTMLResponse)
def operations_page(request: Request, days: int = 14, source: str = "", severity: str = ""):
    ctx = dashboards.operational(days=days, source=source or None, severity=severity or None)
    return templates.TemplateResponse(request, "operations.html", {**ctx, "page": "operations"})


@app.get("/lineage", response_class=HTMLResponse)
def lineage_page(request: Request, node: str = "metric.occupancy"):
    nodes, edges = lineage.upstream(node)
    choices = fetch_all("SELECT node_id, title FROM ops.lineage_node WHERE node_type IN ('metric', 'dashboard') "
                        "ORDER BY node_type, node_id", role="engineer")
    order = {"source": 0, "loader": 1, "raw": 2, "stg": 3, "transform": 4, "core": 5, "mart": 6, "model": 7,
             "metric": 8, "dashboard": 9}
    nodes = sorted(nodes, key=lambda n: (order.get(n["node_type"], 99), n["node_id"]))
    return templates.TemplateResponse(request, "lineage.html", {
        "node": node, "choices": choices, "nodes": nodes, "mermaid": lineage.mermaid(nodes, edges),
        "page": "lineage"})


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html", {"page": "about"})


# ---------------------------------------------------------------- API водителя
@app.get("/api/vehicles")
def api_vehicles():
    rows = fetch_all(
        """SELECT v.vehicle_id, v.brand, v.model, v.variant, v.release_year, v.ac_max_kw::float AS ac,
                  v.dc_max_kw::float AS dc, array_agg(DISTINCT p.plug_code) AS plugs
             FROM core.vehicle v JOIN core.vehicle_plug p USING (vehicle_id)
            GROUP BY v.vehicle_id ORDER BY v.brand, v.model, v.release_year DESC NULLS LAST, v.variant""",
        role="driver")
    return [{"id": r["vehicle_id"],
             "label": " ".join(str(x) for x in (r["brand"], r["model"], r["variant"], r["release_year"]) if x),
             "ac": r["ac"], "dc": r["dc"], "plugs": r["plugs"]} for r in rows]


@app.get("/api/geocode")
def api_geocode(q: str = Query(min_length=2, max_length=120)):
    cfg = source_cfg("geocoder")
    try:
        resp = requests.get(cfg["url"], params={"searchText": q, "type": "locations", "limit": 6, "sr": 4326},
                            timeout=timeout("geocoder"))
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(503, f"Геокодер недоступен: {exc}") from exc
    out = []
    for r in resp.json().get("results", []):
        a = r.get("attrs", {})
        out.append({"label": re.sub(r"<[^>]+>", "", a.get("label", "")), "lat": a.get("lat"), "lon": a.get("lon")})
    return out


class RecommendBody(BaseModel):
    vehicle_id: int
    lat: float = Field(ge=45.5, le=48.0)
    lon: float = Field(ge=5.5, le=11.0)
    energy_kwh: float = Field(20, gt=0, le=150)
    dc_only: bool = False
    open_24h: bool = False
    public_only: bool = False
    has_cable: bool = True


def _clean(obj):
    """NaN/inf → null, numpy-типы → Python: JSON не допускает NaN."""
    import math

    import numpy as np

    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


@app.post("/api/recommend")
def api_recommend(body: RecommendBody):
    try:
        res = recommend(RecRequest(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse(_clean({
        "request_id": res.request_id, "vehicle": res.vehicle, "status_age_min": res.status_age_min,
        "temperature_c": res.temperature_c, "radius_km": res.radius_km, "warnings": res.warnings,
        "items": res.items}))


@app.get("/api/route")
def api_route(lat: float, lon: float, lat2: float, lon2: float):
    route = routing.osrm_route(lat, lon, lat2, lon2)
    if route is None:
        return {"geometry": {"type": "LineString", "coordinates": [[lon, lat], [lon2, lat2]]}, "straight": True}
    return {**route, "straight": False}


@app.get("/api/station/{station_id}/profile")
def api_station_profile(station_id: int):
    """Типичная доля свободных точек станции по часам для текущего дня недели."""
    rows = fetch_all(
        """SELECT p.hour_local, avg(p.p_free) AS p_free
             FROM core.evse e JOIN mart.evse_profile p ON p.evse_id = e.evse_id
            WHERE e.station_id = %s AND e.is_active
              AND p.dow = (extract(isodow FROM now() AT TIME ZONE 'Europe/Zurich')::int - 1)
            GROUP BY 1 ORDER BY 1""", (station_id,), role="driver")
    if not rows:
        st = fetch_one("SELECT canton_code FROM core.station WHERE station_id = %s", (station_id,), role="driver")
        rows = fetch_all(
            """SELECT hour_local, avg(p_free) AS p_free FROM mart.profile_group
                WHERE canton_code = %s AND dow = (extract(isodow FROM now() AT TIME ZONE 'Europe/Zurich')::int - 1)
                GROUP BY 1 ORDER BY 1""", ((st or {}).get("canton_code"),), role="driver")
        return {"source": "canton", "hours": rows}
    return {"source": "station", "hours": rows}


@app.get("/health")
def health():
    row = fetch_one("SELECT max(slot_ts) AS slot, (SELECT count(*) FROM core.evse WHERE is_active) AS evse "
                    "FROM core.status_snapshot", role="driver")
    return {"ok": True, **row}
