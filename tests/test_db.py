"""Тесты на БД: разграничение доступа (ПР9), триггеры и ограничения (ПР6, ПР8), идемпотентность.

Каждый тест выполняется в транзакции с откатом — данные БД не меняются.
Пропускаются, если PostgreSQL проекта недоступна.
"""

import uuid

import psycopg
import pytest
from psycopg import errors

from evadvisor.db import connect


def _available() -> bool:
    try:
        with connect("admin") as conn:
            conn.execute("SELECT 1 FROM core.vehicle LIMIT 1")
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.db, pytest.mark.skipif(not _available(), reason="PostgreSQL проекта недоступна")]


@pytest.fixture
def conn_of():
    opened = []

    def make(role):
        c = connect(role)
        opened.append(c)
        return c

    yield make
    for c in opened:
        c.rollback()
        c.close()


# ---------------------------------------------------------------- ПР9: сценарии доступа
@pytest.mark.parametrize("role, sql, allowed", [
    ("driver", "SELECT count(*) FROM core.vehicle", True),                                    # чтение каталога
    ("driver", "SELECT * FROM core.fn_compatible_evses(1, 46.95, 7.44, 2, true)", True),      # вызов функции подбора
    ("driver", "UPDATE core.evse SET power_kw = 1 WHERE false", False),                       # изменение справочника
    ("driver", "SELECT count(*) FROM ops.load_run", False),                                   # служебный слой
    ("driver", "CALL core.sp_apply_weather('00000000-0000-0000-0000-000000000000')", False),  # процедура загрузки
    ("analyst", "SELECT count(*) FROM mart.occupancy_hourly", True),                          # чтение витрины
    ("analyst", "DELETE FROM core.recommendation_request WHERE false", False),                # удаление журнала
    ("analyst", "INSERT INTO mart.model_metric VALUES ('canton','x',0,'m','all',1,now())", False),
    ("engineer", "SELECT count(*) FROM ops.load_run", True),                                  # журнал загрузок
    ("engineer", "DELETE FROM ops.schema_version WHERE false", False),                        # журнал версий схемы
])
def test_role_access(conn_of, role, sql, allowed):
    conn = conn_of(role)
    if allowed:
        conn.execute(sql)
    else:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql)


def test_public_has_no_default_execute(conn_of):
    conn = conn_of("admin")
    row = conn.execute(
        """SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname IN ('core', 'mart', 'ops') AND has_function_privilege('public', p.oid, 'EXECUTE')"""
    ).fetchone()
    assert row["n"] == 0


# ---------------------------------------------------------------- ПР6: ограничения
def test_check_rejects_invalid_vehicle(conn_of):
    conn = conn_of("engineer")
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO core.vehicle (brand, model, ac_max_kw, source) VALUES ('X', 'Y', -5, 'manual')")


def test_unique_vehicle(conn_of):
    conn = conn_of("engineer")
    conn.execute("INSERT INTO core.vehicle (brand, model, variant, release_year, ac_max_kw, source) "
                 "VALUES ('Test', 'Car', 'v', 2024, 11, 'manual')")
    with pytest.raises(errors.UniqueViolation):
        conn.execute("INSERT INTO core.vehicle (brand, model, variant, release_year, ac_max_kw, source) "
                     "VALUES ('Test', 'Car', 'v', 2024, 11, 'manual')")


def test_fk_rejects_unknown_canton(conn_of):
    conn = conn_of("engineer")
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute("INSERT INTO core.postal_code VALUES (999999, '9999', 'X', 'X', 'XX', 100, 47, 8)")


# ---------------------------------------------------------------- ПР8: триггеры
def test_finished_run_cannot_be_reopened(conn_of):
    conn = conn_of("engineer")
    run = uuid.uuid4()
    conn.execute("INSERT INTO ops.load_run (run_id, source) VALUES (%s, 'test')", (run,))
    conn.execute("UPDATE ops.load_run SET status = 'success' WHERE run_id = %s", (run,))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE ops.load_run SET status = 'running' WHERE run_id = %s", (run,))


def test_load_run_is_append_only(conn_of):
    conn = conn_of("admin")
    run = uuid.uuid4()
    conn.execute("INSERT INTO ops.load_run (run_id, source) VALUES (%s, 'test')", (run,))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM ops.load_run WHERE run_id = %s", (run,))


def test_evse_change_creates_history_version(conn_of):
    conn = conn_of("engineer")
    evse = conn.execute("SELECT evse_id FROM core.evse WHERE is_active AND power_kw IS NOT NULL LIMIT 1").fetchone()
    if evse is None:
        pytest.skip("справочник точек пуст")
    eid = evse["evse_id"]
    before = conn.execute("SELECT count(*) AS n FROM core.evse_history WHERE evse_id = %s", (eid,)).fetchone()["n"]
    conn.execute("UPDATE core.evse SET power_kw = power_kw + 1, row_hash = md5(random()::text) || md5('x'), "
                 "valid_from = now() WHERE evse_id = %s", (eid,))
    rows = conn.execute("SELECT valid_to, change_type FROM core.evse_history WHERE evse_id = %s ORDER BY history_id",
                        (eid,)).fetchall()
    assert len(rows) == before + 1
    assert rows[-1]["change_type"] == "update" and rows[-1]["valid_to"] is None
    assert sum(r["valid_to"] is None for r in rows) == 1        # ровно одна открытая версия


def test_last_seen_update_does_not_create_history(conn_of):
    conn = conn_of("engineer")
    evse = conn.execute("SELECT evse_id FROM core.evse LIMIT 1").fetchone()
    if evse is None:
        pytest.skip("справочник точек пуст")
    before = conn.execute("SELECT count(*) AS n FROM core.evse_history").fetchone()["n"]
    conn.execute("UPDATE core.evse SET last_seen = now() WHERE evse_id = %s", (evse["evse_id"],))
    assert conn.execute("SELECT count(*) AS n FROM core.evse_history").fetchone()["n"] == before


def test_recommendation_of_incompatible_station_is_rejected(conn_of):
    conn = conn_of("driver")
    # автомобиль только с Type 1 и станция только с CCS — несовместимы
    pair = conn.execute(
        """SELECT v.vehicle_id, e.station_id
             FROM core.vehicle v, core.evse e
            WHERE NOT EXISTS (SELECT 1 FROM core.vehicle_plug vp JOIN core.evse_plug ep USING (plug_code)
                               JOIN core.evse e2 ON e2.evse_id = ep.evse_id
                              WHERE vp.vehicle_id = v.vehicle_id AND e2.station_id = e.station_id)
            LIMIT 1""").fetchone()
    if pair is None:
        pytest.skip("нет несовместимой пары")
    req = uuid.uuid4()
    conn.execute("INSERT INTO core.recommendation_request (request_id, vehicle_id, lat_round, lon_round, energy_kwh) "
                 "VALUES (%s, %s, 46.95, 7.44, 20)", (req, pair["vehicle_id"]))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("INSERT INTO core.recommendation_item (request_id, rank, station_id, distance_km, eta_min, "
                     "p_free, expected_total_min) VALUES (%s, 1, %s, 1, 2, 0.5, 30)", (req, pair["station_id"]))


# ---------------------------------------------------------------- идемпотентность
def test_effective_power_ignores_unknown_point_power(conn_of):
    conn = conn_of("driver")
    row = conn.execute(
        """SELECT core.fn_effective_power_kw(e.evse_id, v.vehicle_id) AS kw
             FROM core.evse e, core.vehicle v
            WHERE e.power_kw IS NULL AND e.power_class = 'DC' AND v.dc_max_kw IS NOT NULL LIMIT 1""").fetchone()
    if row is None:
        pytest.skip("нет точки без мощности")
    assert row["kw"] is None


def test_weather_procedure_is_idempotent(conn_of):
    conn = conn_of("engineer")
    run = uuid.uuid4()
    conn.execute("INSERT INTO ops.load_run (run_id, source) VALUES (%s, 'test')", (run,))
    for _ in range(2):
        conn.execute("INSERT INTO stg.weather_hourly VALUES (%s, 'BE', '2020-01-01 00:00+00', 'actual', 1, 0, 0, 3)",
                     (run,))
        conn.execute("CALL core.sp_apply_weather(%s)", (run,))
    n = conn.execute("SELECT count(*) AS n FROM core.weather_hourly WHERE canton_code = 'BE' "
                     "AND hour_utc = '2020-01-01 00:00+00' AND kind = 'actual'").fetchone()["n"]
    assert n == 1
