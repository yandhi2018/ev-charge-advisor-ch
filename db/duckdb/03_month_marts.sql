-- staging (5 мин) + метаданные точек → агрегаты месяца для витрин PostgreSQL.
-- Таблица dim (evse_id, canton_code, power_class) регистрируется из core.archive_evse.
-- Параметры: {src} — 5-минутный Parquet месяца; {occ}, {prof}, {cov} — файлы результатов.

-- 1. Почасовая загрузка по кантону и классу мощности (показатель A)
COPY (
    WITH per_slot AS (
        SELECT d.canton_code, d.power_class, s.slot_ts,
               count(*)                                          AS n_evse,
               count(*) FILTER (WHERE s.status = 'Available')    AS n_av,
               count(*) FILTER (WHERE s.status = 'Occupied')     AS n_oc,
               count(*) FILTER (WHERE s.status = 'OutOfService') AS n_oos,
               count(*) FILTER (WHERE s.status NOT IN ('Available', 'Occupied', 'OutOfService')) AS n_unk
          FROM read_parquet('{src}') s
          JOIN dim d ON d.evse_id = s.evse_id
         WHERE d.canton_code IS NOT NULL AND d.power_class IS NOT NULL
         GROUP BY ALL
    )
    SELECT canton_code, power_class, date_trunc('hour', slot_ts) AS hour_utc,
           max(n_evse)::INTEGER AS n_evse,
           avg(n_av)  AS avg_available, avg(n_oc) AS avg_occupied,
           avg(n_oos) AS avg_out_of_service, avg(n_unk) AS avg_unknown,
           CASE WHEN sum(n_av + n_oc) > 0 THEN sum(n_oc) / sum(n_av + n_oc) END AS occupancy_rate,
           CASE WHEN sum(n_evse) > 0 THEN sum(n_oos) / sum(n_evse) END        AS oos_rate,
           count(DISTINCT slot_ts)::SMALLINT AS slots_observed
      FROM per_slot
     GROUP BY ALL
) TO '{occ}' (FORMAT parquet);

-- 2. Частичные суммы профиля точки: день недели × местный час (Europe/Zurich).
-- Unknown и EvseNotFound не несут информации о занятости и в знаменатель не входят.
COPY (
    SELECT evse_id,
           CAST((dayofweek(local_ts) + 6) % 7 AS TINYINT) AS dow,     -- 0 = понедельник
           CAST(hour(local_ts) AS TINYINT)                  AS hour_local,
           count(*) FILTER (WHERE status = 'Available')     AS n_free,
           count(*) FILTER (WHERE status = 'Occupied')      AS n_occ,
           count(*)                                          AS n_obs
      FROM (SELECT evse_id, status,
                   (CAST(slot_ts AS TIMESTAMP) AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/Zurich' AS local_ts
              FROM read_parquet('{src}')
             WHERE status IN ('Available', 'Occupied', 'OutOfService', 'Reserved'))
     GROUP BY ALL
) TO '{prof}' (FORMAT parquet);

-- 3. Полнота месяца
COPY (
    SELECT count(*)                                                   AS n_rows,
           count(DISTINCT slot_ts)                                    AS n_slots,
           count(DISTINCT s.evse_id)                                  AS n_evse,
           count(DISTINCT s.evse_id) FILTER (WHERE d.canton_code IS NOT NULL)
               / nullif(count(DISTINCT s.evse_id), 0)                 AS share_with_geo,
           avg(CASE WHEN status IN ('Unknown', 'EvseNotFound') THEN 1.0 ELSE 0.0 END) AS share_unknown
      FROM read_parquet('{src}') s
      LEFT JOIN dim d ON d.evse_id = s.evse_id
) TO '{cov}' (FORMAT parquet);
