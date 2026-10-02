"""Модульные тесты без БД: разбор источников, метрики, каталоги проверок и lineage."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
import yaml

from evadvisor.config import ROOT
from evadvisor.ingestion import evse_data, evse_status, vehicles, weather
from evadvisor.models.common import calendar_features, classification_metrics, regression_metrics


def _record(**over):
    rec = {
        "EvseID": "CH*CCI*E22078", "Plugs": ["Type 2 Outlet"], "IsOpen24Hours": True,
        "Accessibility": "Paying publicly accessible", "ChargingStationId": "CH*CCI*E22078",
        "Address": {"City": "Meyrin", "PostalCode": "1217", "Street": "Esplanade des Particules"},
        "GeoCoordinates": {"Google": "46.23432 6.055602"},
        "ChargingFacilities": [{"power": "22.0", "powertype": "AC_3_PHASE"}],
        "ChargingStationNames": [{"lang": "en", "value": "SIG CERN"}],
    }
    rec.update(over)
    return rec


OPERATOR = {"OperatorID": "CH*CCC", "OperatorName": "Move"}


def test_evse_row_parses_core_fields():
    row = dict(zip(evse_data.COLUMNS, evse_data.to_row("run", OPERATOR, _record()), strict=True))
    assert row["evse_id"] == "CH*CCI*E22078"
    assert (row["lat"], row["lon"]) == (46.23432, 6.055602)
    assert row["power_kw"] == 22.0 and row["power_type"] == "AC_3_PHASE"
    assert row["plugs"] == ["Type 2 Outlet"]
    assert len(row["record_hash"]) == 64


def test_evse_row_handles_numeric_postal_code_and_watts():
    rec = _record(Address={"PostalCode": 3011, "Street": "Bahnhofplatz", "City": "Bern"},
                  ChargingFacilities=[{"power": "150000", "powertype": "DC"}])
    row = dict(zip(evse_data.COLUMNS, evse_data.to_row("run", OPERATOR, rec), strict=True))
    assert row["postal_code"] == "3011"
    assert row["power_kw"] == 150.0          # ватты приведены к киловаттам
    assert row["power_type"] == "DC"


def test_evse_row_bad_coordinates_become_null():
    rec = _record(GeoCoordinates={"Google": "None None"})
    row = dict(zip(evse_data.COLUMNS, evse_data.to_row("run", OPERATOR, rec), strict=True))
    assert row["lat"] is None and row["lon"] is None


def test_record_hash_ignores_insignificant_fields():
    a = evse_data.to_row("r1", OPERATOR, _record(lastUpdate="2026-01-01T00:00:00Z"))
    b = evse_data.to_row("r2", OPERATOR, _record(lastUpdate="2026-09-01T00:00:00Z"))
    c = evse_data.to_row("r3", OPERATOR, _record(Plugs=["CCS Combo 2 Plug (Cable Attached)"]))
    assert a[-1] == b[-1]          # смена только lastUpdate не порождает новую версию
    assert a[-1] != c[-1]          # смена разъёма — порождает


def test_status_slot_is_aligned_to_five_minutes():
    slot = evse_status.current_slot(datetime(2026, 10, 2, 13, 47, 31, tzinfo=UTC))
    assert slot == datetime(2026, 10, 2, 13, 45, tzinfo=UTC)


def test_weather_rows():
    payload = {"hourly": {"time": ["2025-01-01T00:00", "2025-01-01T01:00"], "temperature_2m": [-2.5, -3.0],
                          "precipitation": [0, 0.2], "snowfall": [0, 0.1], "wind_speed_10m": [5, 7]}}
    rows = weather._rows("run", "BE", "actual", payload)
    assert len(rows) == 2 and rows[0][2] == datetime(2025, 1, 1, tzinfo=UTC)
    assert rows[1][4:] == [-3.0, 0.2, 0.1, 7]


def test_open_ev_data_parser_keeps_only_cars_and_merges_ports():
    payload = {"data": [
        {"id": "1", "brand": "Tesla", "model": "Model 3", "variant": "LR", "release_year": 2024, "vehicle_type": "car",
         "usable_battery_size": 75, "ac_charger": {"ports": ["type2"], "max_power": 11},
         "dc_charger": {"ports": ["ccs"], "max_power": 250}},
        {"id": "2", "brand": "Zero", "model": "SR/F", "vehicle_type": "motorbike", "ac_charger": {"ports": ["type2"]}},
    ]}
    rows = vehicles.parse_open_ev_data("run", payload)
    assert len(rows) == 1
    assert rows[0][-1] == ["ccs", "type2"] and rows[0][8] == 11 and rows[0][9] == 250


def test_manual_vehicle_catalogue_is_valid():
    rows = vehicles.parse_manual("run")
    assert rows and all(r[8] and r[8] > 0 for r in rows)


def test_calendar_features_swiss_holiday_and_local_time():
    ts = pd.Series(pd.to_datetime(["2025-08-01 10:00", "2025-08-04 10:00"], utc=True))   # 1 августа — праздник
    cal = calendar_features(ts)
    assert list(cal["is_holiday"]) == [1, 0]
    assert list(cal["hour_local"]) == [12, 12]    # летнее время UTC+2


def test_regression_metrics_mase():
    y = np.array([0.1, 0.2, 0.3])
    m = regression_metrics(y, y + 0.01, y + 0.02)
    assert m["mae"] == pytest.approx(0.01) and m["mase"] == pytest.approx(0.5)


def test_classification_metrics_perfect_and_random():
    y = np.array([0, 1] * 50)
    assert classification_metrics(y, y.astype(float))["auc"] == pytest.approx(1.0)
    assert classification_metrics(y, np.full(100, 0.5))["brier"] == pytest.approx(0.25)


def test_dq_catalogue_is_consistent():
    checks = yaml.safe_load(open(ROOT / "config" / "dq_checks.yaml", encoding="utf-8"))["checks"]
    ids = [c["id"] for c in checks]
    assert len(ids) == len(set(ids))
    types = {c["type"] for c in checks}
    assert types == {"completeness", "uniqueness", "validity", "type", "range", "referential", "freshness"}
    assert {c["severity"] for c in checks} <= {"error", "warn"}


def test_lineage_graph_connects_source_to_dashboard():
    from evadvisor.lineage import load_graph

    nodes, edges = load_graph()
    ids = {n["id"] for n in nodes}
    assert all(a in ids and b in ids for a, b in edges)
    parents: dict[str, set[str]] = {}
    for a, b in edges:
        parents.setdefault(b, set()).add(a)
    seen, stack = set(), ["dash.subject"]
    while stack:
        n = stack.pop()
        for p in parents.get(n, ()):
            if p not in seen:
                seen.add(p)
                stack.append(p)
    assert "src.archive" in seen and "metric.occupancy" in seen
