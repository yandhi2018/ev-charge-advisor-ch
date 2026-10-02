-- 07_roles.sql — роли и разграничение доступа (ПР9 ПСУД).
-- Групповые роли (NOLOGIN) несут права; учётные записи приложения (LOGIN) создаёт
-- мигратор с паролями из .env и включает в группы: app_driver → role_driver и т. д.
--
-- Матрица доступа (S — SELECT, I — INSERT, U — UPDATE, D — DELETE, X — EXECUTE):
-- | Объект                              | role_driver | role_analyst | role_engineer |
-- |-------------------------------------|-------------|--------------|---------------|
-- | core: справочники, точки, станции   | S           | S            | S I U D       |
-- | core.status_snapshot, weather       | S           | S            | S I U D       |
-- | core.recommendation_request / item  | S I (U n_results) | S      | S             |
-- | stg.*                               | —           | —            | S I U D       |
-- | mart.*                              | S           | S            | S I U D       |
-- | ops.*                               | —           | —            | S I U         |
-- | функции подбора (fn_compatible_...) | X           | X            | X             |
-- | процедуры загрузки (sp_*)           | —           | —            | X             |

DO $$
DECLARE r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['role_driver', 'role_analyst', 'role_engineer'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        END IF;
    END LOOP;
END
$$;

-- Ничего лишнего по умолчанию: PUBLIC не получает доступ к схемам и функциям
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA stg, core, mart, ops FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA core, mart, ops FROM PUBLIC;
REVOKE EXECUTE ON ALL PROCEDURES IN SCHEMA core, mart, ops FROM PUBLIC;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Сброс прав групп перед выдачей (скрипт можно применять повторно)
REVOKE ALL ON ALL TABLES IN SCHEMA stg, core, mart, ops FROM role_driver, role_analyst, role_engineer;
REVOKE ALL ON SCHEMA stg, core, mart, ops FROM role_driver, role_analyst, role_engineer;

------------------------------------------------------------------------
-- Инженер данных: загрузка и преобразования
------------------------------------------------------------------------
GRANT USAGE ON SCHEMA stg, core, mart, ops TO role_engineer;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA stg, core, mart TO role_engineer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ops TO role_engineer;
GRANT DELETE ON ops.dq_check, ops.dq_result, ops.lineage_node, ops.lineage_edge, ops.source_schema
    TO role_engineer;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA stg, core, mart, ops TO role_engineer;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core, mart, ops TO role_engineer;
GRANT EXECUTE ON ALL PROCEDURES IN SCHEMA core, mart TO role_engineer;
-- Журнал версий схемы ведёт только владелец
REVOKE INSERT, UPDATE, DELETE ON ops.schema_version FROM role_engineer;

------------------------------------------------------------------------
-- Аналитик: только чтение предметных данных и витрин
------------------------------------------------------------------------
GRANT USAGE ON SCHEMA core, mart TO role_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA mart TO role_analyst;
GRANT SELECT ON core.canton, core.operator, core.station, core.evse, core.evse_plug, core.plug_type,
                core.evse_history, core.weather_hourly, core.status_snapshot, core.vehicle,
                core.recommendation_request, core.recommendation_item
    TO role_analyst;
GRANT EXECUTE ON FUNCTION core.fn_haversine_km(double precision, double precision, double precision, double precision)
    TO role_analyst;

------------------------------------------------------------------------
-- Приложение водителя: чтение справочников и витрин, запись журнала запросов
------------------------------------------------------------------------
GRANT USAGE ON SCHEMA core, mart TO role_driver;
GRANT SELECT ON core.canton, core.operator, core.station, core.evse, core.evse_plug, core.plug_type,
                core.vehicle, core.vehicle_plug, core.status_snapshot, core.weather_hourly
    TO role_driver;
GRANT SELECT ON mart.profile_group, mart.evse_profile TO role_driver;
GRANT SELECT, INSERT ON core.recommendation_request, core.recommendation_item TO role_driver;
GRANT UPDATE (n_results) ON core.recommendation_request TO role_driver;
GRANT EXECUTE ON FUNCTION
    core.fn_haversine_km(double precision, double precision, double precision, double precision),
    core.fn_effective_power_kw(text, integer),
    core.fn_compatible_evses(integer, double precision, double precision, double precision, boolean)
    TO role_driver;
