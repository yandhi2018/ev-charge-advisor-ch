-- Обучающая выборка модели B за один месяц: (точка, t0) → статус через Δ минут.
-- Параметры: {src} — 5-минутные Parquet месяца, {dst} — файл выборки, {share_bp} — доля в базисных
-- пунктах (100 = 1 %). Таблица dim (evse_id, grp, canton_code, power_class) зарегистрирована заранее.
-- Отбор детерминирован (hash), поэтому выборка воспроизводима.
-- Признаки — только то, что доступно приложению в момент запроса: текущий статус точки
-- и число свободных точек на той же станции (grp — совпадающие координаты, ~10 м).
COPY (
    WITH s AS (
        SELECT s.evse_id, s.slot_ts, s.status, d.grp
          FROM read_parquet('{src}') s JOIN dim d USING (evse_id)
    ), origins AS (
        SELECT evse_id, grp, slot_ts AS t0, status AS status_now,
               CAST(5 * (1 + hash(evse_id || '#' || CAST(epoch(slot_ts) AS VARCHAR)) % 12) AS INTEGER) AS delta_min
          FROM s
         WHERE minute(slot_ts) % 15 = 0
           AND hash(CAST(epoch(slot_ts) AS VARCHAR) || '@' || evse_id) % 10000 < {share_bp}
           AND status IN ('Available', 'Occupied', 'OutOfService', 'Reserved', 'Unknown')
    ), labeled AS (
        SELECT o.*, t.status AS status_arr
          FROM origins o
          JOIN s t ON t.evse_id = o.evse_id AND t.slot_ts = o.t0 + to_minutes(o.delta_min)
         WHERE t.status IN ('Available', 'Occupied', 'OutOfService', 'Reserved')
    ), keys AS (
        SELECT DISTINCT grp, t0 FROM labeled
    ), grp_now AS (
        SELECT k.grp, k.t0, count(*) AS n_total,
               count(*) FILTER (WHERE s.status = 'Available') AS n_free
          FROM keys k JOIN s ON s.grp = k.grp AND s.slot_ts = k.t0
         GROUP BY 1, 2
    )
    SELECT l.evse_id, l.t0, l.delta_min, l.status_now,
           CAST(l.status_arr = 'Available' AS TINYINT)                                  AS y,
           g.n_total - 1                                                                AS n_total_other,
           g.n_free - CAST(l.status_now = 'Available' AS INTEGER)                       AS n_free_other
      FROM labeled l JOIN grp_now g ON g.grp = l.grp AND g.t0 = l.t0
) TO '{dst}' (FORMAT parquet);
