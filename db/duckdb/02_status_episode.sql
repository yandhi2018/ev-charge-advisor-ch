-- staging (5 мин) → core (Parquet): эпизоды постоянного статуса точки.
-- Новый эпизод начинается при смене статуса или разрыве наблюдений больше 10 минут.
-- Параметры: {src} — 5-минутный Parquet части месяца, {dst} — файл эпизодов части.
COPY (
    WITH s AS (
        SELECT evse_id, slot_ts, status,
               CASE WHEN lag(status) OVER w IS DISTINCT FROM status
                      OR slot_ts - lag(slot_ts) OVER w > INTERVAL 10 MINUTE
                    THEN 1 ELSE 0 END AS is_new
          FROM read_parquet('{src}')
        WINDOW w AS (PARTITION BY evse_id ORDER BY slot_ts)
    ), g AS (
        SELECT *, sum(is_new) OVER (PARTITION BY evse_id ORDER BY slot_ts) AS episode_no FROM s
    )
    SELECT evse_id, status,
           min(slot_ts)                        AS valid_from,
           max(slot_ts) + INTERVAL 5 MINUTE    AS valid_to,
           count(*)::INTEGER                   AS n_slots
      FROM g
     GROUP BY evse_id, episode_no, status
     ORDER BY evse_id, valid_from
) TO '{dst}' (FORMAT parquet, COMPRESSION zstd);
