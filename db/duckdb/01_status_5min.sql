-- Архив статусов → staging (Parquet): выравнивание времени по 5-минутной сетке, дедупликация.
-- Месяц обрабатывается частями по хэшу EvseID ({part} из {nparts}): каждая точка целиком
-- попадает в одну часть, поэтому эпизоды можно строить по частям независимо.
-- Параметры: {src} — исходный Parquet месяца, {dst} — файл части.
-- Метки времени источника смещены на ±2 с (00:04:59, 00:05:01): округляем до ближайшего слота;
-- при двух записях точки в одном слоте берётся более поздняя (arg_max).
COPY (
    SELECT CAST(to_timestamp(slot_epoch) AT TIME ZONE 'UTC' AS TIMESTAMP) AS slot_ts,
           evse_id, status, operator_id
      FROM (
        SELECT trim(EvseID)                               AS evse_id,
               CAST(round(epoch(datetime) / 300) * 300 AS BIGINT) AS slot_epoch,
               arg_max(EVSEStatus, datetime)              AS status,
               arg_max(OperatorID, datetime)              AS operator_id
          FROM read_parquet('{src}')
         WHERE EvseID IS NOT NULL AND EVSEStatus IS NOT NULL
           AND hash(trim(EvseID)) % {nparts} = {part}
         GROUP BY 1, 2
      )
     ORDER BY evse_id, slot_ts
) TO '{dst}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 500000);
