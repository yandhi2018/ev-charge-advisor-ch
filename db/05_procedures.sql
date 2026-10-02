-- 05_procedures.sql — хранимые процедуры преобразования stg → core → mart (ПР8 ПСУД).
-- Каждая процедура обрабатывает данные одного запуска (run_id) и идемпотентна:
-- повторный вызов с тем же run_id не меняет результат.

------------------------------------------------------------------------
-- Справочник зарядных точек: дедупликация, карантин, разрешение станций, SCD2
------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE core.sp_apply_evse_data(p_run_id uuid)
LANGUAGE plpgsql AS $$
DECLARE
    v_run_ts     timestamptz;
    v_stg_rows   bigint;
    v_dups       bigint;
    v_rejected   bigint;
    v_written    bigint;
    v_active     bigint;
    v_incoming   bigint;
BEGIN
    SELECT started_at INTO v_run_ts FROM ops.load_run WHERE run_id = p_run_id;
    IF v_run_ts IS NULL THEN
        RAISE EXCEPTION 'run % not found in ops.load_run', p_run_id;
    END IF;

    SELECT count(*) INTO v_stg_rows FROM stg.evse_data WHERE run_id = p_run_id;

    -- 1. Дедупликация: одна запись на EvseID (самая свежая по lastUpdate, затем последняя в выгрузке)
    DROP TABLE IF EXISTS tmp_evse;
    CREATE TEMP TABLE tmp_evse AS
    SELECT DISTINCT ON (evse_id) *
      FROM stg.evse_data
     WHERE run_id = p_run_id AND evse_id IS NOT NULL
     ORDER BY evse_id, last_update DESC NULLS LAST, stg_id DESC;
    v_dups := v_stg_rows - (SELECT count(*) FROM tmp_evse)
              - (SELECT count(*) FROM stg.evse_data WHERE run_id = p_run_id AND evse_id IS NULL);

    -- 2. Карантин: нет идентификатора, оператора или координат; координаты вне Швейцарии и окрестностей
    INSERT INTO ops.quarantine (run_id, table_name, record, reason)
    SELECT p_run_id, 'stg.evse_data', to_jsonb(s), 'missing evse_id'
      FROM stg.evse_data s WHERE s.run_id = p_run_id AND s.evse_id IS NULL;

    INSERT INTO ops.quarantine (run_id, table_name, record, reason)
    SELECT p_run_id, 'stg.evse_data', to_jsonb(t),
           CASE WHEN t.operator_id IS NULL THEN 'missing operator'
                WHEN t.lat IS NULL OR t.lon IS NULL THEN 'missing coordinates'
                ELSE 'coordinates out of range' END
      FROM tmp_evse t
     WHERE t.operator_id IS NULL OR t.lat IS NULL OR t.lon IS NULL
        OR t.lat NOT BETWEEN 45.5 AND 48.0 OR t.lon NOT BETWEEN 5.5 AND 11.0;
    DELETE FROM tmp_evse t
     WHERE t.operator_id IS NULL OR t.lat IS NULL OR t.lon IS NULL
        OR t.lat NOT BETWEEN 45.5 AND 48.0 OR t.lon NOT BETWEEN 5.5 AND 11.0;
    SELECT count(*) INTO v_rejected FROM ops.quarantine WHERE run_id = p_run_id AND table_name = 'stg.evse_data';

    -- Защита от частичной выгрузки: если пришло менее половины активных точек, не деактивируем
    SELECT count(*) INTO v_active FROM core.evse WHERE is_active;
    SELECT count(*) INTO v_incoming FROM tmp_evse;

    -- 3. Операторы
    INSERT INTO core.operator (operator_id, name, first_seen, last_seen)
    SELECT DISTINCT ON (operator_id) operator_id, coalesce(operator_name, operator_id), v_run_ts, v_run_ts
      FROM tmp_evse ORDER BY operator_id
    ON CONFLICT (operator_id) DO UPDATE
        SET name = EXCLUDED.name, last_seen = greatest(core.operator.last_seen, EXCLUDED.last_seen);

    -- 4. Станции (разрешение сущностей по ключу оператор + адрес)
    ALTER TABLE tmp_evse ADD COLUMN station_key char(32);
    UPDATE tmp_evse
       SET station_key = core.fn_station_key(operator_id, street, postal_code, lat, lon);

    INSERT INTO core.station (station_key, name, street, postal_code, city, canton_code, lat, lon,
                              operator_id, is_open_24h, accessibility, first_seen, last_seen)
    SELECT g.station_key, g.name, g.street, g.postal_code, g.city,
           core.fn_resolve_canton(g.postal_code, g.lat, g.lon),
           g.lat, g.lon, g.operator_id, g.is_open_24h, g.accessibility, v_run_ts, v_run_ts
      FROM (SELECT station_key,
                   mode() WITHIN GROUP (ORDER BY station_name) AS name,
                   mode() WITHIN GROUP (ORDER BY street)       AS street,
                   mode() WITHIN GROUP (ORDER BY postal_code)  AS postal_code,
                   mode() WITHIN GROUP (ORDER BY city)         AS city,
                   avg(lat) AS lat, avg(lon) AS lon,
                   min(operator_id) AS operator_id,
                   bool_or(is_open_24h) AS is_open_24h,
                   mode() WITHIN GROUP (ORDER BY accessibility) AS accessibility
              FROM tmp_evse GROUP BY station_key) g
    ON CONFLICT (station_key) DO UPDATE
        SET name = EXCLUDED.name, street = EXCLUDED.street, postal_code = EXCLUDED.postal_code,
            city = EXCLUDED.city, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
            canton_code = coalesce(core.station.canton_code, EXCLUDED.canton_code),
            is_open_24h = EXCLUDED.is_open_24h, accessibility = EXCLUDED.accessibility,
            last_seen = EXCLUDED.last_seen;

    -- 5. Точки: вставка новых, обновление изменившихся (триггер пишет историю)
    INSERT INTO core.evse (evse_id, station_id, operator_id, power_kw, power_type, is_active,
                           row_hash, valid_from, last_run_id, first_seen, last_seen)
    SELECT t.evse_id, s.station_id, t.operator_id, t.power_kw, t.power_type, true,
           t.record_hash, v_run_ts, p_run_id, v_run_ts, v_run_ts
      FROM tmp_evse t JOIN core.station s ON s.station_key = t.station_key
    ON CONFLICT (evse_id) DO UPDATE
        SET station_id = EXCLUDED.station_id, operator_id = EXCLUDED.operator_id,
            power_kw = EXCLUDED.power_kw, power_type = EXCLUDED.power_type,
            is_active = true, row_hash = EXCLUDED.row_hash, valid_from = EXCLUDED.valid_from,
            last_run_id = EXCLUDED.last_run_id, last_seen = EXCLUDED.last_seen
        WHERE core.evse.row_hash IS DISTINCT FROM EXCLUDED.row_hash OR NOT core.evse.is_active;
    GET DIAGNOSTICS v_written = ROW_COUNT;

    UPDATE core.evse e SET last_seen = v_run_ts
      FROM tmp_evse t WHERE t.evse_id = e.evse_id AND e.last_seen < v_run_ts;

    -- 6. Разъёмы точки (связь M:N), неизвестные названия — в карантин
    DELETE FROM core.evse_plug ep
     USING tmp_evse t
     WHERE ep.evse_id = t.evse_id
       AND NOT EXISTS (SELECT 1 FROM unnest(t.plugs) p(name)
                         JOIN core.plug_alias a ON a.source = 'oicp' AND a.source_name = p.name
                        WHERE a.plug_code = ep.plug_code);
    INSERT INTO core.evse_plug (evse_id, plug_code)
    SELECT DISTINCT t.evse_id, a.plug_code
      FROM tmp_evse t, unnest(t.plugs) p(name)
      JOIN core.plug_alias a ON a.source = 'oicp' AND a.source_name = p.name
    ON CONFLICT DO NOTHING;
    INSERT INTO ops.quarantine (run_id, table_name, record, reason)
    SELECT p_run_id, 'stg.evse_data.plugs',
           jsonb_build_object('plug', p.name, 'evse_count', count(*)), 'unknown plug type'
      FROM tmp_evse t, unnest(t.plugs) p(name)
     WHERE NOT EXISTS (SELECT 1 FROM core.plug_alias a WHERE a.source = 'oicp' AND a.source_name = p.name)
     GROUP BY p.name;

    -- 7. Точки, исчезнувшие из полной выгрузки, деактивируются (история сохраняется)
    IF v_incoming >= v_active * 0.5 THEN
        UPDATE core.evse e SET is_active = false, valid_from = v_run_ts, last_run_id = p_run_id
         WHERE e.is_active AND NOT EXISTS (SELECT 1 FROM tmp_evse t WHERE t.evse_id = e.evse_id);
    ELSE
        RAISE WARNING 'partial EVSEData payload (% of % active): deactivation skipped', v_incoming, v_active;
    END IF;

    UPDATE ops.load_run
       SET rows_written = v_written, rows_duplicate = greatest(v_dups, 0), rows_rejected = v_rejected
     WHERE run_id = p_run_id;
    DROP TABLE IF EXISTS tmp_evse;
END;
$$;
COMMENT ON PROCEDURE core.sp_apply_evse_data IS 'stg.evse_data → core: дедупликация, карантин, станции, точки (SCD2), разъёмы';

------------------------------------------------------------------------
-- Снимок текущих статусов
------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE core.sp_apply_status_snapshot(p_run_id uuid)
LANGUAGE plpgsql AS $$
DECLARE
    v_written  bigint;
    v_unknown  bigint;
BEGIN
    INSERT INTO core.status_snapshot (slot_ts, evse_id, status, run_id)
    SELECT DISTINCT ON (s.evse_id) s.slot_ts, s.evse_id, s.status, p_run_id
      FROM stg.evse_status s
      JOIN core.evse e ON e.evse_id = s.evse_id
     WHERE s.run_id = p_run_id
       AND s.status IN ('Available', 'Occupied', 'OutOfService', 'Unknown', 'Reserved', 'EvseNotFound')
     ORDER BY s.evse_id
    ON CONFLICT (slot_ts, evse_id) DO NOTHING;
    GET DIAGNOSTICS v_written = ROW_COUNT;

    -- Ссылочная целостность: статусы точек, которых нет в справочнике, — одной записью в карантин
    SELECT count(*) INTO v_unknown
      FROM stg.evse_status s
     WHERE s.run_id = p_run_id
       AND NOT EXISTS (SELECT 1 FROM core.evse e WHERE e.evse_id = s.evse_id);
    IF v_unknown > 0 THEN
        INSERT INTO ops.quarantine (run_id, table_name, record, reason)
        SELECT p_run_id, 'stg.evse_status',
               jsonb_build_object('count', v_unknown,
                                  'examples', (SELECT jsonb_agg(evse_id) FROM (
                                      SELECT s.evse_id FROM stg.evse_status s
                                       WHERE s.run_id = p_run_id
                                         AND NOT EXISTS (SELECT 1 FROM core.evse e WHERE e.evse_id = s.evse_id)
                                       LIMIT 20) x)),
               'evse not in reference';
    END IF;

    UPDATE ops.load_run SET rows_written = v_written, rows_rejected = v_unknown WHERE run_id = p_run_id;
    DELETE FROM stg.evse_status WHERE run_id = p_run_id;   -- staging снимков не накапливаем
END;
$$;
COMMENT ON PROCEDURE core.sp_apply_status_snapshot IS 'stg.evse_status → core.status_snapshot; неизвестные точки — в карантин';

------------------------------------------------------------------------
-- Погода
------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE core.sp_apply_weather(p_run_id uuid)
LANGUAGE plpgsql AS $$
DECLARE
    v_written bigint;
    v_bad     bigint;
BEGIN
    INSERT INTO ops.quarantine (run_id, table_name, record, reason)
    SELECT p_run_id, 'stg.weather_hourly', to_jsonb(w), 'value out of range'
      FROM stg.weather_hourly w
     WHERE w.run_id = p_run_id
       AND (w.temperature_c NOT BETWEEN -45 AND 45 OR w.precipitation_mm < 0
            OR w.snowfall_cm < 0 OR w.wind_kmh < 0);
    GET DIAGNOSTICS v_bad = ROW_COUNT;

    INSERT INTO core.weather_hourly (canton_code, hour_utc, kind, temperature_c, precipitation_mm,
                                     snowfall_cm, wind_kmh, run_id, loaded_at)
    SELECT DISTINCT ON (w.canton_code, w.hour_utc, w.kind)
           w.canton_code, w.hour_utc, w.kind, w.temperature_c, w.precipitation_mm,
           w.snowfall_cm, w.wind_kmh, p_run_id, now()
      FROM stg.weather_hourly w
      JOIN core.canton c ON c.canton_code = w.canton_code
     WHERE w.run_id = p_run_id
       AND NOT (coalesce(w.temperature_c NOT BETWEEN -45 AND 45, false) OR coalesce(w.precipitation_mm < 0, false)
                OR coalesce(w.snowfall_cm < 0, false) OR coalesce(w.wind_kmh < 0, false))
     ORDER BY w.canton_code, w.hour_utc, w.kind
    ON CONFLICT (canton_code, hour_utc, kind) DO UPDATE
        SET temperature_c = EXCLUDED.temperature_c, precipitation_mm = EXCLUDED.precipitation_mm,
            snowfall_cm = EXCLUDED.snowfall_cm, wind_kmh = EXCLUDED.wind_kmh,
            run_id = EXCLUDED.run_id, loaded_at = EXCLUDED.loaded_at;
    GET DIAGNOSTICS v_written = ROW_COUNT;

    UPDATE ops.load_run SET rows_written = v_written, rows_rejected = v_bad WHERE run_id = p_run_id;
    DELETE FROM stg.weather_hourly WHERE run_id = p_run_id;
END;
$$;
COMMENT ON PROCEDURE core.sp_apply_weather IS 'stg.weather_hourly → core.weather_hourly (upsert), выбросы — в карантин';

------------------------------------------------------------------------
-- Каталог автомобилей
------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE core.sp_apply_vehicles(p_run_id uuid)
LANGUAGE plpgsql AS $$
DECLARE
    v_written bigint;
    v_bad     bigint;
BEGIN
    INSERT INTO ops.quarantine (run_id, table_name, record, reason)
    SELECT p_run_id, 'stg.vehicle', to_jsonb(v), 'missing AC power or name'
      FROM stg.vehicle v
     WHERE v.run_id = p_run_id
       AND (v.ac_max_kw IS NULL OR v.ac_max_kw <= 0 OR v.brand IS NULL OR v.model IS NULL);
    GET DIAGNOSTICS v_bad = ROW_COUNT;

    -- При совпадении модели приоритет у собственного каталога (manual)
    DROP TABLE IF EXISTS tmp_vehicle;
    CREATE TEMP TABLE tmp_vehicle AS
    SELECT DISTINCT ON (brand, model, coalesce(variant, ''), release_year)
           brand, model, coalesce(variant, '') AS variant, release_year, battery_kwh,
           least(ac_max_kw, 43) AS ac_max_kw, nullif(dc_max_kw, 0) AS dc_max_kw, source, plugs
      FROM stg.vehicle
     WHERE run_id = p_run_id AND ac_max_kw > 0 AND brand IS NOT NULL AND model IS NOT NULL
     ORDER BY brand, model, coalesce(variant, ''), release_year, (source = 'manual') DESC;

    INSERT INTO core.vehicle (brand, model, variant, release_year, battery_kwh, ac_max_kw, dc_max_kw, source)
    SELECT brand, model, variant, release_year, battery_kwh, ac_max_kw, dc_max_kw, source FROM tmp_vehicle
    ON CONFLICT (brand, model, variant, release_year) DO UPDATE
        SET battery_kwh = EXCLUDED.battery_kwh, ac_max_kw = EXCLUDED.ac_max_kw,
            dc_max_kw = EXCLUDED.dc_max_kw, source = EXCLUDED.source
        WHERE core.vehicle.source = EXCLUDED.source OR EXCLUDED.source = 'manual';
    GET DIAGNOSTICS v_written = ROW_COUNT;

    DELETE FROM core.vehicle_plug vp
     USING core.vehicle v, tmp_vehicle t
     WHERE vp.vehicle_id = v.vehicle_id
       AND (v.brand, v.model, v.variant, v.release_year) IS NOT DISTINCT FROM
           (t.brand, t.model, t.variant, t.release_year);
    INSERT INTO core.vehicle_plug (vehicle_id, plug_code)
    SELECT DISTINCT v.vehicle_id, a.plug_code
      FROM tmp_vehicle t
      JOIN core.vehicle v ON (v.brand, v.model, v.variant, v.release_year) IS NOT DISTINCT FROM
                             (t.brand, t.model, t.variant, t.release_year)
      CROSS JOIN unnest(t.plugs) p(name)
      JOIN core.plug_alias a ON a.source = 'open-ev-data' AND a.source_name = p.name
    ON CONFLICT DO NOTHING;

    UPDATE ops.load_run SET rows_written = v_written, rows_rejected = v_bad WHERE run_id = p_run_id;
    DELETE FROM stg.vehicle WHERE run_id = p_run_id;
    DROP TABLE IF EXISTS tmp_vehicle;
END;
$$;
COMMENT ON PROCEDURE core.sp_apply_vehicles IS 'stg.vehicle → core.vehicle + vehicle_plug; собственный каталог имеет приоритет';

------------------------------------------------------------------------
-- Метаданные точек архива: текущий справочник приоритетнее ChargingStationDetails
------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE core.sp_build_archive_evse(p_run_id uuid)
LANGUAGE plpgsql AS $$
DECLARE
    v_written bigint;
BEGIN
    DELETE FROM core.archive_evse;
    INSERT INTO core.archive_evse (evse_id, canton_code, power_class, power_kw, lat, lon, meta_source, run_id)
    SELECT e.evse_id, s.canton_code, e.power_class, e.power_kw, s.lat, s.lon, 'evse_data', p_run_id
      FROM core.evse e JOIN core.station s ON s.station_id = e.station_id;

    INSERT INTO core.archive_evse (evse_id, canton_code, power_class, power_kw, lat, lon, meta_source, run_id)
    SELECT d.evse_id,
           core.fn_resolve_canton(d.postal_code, d.lat, d.lon),
           CASE WHEN upper(d.power_type) = 'DC' THEN 'DC'
                WHEN upper(d.power_type) LIKE 'AC%' THEN 'AC'
                WHEN d.power_kw > 22 THEN 'DC' END,
           CASE WHEN d.power_kw > 0 THEN d.power_kw END,
           d.lat, d.lon, 'details_csv', p_run_id
      FROM (SELECT DISTINCT ON (evse_id) * FROM stg.archive_details
             WHERE run_id = p_run_id AND evse_id IS NOT NULL
               AND lat BETWEEN 45.5 AND 48.0 AND lon BETWEEN 5.5 AND 11.0
             ORDER BY evse_id, power_kw DESC NULLS LAST) d
    ON CONFLICT (evse_id) DO NOTHING;
    GET DIAGNOSTICS v_written = ROW_COUNT;

    UPDATE ops.load_run SET rows_written = v_written WHERE run_id = p_run_id;
    DELETE FROM stg.archive_details WHERE run_id = p_run_id;
END;
$$;
COMMENT ON PROCEDURE core.sp_build_archive_evse IS 'Справочник точек архива (кантон, класс мощности) для расчёта витрин';

------------------------------------------------------------------------
-- Витрина: почасовая загрузка по живым снимкам
------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE mart.sp_refresh_live_occupancy(p_from timestamptz DEFAULT now() - interval '2 days')
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM mart.occupancy_hourly WHERE source = 'live' AND hour_utc >= date_trunc('hour', p_from);
    INSERT INTO mart.occupancy_hourly (canton_code, power_class, hour_utc, n_evse, avg_available,
                                       avg_occupied, avg_out_of_service, avg_unknown,
                                       occupancy_rate, oos_rate, slots_observed, source)
    SELECT canton_code, power_class, hour_utc,
           max(n_evse), avg(n_av), avg(n_oc), avg(n_oos), avg(n_unk),
           CASE WHEN sum(n_av + n_oc) > 0 THEN sum(n_oc)::real / sum(n_av + n_oc) END,
           CASE WHEN sum(n_evse) > 0 THEN sum(n_oos)::real / sum(n_evse) END,
           count(DISTINCT slot_ts), 'live'
      FROM (SELECT s.canton_code, e.power_class, date_trunc('hour', ss.slot_ts) AS hour_utc, ss.slot_ts,
                   count(*) AS n_evse,
                   count(*) FILTER (WHERE ss.status = 'Available')    AS n_av,
                   count(*) FILTER (WHERE ss.status = 'Occupied')     AS n_oc,
                   count(*) FILTER (WHERE ss.status = 'OutOfService') AS n_oos,
                   count(*) FILTER (WHERE ss.status NOT IN ('Available', 'Occupied', 'OutOfService')) AS n_unk
              FROM core.status_snapshot ss
              JOIN core.evse e    ON e.evse_id = ss.evse_id
              JOIN core.station s ON s.station_id = e.station_id
             WHERE ss.slot_ts >= date_trunc('hour', p_from)
               AND s.canton_code IS NOT NULL AND e.power_class IS NOT NULL
             GROUP BY 1, 2, 3, 4) x
     GROUP BY canton_code, power_class, hour_utc
    ON CONFLICT (canton_code, power_class, hour_utc) DO NOTHING;   -- архивные часы не перезаписываем
END;
$$;
COMMENT ON PROCEDURE mart.sp_refresh_live_occupancy IS 'core.status_snapshot → mart.occupancy_hourly (source = live)';
