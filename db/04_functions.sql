-- 04_functions.sql — пользовательские функции (ПР8 ПСУД).
-- Каждая функция связана с требованием или бизнес-правилом (см. docs/psud/technical_task.md).

-- Расстояние по большому кругу, км (FR: расчёт расстояния до станции)
CREATE OR REPLACE FUNCTION core.fn_haversine_km(lat1 double precision, lon1 double precision,
                                                lat2 double precision, lon2 double precision)
RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT 2 * 6371.0088 * asin(sqrt(
        power(sin(radians(lat2 - lat1) / 2), 2) +
        cos(radians(lat1)) * cos(radians(lat2)) * power(sin(radians(lon2 - lon1) / 2), 2)));
$$;
COMMENT ON FUNCTION core.fn_haversine_km IS 'Расстояние по большому кругу между двумя точками, км';

-- Ключ станции для разрешения сущностей: оператор + нормализованный адрес,
-- при отсутствии адреса — координаты, округлённые до ~10 м
CREATE OR REPLACE FUNCTION core.fn_station_key(p_operator_id text, p_street text, p_postal_code text,
                                               p_lat double precision, p_lon double precision)
RETURNS char(32)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT md5(
        CASE WHEN coalesce(btrim(p_street), '') <> '' AND coalesce(btrim(p_postal_code), '') <> ''
             THEN p_operator_id || '|' ||
                  lower(regexp_replace(translate(p_street, 'ÄÖÜäöüéèàâç', 'AOUaoueeaac'), '[^a-zA-Z0-9]+', '', 'g'))
                  || '|' || btrim(p_postal_code)
             ELSE p_operator_id || '|geo|' || round(p_lat::numeric, 4) || '|' || round(p_lon::numeric, 4)
        END);
$$;
COMMENT ON FUNCTION core.fn_station_key IS 'Ключ разрешения сущности «станция» (правило: одна станция = оператор + адрес)';

-- Кантон по почтовому индексу, иначе по ближайшему населённому пункту
CREATE OR REPLACE FUNCTION core.fn_resolve_canton(p_postal_code text, p_lat double precision,
                                                  p_lon double precision)
RETURNS char(2)
LANGUAGE sql STABLE AS $$
    SELECT coalesce(
        (SELECT pc.canton_code FROM core.postal_code pc
          WHERE pc.postal_code = btrim(p_postal_code)
          ORDER BY pc.address_share DESC NULLS LAST,
                   core.fn_haversine_km(p_lat, p_lon, pc.lat, pc.lon)
          LIMIT 1),
        (SELECT pc.canton_code FROM core.postal_code pc
          WHERE pc.lat BETWEEN p_lat - 0.1 AND p_lat + 0.1
            AND pc.lon BETWEEN p_lon - 0.15 AND p_lon + 0.15
          ORDER BY core.fn_haversine_km(p_lat, p_lon, pc.lat, pc.lon)
          LIMIT 1));
$$;
COMMENT ON FUNCTION core.fn_resolve_canton IS 'Кантон станции: по почтовому индексу или ближайшему населённому пункту (до ~10 км)';

-- Эффективная мощность для пары «точка — автомобиль», кВт.
-- Бизнес-правило: мощность ограничена минимумом из мощности точки и предела автомобиля
-- для соответствующего тока; NULL — автомобиль не может заряжаться этим током.
CREATE OR REPLACE FUNCTION core.fn_effective_power_kw(p_evse_id text, p_vehicle_id integer)
RETURNS numeric
LANGUAGE sql STABLE AS $$
    -- least() в PostgreSQL игнорирует NULL: при неизвестной мощности точки результат NULL, а не предел авто
    SELECT CASE WHEN e.power_kw IS NULL THEN NULL
                WHEN e.power_class = 'DC' AND v.dc_max_kw IS NOT NULL THEN least(e.power_kw, v.dc_max_kw)
                WHEN e.power_class = 'AC' THEN least(e.power_kw, v.ac_max_kw)
           END
      FROM core.evse e, core.vehicle v
     WHERE e.evse_id = p_evse_id AND v.vehicle_id = p_vehicle_id;
$$;
COMMENT ON FUNCTION core.fn_effective_power_kw IS 'min(мощность точки, предел автомобиля по AC/DC), кВт';

-- Совместимые точки в радиусе (FR: подбор станций с учётом разъёма и мощности).
-- Жёсткие ограничения: общий тип разъёма; розетка без кабеля — только при наличии своего кабеля;
-- точка активна; автомобиль поддерживает соответствующий ток.
CREATE OR REPLACE FUNCTION core.fn_compatible_evses(p_vehicle_id integer,
                                                    p_lat double precision, p_lon double precision,
                                                    p_radius_km double precision,
                                                    p_has_own_cable boolean DEFAULT true)
RETURNS TABLE (evse_id text, station_id integer, distance_km double precision,
               plug_codes text[], power_class text, power_kw numeric, effective_kw numeric)
LANGUAGE sql STABLE AS $$
    WITH box AS (
        SELECT p_radius_km / 111.0 AS dlat,
               p_radius_km / (111.0 * cos(radians(p_lat))) AS dlon
    )
    SELECT e.evse_id,
           s.station_id,
           core.fn_haversine_km(p_lat, p_lon, s.lat, s.lon) AS distance_km,
           array_agg(DISTINCT ep.plug_code ORDER BY ep.plug_code) AS plug_codes,
           e.power_class,
           e.power_kw,
           CASE WHEN e.power_kw IS NULL THEN NULL
                WHEN e.power_class = 'DC' THEN least(e.power_kw, v.dc_max_kw)
                ELSE least(e.power_kw, v.ac_max_kw) END AS effective_kw
      FROM box, core.station s
      JOIN core.evse e        ON e.station_id = s.station_id AND e.is_active
      JOIN core.evse_plug ep  ON ep.evse_id = e.evse_id
      JOIN core.plug_type pt  ON pt.plug_code = ep.plug_code
      JOIN core.vehicle_plug vp ON vp.plug_code = ep.plug_code AND vp.vehicle_id = p_vehicle_id
      JOIN core.vehicle v     ON v.vehicle_id = p_vehicle_id
     WHERE s.lat BETWEEN p_lat - box.dlat AND p_lat + box.dlat
       AND s.lon BETWEEN p_lon - box.dlon AND p_lon + box.dlon
       AND core.fn_haversine_km(p_lat, p_lon, s.lat, s.lon) <= p_radius_km
       AND (NOT pt.needs_own_cable OR p_has_own_cable)
       AND (e.power_class = 'AC' OR v.dc_max_kw IS NOT NULL)
     GROUP BY e.evse_id, s.station_id, s.lat, s.lon, e.power_class, e.power_kw, v.ac_max_kw, v.dc_max_kw;
$$;
COMMENT ON FUNCTION core.fn_compatible_evses IS 'Совместимые с автомобилем активные точки в радиусе, с эффективной мощностью';

-- Свежесть источника в часах (проверка актуальности, операционный дашборд)
CREATE OR REPLACE FUNCTION ops.fn_source_age_hours(p_source text)
RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT extract(epoch FROM now() - max(finished_at)) / 3600.0
      FROM ops.load_run
     WHERE source = p_source AND status IN ('success', 'partial');
$$;
COMMENT ON FUNCTION ops.fn_source_age_hours IS 'Часы с последней успешной загрузки источника';
