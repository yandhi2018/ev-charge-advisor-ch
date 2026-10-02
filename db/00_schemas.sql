-- 00_schemas.sql — схемы слоёв хранения.
-- Выполняется владельцем БД (ev_admin). Скрипт идемпотентен.

CREATE SCHEMA IF NOT EXISTS stg;   -- staging: типизированные данные источников 1:1, привязаны к run_id
CREATE SCHEMA IF NOT EXISTS core;  -- core: интегрированная модель предметной области, справочники, история
CREATE SCHEMA IF NOT EXISTS mart;  -- mart: витрины для потребителей (дашборды, рекомендатель, модели)
CREATE SCHEMA IF NOT EXISTS ops;   -- ops: журнал загрузок, качество данных, версии схемы, lineage, модели

-- Слой raw хранится файлами в data/raw/ (неизменяемые копии ответов источников);
-- в БД его представляет реестр ops.raw_manifest.

COMMENT ON SCHEMA stg  IS 'Слой staging: данные источников после приведения типов, 1:1 к источнику';
COMMENT ON SCHEMA core IS 'Слой core: интегрированная модель предметной области (справочники, SCD2, факты)';
COMMENT ON SCHEMA mart IS 'Слой mart: витрины для дашбордов, рекомендателя и прогнозных моделей';
COMMENT ON SCHEMA ops  IS 'Служебный слой: журнал загрузок, качество данных, метаданные, lineage';
