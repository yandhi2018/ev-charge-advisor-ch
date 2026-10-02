-- 03_queries.sql — прикладные запросы к БД (ПР7 ПСУД).
-- Каждый запрос решает информационную задачу одной из ролей системы.
-- Выполнение: docker exec -i evadvisor-db psql -U postgres -d evadvisor -f - < db/03_queries.sql
-- (или под учётной записью нужной роли, см. db/07_roles.sql).

-- Запрос 1. [WHERE, ORDER BY] Водитель: быстрые (DC) точки мощностью от 150 кВт в кантоне Bern,
-- по убыванию мощности. Ожидаемый результат: список точек с адресами.
SELECT e.evse_id, s.name, s.street, s.city, e.power_kw
  FROM core.evse e
  JOIN core.station s ON s.station_id = e.station_id
 WHERE e.is_active AND e.power_class = 'DC' AND e.power_kw >= 150 AND s.canton_code = 'BE'
 ORDER BY e.power_kw DESC, s.city;

-- Запрос 2. [JOIN трёх и более таблиц] Водитель: станции с разъёмом CCS2 и оператором в Цюрихе
-- (station — evse — evse_plug — plug_type — operator).
SELECT DISTINCT s.station_id, s.name, o.name AS operator, pt.name_ru AS plug
  FROM core.station s
  JOIN core.operator o   ON o.operator_id = s.operator_id
  JOIN core.evse e       ON e.station_id = s.station_id AND e.is_active
  JOIN core.evse_plug ep ON ep.evse_id = e.evse_id
  JOIN core.plug_type pt ON pt.plug_code = ep.plug_code
 WHERE pt.plug_code = 'CCS2' AND s.city ILIKE 'Zürich%'
 ORDER BY s.name;

-- Запрос 3. [агрегатная функция, GROUP BY] Аналитик: число станций, точек и суммарная мощность по кантонам.
SELECT s.canton_code, count(DISTINCT s.station_id) AS stations, count(e.evse_id) AS evse,
       round(sum(e.power_kw)) AS total_kw
  FROM core.station s
  JOIN core.evse e ON e.station_id = s.station_id AND e.is_active
 GROUP BY s.canton_code
 ORDER BY evse DESC;

-- Запрос 4. [GROUP BY + HAVING] Аналитик: операторы, у которых больше 5 % активных точек без указанной мощности
-- (кандидаты на запрос уточнения данных).
SELECT o.name, count(*) AS evse, count(*) FILTER (WHERE e.power_kw IS NULL) AS no_power
  FROM core.evse e JOIN core.operator o ON o.operator_id = e.operator_id
 WHERE e.is_active
 GROUP BY o.name
HAVING count(*) FILTER (WHERE e.power_kw IS NULL) > 0.05 * count(*)
 ORDER BY no_power DESC;

-- Запрос 5. [CTE, оконная функция] Аналитик: три самых загруженных часа суток (местное время)
-- для каждого класса мощности по архиву.
WITH by_hour AS (
    SELECT power_class,
           extract(hour FROM hour_utc AT TIME ZONE 'Europe/Zurich')::int AS hour_local,
           sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0) AS occupancy
      FROM mart.occupancy_hourly WHERE source = 'archive'
     GROUP BY 1, 2
)
SELECT power_class, hour_local, round(occupancy::numeric, 4) AS occupancy
  FROM (SELECT *, rank() OVER (PARTITION BY power_class ORDER BY occupancy DESC) AS r FROM by_hour) x
 WHERE r <= 3
 ORDER BY power_class, r;

-- Запрос 6. [вложенный запрос] Водитель: автомобили, которые могут заряжаться на всех DC-точках выбранной станции
-- (есть хотя бы один общий разъём с каждой точкой).
SELECT v.brand, v.model, v.variant, v.release_year
  FROM core.vehicle v
 WHERE v.dc_max_kw IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM core.evse e
         WHERE e.station_id = (SELECT min(station_id) FROM core.station WHERE city ILIKE 'Bern%')
           AND e.is_active AND e.power_class = 'DC'
           AND NOT EXISTS (SELECT 1 FROM core.evse_plug ep JOIN core.vehicle_plug vp ON vp.plug_code = ep.plug_code
                            WHERE ep.evse_id = e.evse_id AND vp.vehicle_id = v.vehicle_id))
 ORDER BY v.brand, v.model
 LIMIT 20;

-- Запрос 7. [несколько JOIN, группировка, агрегирование, фильтрация, сортировка] Аналитик: доля неисправных точек
-- по операторам и классам мощности по последнему снимку статусов (операторы с 20+ точками).
WITH last AS (SELECT max(slot_ts) AS slot FROM core.status_snapshot)
SELECT o.name AS operator, e.power_class, count(*) AS evse,
       round(100.0 * count(*) FILTER (WHERE ss.status = 'OutOfService') / count(*), 1) AS oos_pct
  FROM core.status_snapshot ss
  JOIN last ON ss.slot_ts = last.slot
  JOIN core.evse e     ON e.evse_id = ss.evse_id
  JOIN core.operator o ON o.operator_id = e.operator_id
 WHERE e.power_class IS NOT NULL
 GROUP BY o.name, e.power_class
HAVING count(*) >= 20
 ORDER BY oos_pct DESC, evse DESC;

-- Запрос 8. [специфика предметной области] Водитель: эффективная мощность зарядки Tesla Model 3 2024
-- на ближайших к вокзалу Берна совместимых точках (функция подбора и расстояние).
SELECT c.evse_id, round(c.distance_km::numeric, 2) AS km, c.power_class, c.power_kw, c.effective_kw, c.plug_codes
  FROM core.fn_compatible_evses(
         (SELECT vehicle_id FROM core.vehicle WHERE brand = 'Tesla' AND model LIKE 'Model 3%' AND release_year = 2024
           ORDER BY dc_max_kw DESC NULLS LAST LIMIT 1),
         46.9488, 7.4391, 3, true) c
 ORDER BY c.effective_kw DESC NULLS LAST, c.distance_km
 LIMIT 10;

-- Запрос 9. [функции дат, CASE] Аналитик: загрузка в будни и выходные по месяцам (архив).
SELECT date_trunc('month', hour_utc)::date AS month,
       CASE WHEN extract(isodow FROM hour_utc AT TIME ZONE 'Europe/Zurich') >= 6 THEN 'выходные' ELSE 'будни' END AS day_type,
       round((sum(avg_occupied) / nullif(sum(avg_available + avg_occupied), 0))::numeric, 4) AS occupancy
  FROM mart.occupancy_hourly
 WHERE source = 'archive'
 GROUP BY 1, 2
 ORDER BY 1, 2;

-- Запрос 10. [история изменений, SCD2] Инженер данных: точки, у которых за всё время было больше одной версии,
-- с датами изменений.
SELECT h.evse_id, count(*) AS versions, min(h.valid_from) AS first_version, max(h.valid_from) AS last_change,
       string_agg(h.change_type, ' → ' ORDER BY h.valid_from) AS history
  FROM core.evse_history h
 GROUP BY h.evse_id
HAVING count(*) > 1
 ORDER BY last_change DESC
 LIMIT 20;

-- Запрос 11. [журнал загрузок, интервалы] Инженер данных: длительность и результат последних запусков по источникам.
SELECT source, status, started_at, finished_at - started_at AS duration, rows_received, rows_written, rows_rejected
  FROM ops.load_run
 WHERE started_at > now() - interval '7 days'
 ORDER BY started_at DESC
 LIMIT 30;

-- Запрос 12. [операции над множествами] Инженер данных: точки архива 2025 г., отсутствующие в текущем справочнике
-- (исчезли или сменили идентификатор), по операторскому префиксу EvseID.
SELECT split_part(evse_id, '*', 2) AS operator_prefix, count(*) AS n
  FROM (SELECT evse_id FROM core.archive_evse WHERE meta_source = 'details_csv'
        EXCEPT
        SELECT evse_id FROM core.evse) gone
 GROUP BY 1
 ORDER BY n DESC
 LIMIT 15;

-- Запрос 13. [рекурсивный CTE] Инженер данных: все предки показателя «загрузка» в графе происхождения.
WITH RECURSIVE up(node, depth) AS (
    SELECT 'metric.occupancy'::text, 0
    UNION
    SELECT e.from_node, up.depth + 1 FROM ops.lineage_edge e JOIN up ON e.to_node = up.node
)
SELECT up.depth, n.node_type, n.title
  FROM up JOIN ops.lineage_node n ON n.node_id = up.node
 ORDER BY up.depth, n.node_type;

-- Запрос 14. [журнал рекомендаций] Аналитик: среднее ожидаемое время и вероятность для первых мест выдачи по дням.
SELECT r.requested_at::date AS day, count(DISTINCT r.request_id) AS requests,
       round(avg(i.expected_total_min), 1) AS avg_total_min, round(avg(i.p_free), 3) AS avg_p_free
  FROM core.recommendation_request r
  JOIN core.recommendation_item i ON i.request_id = r.request_id AND i.rank = 1
 GROUP BY 1
 ORDER BY 1 DESC;
