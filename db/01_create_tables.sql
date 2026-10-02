-- 01_create_tables.sql — таблицы, ключи и ограничения целостности.
-- Скрипт идемпотентен (IF NOT EXISTS). Изменения существующих таблиц
-- оформляются отдельными файлами db/migrations/NNN_*.sql.

------------------------------------------------------------------------
-- ops: журнал загрузок и метаданные
------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ops.schema_version (
    version_id    serial PRIMARY KEY,
    file_name     text        NOT NULL,
    checksum      char(64)    NOT NULL,
    applied_at    timestamptz NOT NULL DEFAULT now(),
    applied_by    text        NOT NULL DEFAULT current_user,
    UNIQUE (file_name, checksum)
);
COMMENT ON TABLE ops.schema_version IS 'Журнал версий схемы БД: какой SQL-файл с какой контрольной суммой и когда применён';

CREATE TABLE IF NOT EXISTS ops.load_run (
    run_id         uuid        PRIMARY KEY,
    source         text        NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text        NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'success', 'skipped', 'partial', 'failed')),
    params         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    http_status    integer,
    attempts       integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    content_hash   char(64),
    raw_path       text,
    rows_received  bigint      NOT NULL DEFAULT 0 CHECK (rows_received >= 0),
    rows_written   bigint      NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    rows_duplicate bigint      NOT NULL DEFAULT 0 CHECK (rows_duplicate >= 0),
    rows_rejected  bigint      NOT NULL DEFAULT 0 CHECK (rows_rejected >= 0),
    error_text     text,
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (status = 'running' OR finished_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS load_run_source_started_idx ON ops.load_run (source, started_at DESC);
COMMENT ON TABLE ops.load_run IS 'Журнал загрузок: одна строка на запуск загрузчика или преобразования';

CREATE TABLE IF NOT EXISTS ops.raw_manifest (
    raw_path    text        PRIMARY KEY,
    run_id      uuid        NOT NULL REFERENCES ops.load_run (run_id) ON DELETE RESTRICT,
    source      text        NOT NULL,
    sha256      char(64)    NOT NULL,
    size_bytes  bigint      NOT NULL CHECK (size_bytes >= 0),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_manifest_sha_idx ON ops.raw_manifest (source, sha256);
COMMENT ON TABLE ops.raw_manifest IS 'Реестр неизменяемых raw-файлов: путь, хэш, запуск, который его создал';

CREATE TABLE IF NOT EXISTS ops.source_schema (
    source       text        NOT NULL,
    fields_hash  char(64)    NOT NULL,
    fields       jsonb       NOT NULL,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    last_seen    timestamptz NOT NULL DEFAULT now(),
    first_run_id uuid        REFERENCES ops.load_run (run_id),
    PRIMARY KEY (source, fields_hash)
);
COMMENT ON TABLE ops.source_schema IS 'Реестр структур источников: новая строка = источник изменил набор полей';

CREATE TABLE IF NOT EXISTS ops.dq_check (
    check_id     text PRIMARY KEY,
    layer        text NOT NULL CHECK (layer IN ('raw', 'stg', 'core', 'mart')),
    table_name   text NOT NULL,
    check_type   text NOT NULL CHECK (check_type IN
                 ('completeness', 'uniqueness', 'validity', 'type', 'range', 'referential', 'freshness')),
    severity     text NOT NULL CHECK (severity IN ('error', 'warn')),
    description  text NOT NULL,
    sql_text     text NOT NULL
);
COMMENT ON TABLE ops.dq_check IS 'Каталог проверок качества (загружается из config/dq_checks.yaml)';

CREATE TABLE IF NOT EXISTS ops.dq_result (
    result_id    bigserial   PRIMARY KEY,
    check_id     text        NOT NULL REFERENCES ops.dq_check (check_id) ON DELETE CASCADE,
    run_id       uuid        REFERENCES ops.load_run (run_id),
    checked_at   timestamptz NOT NULL DEFAULT now(),
    passed       boolean     NOT NULL,
    failed_rows  bigint      NOT NULL DEFAULT 0 CHECK (failed_rows >= 0),
    sample       jsonb
);
CREATE INDEX IF NOT EXISTS dq_result_check_idx ON ops.dq_result (check_id, checked_at DESC);
COMMENT ON TABLE ops.dq_result IS 'Результаты проверок качества по запускам';

CREATE TABLE IF NOT EXISTS ops.quarantine (
    quarantine_id bigserial   PRIMARY KEY,
    run_id        uuid        REFERENCES ops.load_run (run_id),
    table_name    text        NOT NULL,
    record        jsonb       NOT NULL,
    reason        text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ops.quarantine IS 'Записи, не прошедшие проверки и не допущенные в core';

CREATE TABLE IF NOT EXISTS ops.lineage_node (
    node_id     text PRIMARY KEY,
    node_type   text NOT NULL CHECK (node_type IN
                ('source', 'loader', 'raw', 'stg', 'transform', 'core', 'mart', 'model', 'metric', 'dashboard')),
    title       text NOT NULL,
    description text,
    ref         text
);
CREATE TABLE IF NOT EXISTS ops.lineage_edge (
    from_node text NOT NULL REFERENCES ops.lineage_node (node_id) ON DELETE CASCADE,
    to_node   text NOT NULL REFERENCES ops.lineage_node (node_id) ON DELETE CASCADE,
    PRIMARY KEY (from_node, to_node),
    CHECK (from_node <> to_node)
);
COMMENT ON TABLE ops.lineage_edge IS 'Граф происхождения данных: источник → загрузка → слои → витрина → показатель → дашборд';

CREATE TABLE IF NOT EXISTS ops.model_registry (
    model_id      serial      PRIMARY KEY,
    task          text        NOT NULL CHECK (task IN ('canton', 'availability')),
    model_name    text        NOT NULL,
    version       text        NOT NULL,
    trained_at    timestamptz NOT NULL DEFAULT now(),
    data_hash     char(64),
    params        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    metrics       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    artifact_path text,
    is_active     boolean     NOT NULL DEFAULT false,
    UNIQUE (task, model_name, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS model_registry_one_active
    ON ops.model_registry (task) WHERE is_active;
COMMENT ON TABLE ops.model_registry IS 'Реестр обученных моделей; активна не более одной модели на задачу';

------------------------------------------------------------------------
-- stg: данные источников после приведения типов (1:1 к источнику)
------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg.evse_data (
    stg_id          bigserial PRIMARY KEY,
    run_id          uuid      NOT NULL REFERENCES ops.load_run (run_id) ON DELETE CASCADE,
    evse_id         text,
    operator_id     text,
    operator_name   text,
    station_ext_id  text,
    pool_ext_id     text,
    station_name    text,
    street          text,
    postal_code     text,
    city            text,
    country         text,
    lat             double precision,
    lon             double precision,
    accessibility   text,
    access_location text,
    is_open_24h     boolean,
    plugs           text[],
    power_kw        numeric(7, 2),
    power_type      text,
    auth_modes      text[],
    last_update     timestamptz,
    record_hash     char(64)  NOT NULL
);
CREATE INDEX IF NOT EXISTS stg_evse_data_run_idx ON stg.evse_data (run_id, evse_id);

CREATE TABLE IF NOT EXISTS stg.evse_status (
    run_id      uuid        NOT NULL REFERENCES ops.load_run (run_id) ON DELETE CASCADE,
    slot_ts     timestamptz NOT NULL,
    evse_id     text,
    status      text,
    operator_id text
);
CREATE INDEX IF NOT EXISTS stg_evse_status_run_idx ON stg.evse_status (run_id);

CREATE TABLE IF NOT EXISTS stg.weather_hourly (
    run_id         uuid        NOT NULL REFERENCES ops.load_run (run_id) ON DELETE CASCADE,
    canton_code    text        NOT NULL,
    hour_utc       timestamptz NOT NULL,
    kind           text        NOT NULL,
    temperature_c  real,
    precipitation_mm real,
    snowfall_cm    real,
    wind_kmh       real
);
CREATE INDEX IF NOT EXISTS stg_weather_run_idx ON stg.weather_hourly (run_id);

CREATE TABLE IF NOT EXISTS stg.archive_details (
    run_id      uuid    NOT NULL REFERENCES ops.load_run (run_id) ON DELETE CASCADE,
    evse_id     text,
    lat         double precision,
    lon         double precision,
    postal_code text,
    power_kw    numeric(7, 2),
    power_type  text
);

CREATE TABLE IF NOT EXISTS stg.vehicle (
    run_id        uuid    NOT NULL REFERENCES ops.load_run (run_id) ON DELETE CASCADE,
    source        text    NOT NULL,
    ext_id        text,
    brand         text,
    model         text,
    variant       text,
    release_year  integer,
    battery_kwh   numeric(6, 1),
    ac_max_kw     numeric(6, 1),
    dc_max_kw     numeric(6, 1),
    plugs         text[]
);

------------------------------------------------------------------------
-- core: модель предметной области
------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.canton (
    canton_code char(2) PRIMARY KEY CHECK (canton_code ~ '^[A-Z]{2}$'),
    name        text    NOT NULL UNIQUE,
    lat         double precision NOT NULL CHECK (lat BETWEEN 45.8 AND 47.9),
    lon         double precision NOT NULL CHECK (lon BETWEEN 5.9 AND 10.6)
);
COMMENT ON TABLE core.canton IS 'Справочник кантонов; координаты административного центра — опорная точка погоды';

CREATE TABLE IF NOT EXISTS core.postal_code (
    zip_id        integer PRIMARY KEY,
    postal_code   char(4) NOT NULL CHECK (postal_code ~ '^[0-9]{4}$'),
    locality      text    NOT NULL,
    municipality  text    NOT NULL,
    canton_code   char(2) NOT NULL REFERENCES core.canton (canton_code),
    address_share real    CHECK (address_share BETWEEN 0 AND 100),
    lat           double precision NOT NULL,
    lon           double precision NOT NULL
);
CREATE INDEX IF NOT EXISTS postal_code_plz_idx ON core.postal_code (postal_code);
COMMENT ON TABLE core.postal_code IS 'Почтовые индексы Швейцарии с кантоном (swisstopo); используется для привязки станций к кантону';

CREATE TABLE IF NOT EXISTS core.operator (
    operator_id text        PRIMARY KEY,
    name        text        NOT NULL,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.operator IS 'Операторы зарядных сетей (OperatorID по OICP)';

CREATE TABLE IF NOT EXISTS core.plug_type (
    plug_code       text    PRIMARY KEY,
    name_ru         text    NOT NULL UNIQUE,
    current_type    text    NOT NULL CHECK (current_type IN ('AC', 'DC')),
    needs_own_cable boolean NOT NULL DEFAULT false
);
COMMENT ON TABLE core.plug_type IS 'Канонические типы разъёмов (жёсткое ограничение совместимости)';

CREATE TABLE IF NOT EXISTS core.plug_alias (
    source      text NOT NULL CHECK (source IN ('oicp', 'open-ev-data')),
    source_name text NOT NULL,
    plug_code   text NOT NULL REFERENCES core.plug_type (plug_code) ON UPDATE CASCADE,
    PRIMARY KEY (source, source_name, plug_code)
);
COMMENT ON TABLE core.plug_alias IS 'Отображение названий разъёмов источников на канонические коды. Для автомобилей одно '
    'название может давать несколько кодов: вход Type 2 подходит и к розетке, и к кабелю Type 2';

CREATE TABLE IF NOT EXISTS core.station (
    station_id    serial      PRIMARY KEY,
    station_key   char(32)    NOT NULL UNIQUE,
    name          text,
    street        text,
    postal_code   text,
    city          text,
    canton_code   char(2)     REFERENCES core.canton (canton_code),
    lat           double precision NOT NULL CHECK (lat BETWEEN 45.5 AND 48.0),
    lon           double precision NOT NULL CHECK (lon BETWEEN 5.5 AND 11.0),
    operator_id   text        NOT NULL REFERENCES core.operator (operator_id),
    is_open_24h   boolean,
    accessibility text        CHECK (accessibility IN
                  ('Free publicly accessible', 'Paying publicly accessible', 'Restricted access', 'Test Station')),
    first_seen    timestamptz NOT NULL DEFAULT now(),
    last_seen     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS station_geo_idx ON core.station (lat, lon);
COMMENT ON TABLE core.station IS 'Станция = точки одного оператора по одному адресу в радиусе 30 м (разрешение сущностей)';

CREATE TABLE IF NOT EXISTS core.evse (
    evse_id     text        PRIMARY KEY,
    station_id  integer     NOT NULL REFERENCES core.station (station_id) ON DELETE RESTRICT,
    operator_id text        NOT NULL REFERENCES core.operator (operator_id),
    power_kw    numeric(7, 2) CHECK (power_kw > 0 AND power_kw <= 1000),
    power_type  text        CHECK (power_type IN ('AC_1_PHASE', 'AC_3_PHASE', 'DC')),
    power_class text        GENERATED ALWAYS AS (
                    CASE WHEN power_type = 'DC' THEN 'DC'
                         WHEN power_type LIKE 'AC%' THEN 'AC' END) STORED,
    is_active   boolean     NOT NULL DEFAULT true,
    row_hash    char(64)    NOT NULL,
    valid_from  timestamptz NOT NULL DEFAULT now(),
    last_run_id uuid        REFERENCES ops.load_run (run_id),
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evse_station_idx ON core.evse (station_id);
COMMENT ON TABLE core.evse IS 'Зарядная точка (EVSE) — текущая версия; история изменений в core.evse_history';

CREATE TABLE IF NOT EXISTS core.evse_plug (
    evse_id   text NOT NULL REFERENCES core.evse (evse_id) ON DELETE CASCADE,
    plug_code text NOT NULL REFERENCES core.plug_type (plug_code),
    PRIMARY KEY (evse_id, plug_code)
);
COMMENT ON TABLE core.evse_plug IS 'Разъёмы зарядной точки (связь M:N точка — тип разъёма)';

CREATE TABLE IF NOT EXISTS core.evse_history (
    history_id  bigserial   PRIMARY KEY,
    evse_id     text        NOT NULL REFERENCES core.evse (evse_id) ON DELETE CASCADE,
    station_id  integer     NOT NULL,
    power_kw    numeric(7, 2),
    power_type  text,
    is_active   boolean     NOT NULL,
    row_hash    char(64)    NOT NULL,
    valid_from  timestamptz NOT NULL,
    valid_to    timestamptz,
    change_type text        NOT NULL CHECK (change_type IN ('insert', 'update', 'deactivate', 'reactivate')),
    run_id      uuid        REFERENCES ops.load_run (run_id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS evse_history_one_open
    ON core.evse_history (evse_id) WHERE valid_to IS NULL;
COMMENT ON TABLE core.evse_history IS 'SCD2-история зарядных точек; ведётся триггером на core.evse';

CREATE TABLE IF NOT EXISTS core.vehicle (
    vehicle_id   serial        PRIMARY KEY,
    brand        text          NOT NULL,
    model        text          NOT NULL,
    variant      text          NOT NULL DEFAULT '',
    release_year integer       CHECK (release_year BETWEEN 2008 AND 2035),
    battery_kwh  numeric(6, 1) CHECK (battery_kwh > 0 AND battery_kwh <= 250),
    ac_max_kw    numeric(6, 1) NOT NULL CHECK (ac_max_kw > 0 AND ac_max_kw <= 43),
    dc_max_kw    numeric(6, 1) CHECK (dc_max_kw > 0 AND dc_max_kw <= 500),
    source       text          NOT NULL CHECK (source IN ('open-ev-data', 'manual')),
    UNIQUE (brand, model, variant, release_year)
);
COMMENT ON TABLE core.vehicle IS 'Каталог электромобилей: пределы мощности AC/DC (dc_max_kw NULL — без быстрой зарядки)';

CREATE TABLE IF NOT EXISTS core.vehicle_plug (
    vehicle_id integer NOT NULL REFERENCES core.vehicle (vehicle_id) ON DELETE CASCADE,
    plug_code  text    NOT NULL REFERENCES core.plug_type (plug_code),
    PRIMARY KEY (vehicle_id, plug_code)
);
COMMENT ON TABLE core.vehicle_plug IS 'Разъёмы автомобиля (связь M:N автомобиль — тип разъёма)';

CREATE TABLE IF NOT EXISTS core.weather_hourly (
    canton_code      char(2)     NOT NULL REFERENCES core.canton (canton_code),
    hour_utc         timestamptz NOT NULL,
    kind             text        NOT NULL CHECK (kind IN ('actual', 'hist_forecast', 'forecast')),
    temperature_c    real        CHECK (temperature_c BETWEEN -45 AND 45),
    precipitation_mm real        CHECK (precipitation_mm >= 0),
    snowfall_cm      real        CHECK (snowfall_cm >= 0),
    wind_kmh         real        CHECK (wind_kmh >= 0),
    run_id           uuid        NOT NULL REFERENCES ops.load_run (run_id),
    loaded_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (canton_code, hour_utc, kind)
);
COMMENT ON TABLE core.weather_hourly IS 'Почасовая погода по кантонам: факт, архивный прогноз, текущий прогноз';

CREATE TABLE IF NOT EXISTS core.status_snapshot (
    slot_ts  timestamptz NOT NULL CHECK (date_part('minute', slot_ts)::int % 5 = 0
                                         AND date_part('second', slot_ts) = 0),
    evse_id  text        NOT NULL REFERENCES core.evse (evse_id) ON DELETE CASCADE,
    status   text        NOT NULL CHECK (status IN
             ('Available', 'Occupied', 'OutOfService', 'Unknown', 'Reserved', 'EvseNotFound')),
    run_id   uuid        NOT NULL REFERENCES ops.load_run (run_id),
    PRIMARY KEY (slot_ts, evse_id)
);
CREATE INDEX IF NOT EXISTS status_snapshot_evse_idx ON core.status_snapshot (evse_id, slot_ts DESC);
COMMENT ON TABLE core.status_snapshot IS 'Снимки текущего статуса точек (5-минутные слоты), получаемые при поиске водителем';

CREATE TABLE IF NOT EXISTS core.archive_evse (
    evse_id     text    PRIMARY KEY,
    canton_code char(2) REFERENCES core.canton (canton_code),
    power_class text    CHECK (power_class IN ('AC', 'DC')),
    power_kw    numeric(7, 2) CHECK (power_kw > 0),
    lat         double precision,
    lon         double precision,
    meta_source text    NOT NULL CHECK (meta_source IN ('evse_data', 'details_csv')),
    run_id      uuid    REFERENCES ops.load_run (run_id)
);
COMMENT ON TABLE core.archive_evse IS 'Метаданные точек архива статусов: кантон и класс мощности. Из текущего справочника, '
    'а для исчезнувших точек — из ChargingStationDetails (февраль 2026)';

CREATE TABLE IF NOT EXISTS core.recommendation_request (
    request_id     uuid        PRIMARY KEY,
    requested_at   timestamptz NOT NULL DEFAULT now(),
    vehicle_id     integer     NOT NULL REFERENCES core.vehicle (vehicle_id),
    lat_round      numeric(5, 2) NOT NULL CHECK (lat_round BETWEEN 45.5 AND 48.0),
    lon_round      numeric(5, 2) NOT NULL CHECK (lon_round BETWEEN 5.5 AND 11.0),
    energy_kwh     numeric(5, 1) NOT NULL CHECK (energy_kwh > 0 AND energy_kwh <= 150),
    filters        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status_slot_ts timestamptz,
    model_version  text,
    n_results      integer     NOT NULL DEFAULT 0 CHECK (n_results >= 0)
);
COMMENT ON TABLE core.recommendation_request IS 'Журнал запросов водителей; координаты огрублены до ~1 км (не персональные данные)';

CREATE TABLE IF NOT EXISTS core.recommendation_item (
    request_id      uuid    NOT NULL REFERENCES core.recommendation_request (request_id) ON DELETE CASCADE,
    rank            smallint NOT NULL CHECK (rank BETWEEN 1 AND 20),
    station_id      integer NOT NULL REFERENCES core.station (station_id),
    distance_km     numeric(6, 2) NOT NULL CHECK (distance_km >= 0),
    eta_min         numeric(6, 1) NOT NULL CHECK (eta_min >= 0),
    p_free          numeric(4, 3) NOT NULL CHECK (p_free BETWEEN 0 AND 1),
    effective_kw    numeric(6, 1) CHECK (effective_kw > 0),
    expected_total_min numeric(7, 1) NOT NULL CHECK (expected_total_min >= 0),
    PRIMARY KEY (request_id, rank),
    UNIQUE (request_id, station_id)
);
COMMENT ON TABLE core.recommendation_item IS 'Выдача рекомендателя: станции по рангу с компонентами оценки';

------------------------------------------------------------------------
-- mart: витрины (наполняются процедурами и обработкой архива в DuckDB)
------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mart.occupancy_hourly (
    canton_code      char(2)     NOT NULL REFERENCES core.canton (canton_code),
    power_class      text        NOT NULL CHECK (power_class IN ('AC', 'DC')),
    hour_utc         timestamptz NOT NULL,
    n_evse           integer     NOT NULL CHECK (n_evse >= 0),
    avg_available    real        NOT NULL CHECK (avg_available >= 0),
    avg_occupied     real        NOT NULL CHECK (avg_occupied >= 0),
    avg_out_of_service real      NOT NULL CHECK (avg_out_of_service >= 0),
    avg_unknown      real        NOT NULL CHECK (avg_unknown >= 0),
    occupancy_rate   real        CHECK (occupancy_rate BETWEEN 0 AND 1),
    oos_rate         real        CHECK (oos_rate BETWEEN 0 AND 1),
    slots_observed   smallint    NOT NULL CHECK (slots_observed BETWEEN 0 AND 12),
    source           text        NOT NULL CHECK (source IN ('archive', 'live')),
    PRIMARY KEY (canton_code, power_class, hour_utc)
);
COMMENT ON TABLE mart.occupancy_hourly IS 'Показатель A: почасовая загрузка = Occupied/(Available+Occupied) по кантону и классу мощности';

CREATE TABLE IF NOT EXISTS mart.profile_group (
    canton_code  char(2)  NOT NULL REFERENCES core.canton (canton_code),
    power_class  text     NOT NULL CHECK (power_class IN ('AC', 'DC')),
    dow          smallint NOT NULL CHECK (dow BETWEEN 0 AND 6),
    hour_local   smallint NOT NULL CHECK (hour_local BETWEEN 0 AND 23),
    p_free       real     NOT NULL CHECK (p_free BETWEEN 0 AND 1),
    p_occupied   real     NOT NULL CHECK (p_occupied BETWEEN 0 AND 1),
    n_obs        bigint   NOT NULL CHECK (n_obs > 0),
    PRIMARY KEY (canton_code, power_class, dow, hour_local)
);
COMMENT ON TABLE mart.profile_group IS 'Исторический профиль «доля времени свободна» по кантону, классу, дню недели и часу';

CREATE TABLE IF NOT EXISTS mart.evse_profile (
    evse_id     text     NOT NULL,
    dow         smallint NOT NULL CHECK (dow BETWEEN 0 AND 6),
    hour_local  smallint NOT NULL CHECK (hour_local BETWEEN 0 AND 23),
    p_free      real     NOT NULL CHECK (p_free BETWEEN 0 AND 1),
    n_obs       integer  NOT NULL CHECK (n_obs > 0),
    PRIMARY KEY (evse_id, dow, hour_local)
);
COMMENT ON TABLE mart.evse_profile IS 'Профиль доступности конкретной точки (только точки, присутствующие в архиве)';

CREATE TABLE IF NOT EXISTS mart.archive_coverage (
    month           date    PRIMARY KEY,
    n_rows          bigint  NOT NULL CHECK (n_rows >= 0),
    n_slots         integer NOT NULL CHECK (n_slots >= 0),
    expected_slots  integer NOT NULL CHECK (expected_slots > 0),
    n_evse          integer NOT NULL CHECK (n_evse >= 0),
    share_with_geo  real    CHECK (share_with_geo BETWEEN 0 AND 1),
    share_unknown   real    CHECK (share_unknown BETWEEN 0 AND 1),
    run_id          uuid    REFERENCES ops.load_run (run_id)
);
COMMENT ON TABLE mart.archive_coverage IS 'Полнота архива по месяцам: слоты, точки, доля с геопривязкой, доля Unknown';

CREATE TABLE IF NOT EXISTS mart.forecast_canton (
    model_name   text        NOT NULL,
    fold         smallint    NOT NULL,
    origin_utc   timestamptz NOT NULL,
    horizon_h    smallint    NOT NULL CHECK (horizon_h BETWEEN 1 AND 48),
    canton_code  char(2)     NOT NULL REFERENCES core.canton (canton_code),
    power_class  text        NOT NULL CHECK (power_class IN ('AC', 'DC')),
    y_pred       real        NOT NULL,
    y_true       real,
    PRIMARY KEY (model_name, fold, origin_utc, horizon_h, canton_code, power_class)
);
COMMENT ON TABLE mart.forecast_canton IS 'Прогнозы показателя A (тест и боевой режим, fold = 0)';

CREATE TABLE IF NOT EXISTS mart.model_metric (
    task        text        NOT NULL CHECK (task IN ('canton', 'availability', 'recommender')),
    model_name  text        NOT NULL,
    fold        smallint    NOT NULL,
    metric      text        NOT NULL,
    slice       text        NOT NULL DEFAULT 'all',
    value       double precision NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (task, model_name, fold, metric, slice)
);
COMMENT ON TABLE mart.model_metric IS 'Метрики моделей по фолдам и срезам (горизонт, класс мощности, текущий статус)';

CREATE TABLE IF NOT EXISTS mart.calibration_bin (
    model_name  text     NOT NULL,
    bin         smallint NOT NULL CHECK (bin BETWEEN 0 AND 19),
    p_mean      real     NOT NULL,
    y_mean      real     NOT NULL,
    n           integer  NOT NULL CHECK (n > 0),
    PRIMARY KEY (model_name, bin)
);
COMMENT ON TABLE mart.calibration_bin IS 'Диаграмма надёжности модели доступности (тестовый период)';
