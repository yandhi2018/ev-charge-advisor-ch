# Словарь данных

Сгенерировано командой `evadvisor docs` из каталога PostgreSQL 02.10.2026. Не редактируйте вручную: описания берутся из `COMMENT ON` в `db/*.sql`.

## Таблицы

| Таблица | Назначение | Строк |
|---|---|---|
| `core.archive_evse` | Метаданные точек архива статусов: кантон и класс мощности. Из текущего справочника, а для исчезнувших точек — из ChargingStationDetails (февраль 2026) | 21 936 |
| `core.canton` | Справочник кантонов; координаты административного центра — опорная точка погоды | 26 |
| `core.evse` | Зарядная точка (EVSE) — текущая версия; история изменений в core.evse_history | 17 044 |
| `core.evse_history` | SCD2-история зарядных точек; ведётся триггером на core.evse | 17 044 |
| `core.evse_plug` | Разъёмы зарядной точки (связь M:N точка — тип разъёма) | 17 552 |
| `core.operator` | Операторы зарядных сетей (OperatorID по OICP) | 41 |
| `core.plug_alias` | Отображение названий разъёмов источников на канонические коды. Для автомобилей одно название может давать несколько кодов: вход Type 2 подходит и к розетке, и к кабелю Type 2 | 17 |
| `core.plug_type` | Канонические типы разъёмов (жёсткое ограничение совместимости) | 9 |
| `core.postal_code` | Почтовые индексы Швейцарии с кантоном (swisstopo); используется для привязки станций к кантону | 4 060 |
| `core.recommendation_item` | Выдача рекомендателя: станции по рангу с компонентами оценки | 10 |
| `core.recommendation_request` | Журнал запросов водителей; координаты огрублены до ~1 км (не персональные данные) | 2 |
| `core.station` | Станция = точки одного оператора по одному адресу в радиусе 30 м (разрешение сущностей) | 5 794 |
| `core.status_snapshot` | Снимки текущего статуса точек (5-минутные слоты), получаемые при поиске водителем | 17 044 |
| `core.vehicle` | Каталог электромобилей: пределы мощности AC/DC (dc_max_kw NULL — без быстрой зарядки) | 1 284 |
| `core.vehicle_plug` | Разъёмы автомобиля (связь M:N автомобиль — тип разъёма) | 3 876 |
| `core.weather_hourly` | Почасовая погода по кантонам: факт, архивный прогноз, текущий прогноз | 1 230 744 |
| `mart.archive_coverage` | Полнота архива по месяцам: слоты, точки, доля с геопривязкой, доля Unknown | 3 |
| `mart.calibration_bin` | Диаграмма надёжности модели доступности (тестовый период) | 0 |
| `mart.evse_profile` | Профиль доступности конкретной точки (только точки, присутствующие в архиве) | 0 |
| `mart.forecast_canton` | Прогнозы показателя A (тест и боевой режим, fold = 0) | 0 |
| `mart.model_metric` | Метрики моделей по фолдам и срезам (горизонт, класс мощности, текущий статус) | 0 |
| `mart.occupancy_hourly` | Показатель A: почасовая загрузка = Occupied/(Available+Occupied) по кантону и классу мощности | 120 075 |
| `mart.profile_group` | Исторический профиль «доля времени свободна» по кантону, классу, дню недели и часу | 0 |
| `ops.dq_check` | Каталог проверок качества (загружается из config/dq_checks.yaml) | 0 |
| `ops.dq_result` | Результаты проверок качества по запускам | 0 |
| `ops.lineage_edge` | Граф происхождения данных: источник → загрузка → слои → витрина → показатель → дашборд | 0 |
| `ops.lineage_node` |  | 0 |
| `ops.load_run` | Журнал загрузок: одна строка на запуск загрузчика или преобразования | 54 |
| `ops.model_registry` | Реестр обученных моделей; активна не более одной модели на задачу | 0 |
| `ops.quarantine` | Записи, не прошедшие проверки и не допущенные в core | 160 |
| `ops.raw_manifest` | Реестр неизменяемых raw-файлов: путь, хэш, запуск, который его создал | 182 |
| `ops.schema_version` | Журнал версий схемы БД: какой SQL-файл с какой контрольной суммой и когда применён | 8 |
| `ops.source_schema` | Реестр структур источников: новая строка = источник изменил набор полей | 3 |
| `stg.archive_details` |  | 0 |
| `stg.evse_data` |  | 17 207 |
| `stg.evse_status` |  | 0 |
| `stg.vehicle` |  | 0 |
| `stg.weather_hourly` |  | 0 |

### `core.archive_evse`

Метаданные точек архива статусов: кантон и класс мощности. Из текущего справочника, а для исчезнувших точек — из ChargingStationDetails (февраль 2026)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `evse_id` | text | нет |  |  |
| `canton_code` | character | да |  |  |
| `power_class` | text | да |  |  |
| `power_kw` | numeric | да |  |  |
| `lat` | double precision | да |  |  |
| `lon` | double precision | да |  |  |
| `meta_source` | text | нет |  |  |
| `run_id` | uuid | да |  |  |

Ограничения:

- CHECK: `CHECK ((power_kw > (0)::numeric))`
- CHECK: `CHECK ((meta_source = ANY (ARRAY['evse_data'::text, 'details_csv'::text])))`
- CHECK: `CHECK ((power_class = ANY (ARRAY['AC'::text, 'DC'::text])))`
- FK: `FOREIGN KEY (canton_code) REFERENCES core.canton(canton_code)`
- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (evse_id)`

### `core.canton`

Справочник кантонов; координаты административного центра — опорная точка погоды

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `canton_code` | character | нет |  |  |
| `name` | text | нет |  |  |
| `lat` | double precision | нет |  |  |
| `lon` | double precision | нет |  |  |

Ограничения:

- CHECK: `CHECK (((lat >= (45.8)::double precision) AND (lat <= (47.9)::double precision)))`
- CHECK: `CHECK ((canton_code ~ '^[A-Z]{2}$'::text))`
- CHECK: `CHECK (((lon >= (5.9)::double precision) AND (lon <= (10.6)::double precision)))`
- PK: `PRIMARY KEY (canton_code)`
- UNIQUE: `UNIQUE (name)`

### `core.evse`

Зарядная точка (EVSE) — текущая версия; история изменений в core.evse_history

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `evse_id` | text | нет |  |  |
| `station_id` | integer | нет |  |  |
| `operator_id` | text | нет |  |  |
| `power_kw` | numeric | да |  |  |
| `power_type` | text | да |  |  |
| `power_class` | text | да |  |  |
| `is_active` | boolean | нет | true |  |
| `row_hash` | character | нет |  |  |
| `valid_from` | timestamp with time zone | нет | now() |  |
| `last_run_id` | uuid | да |  |  |
| `first_seen` | timestamp with time zone | нет | now() |  |
| `last_seen` | timestamp with time zone | нет | now() |  |

Ограничения:

- CHECK: `CHECK (((power_kw > (0)::numeric) AND (power_kw <= (1000)::numeric)))`
- CHECK: `CHECK ((power_type = ANY (ARRAY['AC_1_PHASE'::text, 'AC_3_PHASE'::text, 'DC'::text])))`
- FK: `FOREIGN KEY (operator_id) REFERENCES core.operator(operator_id)`
- FK: `FOREIGN KEY (station_id) REFERENCES core.station(station_id) ON DELETE RESTRICT`
- FK: `FOREIGN KEY (last_run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (evse_id)`

### `core.evse_history`

SCD2-история зарядных точек; ведётся триггером на core.evse

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `history_id` | bigint | нет | nextval('core.evse_history_history_id_seq'::regclass) |  |
| `evse_id` | text | нет |  |  |
| `station_id` | integer | нет |  |  |
| `power_kw` | numeric | да |  |  |
| `power_type` | text | да |  |  |
| `is_active` | boolean | нет |  |  |
| `row_hash` | character | нет |  |  |
| `valid_from` | timestamp with time zone | нет |  |  |
| `valid_to` | timestamp with time zone | да |  |  |
| `change_type` | text | нет |  |  |
| `run_id` | uuid | да |  |  |

Ограничения:

- CHECK: `CHECK (((valid_to IS NULL) OR (valid_to >= valid_from)))`
- CHECK: `CHECK ((change_type = ANY (ARRAY['insert'::text, 'update'::text, 'deactivate'::text, 'reactivate'::text])))`
- FK: `FOREIGN KEY (evse_id) REFERENCES core.evse(evse_id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (history_id)`

### `core.evse_plug`

Разъёмы зарядной точки (связь M:N точка — тип разъёма)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `evse_id` | text | нет |  |  |
| `plug_code` | text | нет |  |  |

Ограничения:

- FK: `FOREIGN KEY (evse_id) REFERENCES core.evse(evse_id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (plug_code) REFERENCES core.plug_type(plug_code)`
- PK: `PRIMARY KEY (evse_id, plug_code)`

### `core.operator`

Операторы зарядных сетей (OperatorID по OICP)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `operator_id` | text | нет |  |  |
| `name` | text | нет |  |  |
| `first_seen` | timestamp with time zone | нет | now() |  |
| `last_seen` | timestamp with time zone | нет | now() |  |

Ограничения:

- PK: `PRIMARY KEY (operator_id)`

### `core.plug_alias`

Отображение названий разъёмов источников на канонические коды. Для автомобилей одно название может давать несколько кодов: вход Type 2 подходит и к розетке, и к кабелю Type 2

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `source` | text | нет |  |  |
| `source_name` | text | нет |  |  |
| `plug_code` | text | нет |  |  |

Ограничения:

- CHECK: `CHECK ((source = ANY (ARRAY['oicp'::text, 'open-ev-data'::text])))`
- FK: `FOREIGN KEY (plug_code) REFERENCES core.plug_type(plug_code) ON UPDATE CASCADE`
- PK: `PRIMARY KEY (source, source_name, plug_code)`

### `core.plug_type`

Канонические типы разъёмов (жёсткое ограничение совместимости)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `plug_code` | text | нет |  |  |
| `name_ru` | text | нет |  |  |
| `current_type` | text | нет |  |  |
| `needs_own_cable` | boolean | нет | false |  |

Ограничения:

- CHECK: `CHECK ((current_type = ANY (ARRAY['AC'::text, 'DC'::text])))`
- PK: `PRIMARY KEY (plug_code)`
- UNIQUE: `UNIQUE (name_ru)`

### `core.postal_code`

Почтовые индексы Швейцарии с кантоном (swisstopo); используется для привязки станций к кантону

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `zip_id` | integer | нет |  |  |
| `postal_code` | character | нет |  |  |
| `locality` | text | нет |  |  |
| `municipality` | text | нет |  |  |
| `canton_code` | character | нет |  |  |
| `address_share` | real | да |  |  |
| `lat` | double precision | нет |  |  |
| `lon` | double precision | нет |  |  |

Ограничения:

- CHECK: `CHECK (((address_share >= (0)::double precision) AND (address_share <= (100)::double precision)))`
- CHECK: `CHECK ((postal_code ~ '^[0-9]{4}$'::text))`
- FK: `FOREIGN KEY (canton_code) REFERENCES core.canton(canton_code)`
- PK: `PRIMARY KEY (zip_id)`

### `core.recommendation_item`

Выдача рекомендателя: станции по рангу с компонентами оценки

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `request_id` | uuid | нет |  |  |
| `rank` | smallint | нет |  |  |
| `station_id` | integer | нет |  |  |
| `distance_km` | numeric | нет |  |  |
| `eta_min` | numeric | нет |  |  |
| `p_free` | numeric | нет |  |  |
| `effective_kw` | numeric | да |  |  |
| `expected_total_min` | numeric | нет |  |  |

Ограничения:

- CHECK: `CHECK (((p_free >= (0)::numeric) AND (p_free <= (1)::numeric)))`
- CHECK: `CHECK ((expected_total_min >= (0)::numeric))`
- CHECK: `CHECK ((effective_kw > (0)::numeric))`
- CHECK: `CHECK ((eta_min >= (0)::numeric))`
- CHECK: `CHECK ((distance_km >= (0)::numeric))`
- CHECK: `CHECK (((rank >= 1) AND (rank <= 20)))`
- FK: `FOREIGN KEY (station_id) REFERENCES core.station(station_id)`
- FK: `FOREIGN KEY (request_id) REFERENCES core.recommendation_request(request_id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (request_id, rank)`
- UNIQUE: `UNIQUE (request_id, station_id)`

### `core.recommendation_request`

Журнал запросов водителей; координаты огрублены до ~1 км (не персональные данные)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `request_id` | uuid | нет |  |  |
| `requested_at` | timestamp with time zone | нет | now() |  |
| `vehicle_id` | integer | нет |  |  |
| `lat_round` | numeric | нет |  |  |
| `lon_round` | numeric | нет |  |  |
| `energy_kwh` | numeric | нет |  |  |
| `filters` | jsonb | нет | '{}'::jsonb |  |
| `status_slot_ts` | timestamp with time zone | да |  |  |
| `model_version` | text | да |  |  |
| `n_results` | integer | нет | 0 |  |

Ограничения:

- CHECK: `CHECK (((energy_kwh > (0)::numeric) AND (energy_kwh <= (150)::numeric)))`
- CHECK: `CHECK (((lat_round >= 45.5) AND (lat_round <= 48.0)))`
- CHECK: `CHECK (((lon_round >= 5.5) AND (lon_round <= 11.0)))`
- CHECK: `CHECK ((n_results >= 0))`
- FK: `FOREIGN KEY (vehicle_id) REFERENCES core.vehicle(vehicle_id)`
- PK: `PRIMARY KEY (request_id)`

### `core.station`

Станция = точки одного оператора по одному адресу в радиусе 30 м (разрешение сущностей)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `station_id` | integer | нет | nextval('core.station_station_id_seq'::regclass) |  |
| `station_key` | character | нет |  |  |
| `name` | text | да |  |  |
| `street` | text | да |  |  |
| `postal_code` | text | да |  |  |
| `city` | text | да |  |  |
| `canton_code` | character | да |  |  |
| `lat` | double precision | нет |  |  |
| `lon` | double precision | нет |  |  |
| `operator_id` | text | нет |  |  |
| `is_open_24h` | boolean | да |  |  |
| `accessibility` | text | да |  |  |
| `first_seen` | timestamp with time zone | нет | now() |  |
| `last_seen` | timestamp with time zone | нет | now() |  |

Ограничения:

- CHECK: `CHECK (((lon >= (5.5)::double precision) AND (lon <= (11.0)::double precision)))`
- CHECK: `CHECK (((lat >= (45.5)::double precision) AND (lat <= (48.0)::double precision)))`
- CHECK: `CHECK ((accessibility = ANY (ARRAY['Free publicly accessible'::text, 'Paying publicly accessible'::text, 'Restricted access'::text, 'Test Station'::text])))`
- FK: `FOREIGN KEY (canton_code) REFERENCES core.canton(canton_code)`
- FK: `FOREIGN KEY (operator_id) REFERENCES core.operator(operator_id)`
- PK: `PRIMARY KEY (station_id)`
- UNIQUE: `UNIQUE (station_key)`

### `core.status_snapshot`

Снимки текущего статуса точек (5-минутные слоты), получаемые при поиске водителем

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `slot_ts` | timestamp with time zone | нет |  |  |
| `evse_id` | text | нет |  |  |
| `status` | text | нет |  |  |
| `run_id` | uuid | нет |  |  |

Ограничения:

- CHECK: `CHECK ((status = ANY (ARRAY['Available'::text, 'Occupied'::text, 'OutOfService'::text, 'Unknown'::text, 'Reserved'::text, 'EvseNotFound'::text])))`
- CHECK: `CHECK (((((date_part('minute'::text, slot_ts))::integer % 5) = 0) AND (date_part('second'::text, slot_ts) = (0)::double precision)))`
- FK: `FOREIGN KEY (evse_id) REFERENCES core.evse(evse_id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (slot_ts, evse_id)`

### `core.vehicle`

Каталог электромобилей: пределы мощности AC/DC (dc_max_kw NULL — без быстрой зарядки)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `vehicle_id` | integer | нет | nextval('core.vehicle_vehicle_id_seq'::regclass) |  |
| `brand` | text | нет |  |  |
| `model` | text | нет |  |  |
| `variant` | text | нет | ''::text |  |
| `release_year` | integer | да |  |  |
| `battery_kwh` | numeric | да |  |  |
| `ac_max_kw` | numeric | нет |  |  |
| `dc_max_kw` | numeric | да |  |  |
| `source` | text | нет |  |  |

Ограничения:

- CHECK: `CHECK (((battery_kwh > (0)::numeric) AND (battery_kwh <= (250)::numeric)))`
- CHECK: `CHECK (((release_year >= 2008) AND (release_year <= 2035)))`
- CHECK: `CHECK (((ac_max_kw > (0)::numeric) AND (ac_max_kw <= (43)::numeric)))`
- CHECK: `CHECK (((dc_max_kw > (0)::numeric) AND (dc_max_kw <= (500)::numeric)))`
- CHECK: `CHECK ((source = ANY (ARRAY['open-ev-data'::text, 'manual'::text])))`
- PK: `PRIMARY KEY (vehicle_id)`
- UNIQUE: `UNIQUE (brand, model, variant, release_year)`

### `core.vehicle_plug`

Разъёмы автомобиля (связь M:N автомобиль — тип разъёма)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `vehicle_id` | integer | нет |  |  |
| `plug_code` | text | нет |  |  |

Ограничения:

- FK: `FOREIGN KEY (plug_code) REFERENCES core.plug_type(plug_code)`
- FK: `FOREIGN KEY (vehicle_id) REFERENCES core.vehicle(vehicle_id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (vehicle_id, plug_code)`

### `core.weather_hourly`

Почасовая погода по кантонам: факт, архивный прогноз, текущий прогноз

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `canton_code` | character | нет |  |  |
| `hour_utc` | timestamp with time zone | нет |  |  |
| `kind` | text | нет |  |  |
| `temperature_c` | real | да |  |  |
| `precipitation_mm` | real | да |  |  |
| `snowfall_cm` | real | да |  |  |
| `wind_kmh` | real | да |  |  |
| `run_id` | uuid | нет |  |  |
| `loaded_at` | timestamp with time zone | нет | now() |  |

Ограничения:

- CHECK: `CHECK ((wind_kmh >= (0)::double precision))`
- CHECK: `CHECK ((precipitation_mm >= (0)::double precision))`
- CHECK: `CHECK (((temperature_c >= ('-45'::integer)::double precision) AND (temperature_c <= (45)::double precision)))`
- CHECK: `CHECK ((kind = ANY (ARRAY['actual'::text, 'hist_forecast'::text, 'forecast'::text])))`
- CHECK: `CHECK ((snowfall_cm >= (0)::double precision))`
- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id)`
- FK: `FOREIGN KEY (canton_code) REFERENCES core.canton(canton_code)`
- PK: `PRIMARY KEY (canton_code, hour_utc, kind)`

### `mart.archive_coverage`

Полнота архива по месяцам: слоты, точки, доля с геопривязкой, доля Unknown

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `month` | date | нет |  |  |
| `n_rows` | bigint | нет |  |  |
| `n_slots` | integer | нет |  |  |
| `expected_slots` | integer | нет |  |  |
| `n_evse` | integer | нет |  |  |
| `share_with_geo` | real | да |  |  |
| `share_unknown` | real | да |  |  |
| `run_id` | uuid | да |  |  |

Ограничения:

- CHECK: `CHECK ((n_rows >= 0))`
- CHECK: `CHECK ((n_slots >= 0))`
- CHECK: `CHECK ((n_evse >= 0))`
- CHECK: `CHECK ((expected_slots > 0))`
- CHECK: `CHECK (((share_unknown >= (0)::double precision) AND (share_unknown <= (1)::double precision)))`
- CHECK: `CHECK (((share_with_geo >= (0)::double precision) AND (share_with_geo <= (1)::double precision)))`
- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (month)`

### `mart.calibration_bin`

Диаграмма надёжности модели доступности (тестовый период)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `model_name` | text | нет |  |  |
| `bin` | smallint | нет |  |  |
| `p_mean` | real | нет |  |  |
| `y_mean` | real | нет |  |  |
| `n` | integer | нет |  |  |

Ограничения:

- CHECK: `CHECK ((n > 0))`
- CHECK: `CHECK (((bin >= 0) AND (bin <= 19)))`
- PK: `PRIMARY KEY (model_name, bin)`

### `mart.evse_profile`

Профиль доступности конкретной точки (только точки, присутствующие в архиве)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `evse_id` | text | нет |  |  |
| `dow` | smallint | нет |  |  |
| `hour_local` | smallint | нет |  |  |
| `p_free` | real | нет |  |  |
| `n_obs` | integer | нет |  |  |

Ограничения:

- CHECK: `CHECK (((hour_local >= 0) AND (hour_local <= 23)))`
- CHECK: `CHECK (((dow >= 0) AND (dow <= 6)))`
- CHECK: `CHECK (((p_free >= (0)::double precision) AND (p_free <= (1)::double precision)))`
- CHECK: `CHECK ((n_obs > 0))`
- PK: `PRIMARY KEY (evse_id, dow, hour_local)`

### `mart.forecast_canton`

Прогнозы показателя A (тест и боевой режим, fold = 0)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `model_name` | text | нет |  |  |
| `fold` | smallint | нет |  |  |
| `origin_utc` | timestamp with time zone | нет |  |  |
| `horizon_h` | smallint | нет |  |  |
| `canton_code` | character | нет |  |  |
| `power_class` | text | нет |  |  |
| `y_pred` | real | нет |  |  |
| `y_true` | real | да |  |  |

Ограничения:

- CHECK: `CHECK (((horizon_h >= 1) AND (horizon_h <= 48)))`
- CHECK: `CHECK ((power_class = ANY (ARRAY['AC'::text, 'DC'::text])))`
- FK: `FOREIGN KEY (canton_code) REFERENCES core.canton(canton_code)`
- PK: `PRIMARY KEY (model_name, fold, origin_utc, horizon_h, canton_code, power_class)`

### `mart.model_metric`

Метрики моделей по фолдам и срезам (горизонт, класс мощности, текущий статус)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `task` | text | нет |  |  |
| `model_name` | text | нет |  |  |
| `fold` | smallint | нет |  |  |
| `metric` | text | нет |  |  |
| `slice` | text | нет | 'all'::text |  |
| `value` | double precision | нет |  |  |
| `computed_at` | timestamp with time zone | нет | now() |  |

Ограничения:

- CHECK: `CHECK ((task = ANY (ARRAY['canton'::text, 'availability'::text, 'recommender'::text])))`
- PK: `PRIMARY KEY (task, model_name, fold, metric, slice)`

### `mart.occupancy_hourly`

Показатель A: почасовая загрузка = Occupied/(Available+Occupied) по кантону и классу мощности

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `canton_code` | character | нет |  |  |
| `power_class` | text | нет |  |  |
| `hour_utc` | timestamp with time zone | нет |  |  |
| `n_evse` | integer | нет |  |  |
| `avg_available` | real | нет |  |  |
| `avg_occupied` | real | нет |  |  |
| `avg_out_of_service` | real | нет |  |  |
| `avg_unknown` | real | нет |  |  |
| `occupancy_rate` | real | да |  |  |
| `oos_rate` | real | да |  |  |
| `slots_observed` | smallint | нет |  |  |
| `source` | text | нет |  |  |

Ограничения:

- CHECK: `CHECK ((avg_available >= (0)::double precision))`
- CHECK: `CHECK ((power_class = ANY (ARRAY['AC'::text, 'DC'::text])))`
- CHECK: `CHECK ((source = ANY (ARRAY['archive'::text, 'live'::text])))`
- CHECK: `CHECK ((avg_occupied >= (0)::double precision))`
- CHECK: `CHECK ((n_evse >= 0))`
- CHECK: `CHECK (((slots_observed >= 0) AND (slots_observed <= 12)))`
- CHECK: `CHECK (((oos_rate >= (0)::double precision) AND (oos_rate <= (1)::double precision)))`
- CHECK: `CHECK (((occupancy_rate >= (0)::double precision) AND (occupancy_rate <= (1)::double precision)))`
- CHECK: `CHECK ((avg_unknown >= (0)::double precision))`
- CHECK: `CHECK ((avg_out_of_service >= (0)::double precision))`
- FK: `FOREIGN KEY (canton_code) REFERENCES core.canton(canton_code)`
- PK: `PRIMARY KEY (canton_code, power_class, hour_utc)`

### `mart.profile_group`

Исторический профиль «доля времени свободна» по кантону, классу, дню недели и часу

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `canton_code` | character | нет |  |  |
| `power_class` | text | нет |  |  |
| `dow` | smallint | нет |  |  |
| `hour_local` | smallint | нет |  |  |
| `p_free` | real | нет |  |  |
| `p_occupied` | real | нет |  |  |
| `n_obs` | bigint | нет |  |  |

Ограничения:

- CHECK: `CHECK ((power_class = ANY (ARRAY['AC'::text, 'DC'::text])))`
- CHECK: `CHECK ((n_obs > 0))`
- CHECK: `CHECK (((p_occupied >= (0)::double precision) AND (p_occupied <= (1)::double precision)))`
- CHECK: `CHECK (((p_free >= (0)::double precision) AND (p_free <= (1)::double precision)))`
- CHECK: `CHECK (((hour_local >= 0) AND (hour_local <= 23)))`
- CHECK: `CHECK (((dow >= 0) AND (dow <= 6)))`
- FK: `FOREIGN KEY (canton_code) REFERENCES core.canton(canton_code)`
- PK: `PRIMARY KEY (canton_code, power_class, dow, hour_local)`

### `ops.dq_check`

Каталог проверок качества (загружается из config/dq_checks.yaml)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `check_id` | text | нет |  |  |
| `layer` | text | нет |  |  |
| `table_name` | text | нет |  |  |
| `check_type` | text | нет |  |  |
| `severity` | text | нет |  |  |
| `description` | text | нет |  |  |
| `sql_text` | text | нет |  |  |

Ограничения:

- CHECK: `CHECK ((severity = ANY (ARRAY['error'::text, 'warn'::text])))`
- CHECK: `CHECK ((layer = ANY (ARRAY['raw'::text, 'stg'::text, 'core'::text, 'mart'::text])))`
- CHECK: `CHECK ((check_type = ANY (ARRAY['completeness'::text, 'uniqueness'::text, 'validity'::text, 'type'::text, 'range'::text, 'referential'::text, 'freshness'::text])))`
- PK: `PRIMARY KEY (check_id)`

### `ops.dq_result`

Результаты проверок качества по запускам

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `result_id` | bigint | нет | nextval('ops.dq_result_result_id_seq'::regclass) |  |
| `check_id` | text | нет |  |  |
| `run_id` | uuid | да |  |  |
| `checked_at` | timestamp with time zone | нет | now() |  |
| `passed` | boolean | нет |  |  |
| `failed_rows` | bigint | нет | 0 |  |
| `sample` | jsonb | да |  |  |

Ограничения:

- CHECK: `CHECK ((failed_rows >= 0))`
- FK: `FOREIGN KEY (check_id) REFERENCES ops.dq_check(check_id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (result_id)`

### `ops.lineage_edge`

Граф происхождения данных: источник → загрузка → слои → витрина → показатель → дашборд

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `from_node` | text | нет |  |  |
| `to_node` | text | нет |  |  |

Ограничения:

- CHECK: `CHECK ((from_node <> to_node))`
- FK: `FOREIGN KEY (from_node) REFERENCES ops.lineage_node(node_id) ON DELETE CASCADE`
- FK: `FOREIGN KEY (to_node) REFERENCES ops.lineage_node(node_id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (from_node, to_node)`

### `ops.lineage_node`



| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `node_id` | text | нет |  |  |
| `node_type` | text | нет |  |  |
| `title` | text | нет |  |  |
| `description` | text | да |  |  |
| `ref` | text | да |  |  |

Ограничения:

- CHECK: `CHECK ((node_type = ANY (ARRAY['source'::text, 'loader'::text, 'raw'::text, 'stg'::text, 'transform'::text, 'core'::text, 'mart'::text, 'model'::text, 'metric'::text, 'dashboard'::text])))`
- PK: `PRIMARY KEY (node_id)`

### `ops.load_run`

Журнал загрузок: одна строка на запуск загрузчика или преобразования

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `run_id` | uuid | нет |  |  |
| `source` | text | нет |  |  |
| `started_at` | timestamp with time zone | нет | now() |  |
| `finished_at` | timestamp with time zone | да |  |  |
| `status` | text | нет | 'running'::text |  |
| `params` | jsonb | нет | '{}'::jsonb |  |
| `http_status` | integer | да |  |  |
| `attempts` | integer | нет | 0 |  |
| `content_hash` | character | да |  |  |
| `raw_path` | text | да |  |  |
| `rows_received` | bigint | нет | 0 |  |
| `rows_written` | bigint | нет | 0 |  |
| `rows_duplicate` | bigint | нет | 0 |  |
| `rows_rejected` | bigint | нет | 0 |  |
| `error_text` | text | да |  |  |

Ограничения:

- CHECK: `CHECK ((rows_duplicate >= 0))`
- CHECK: `CHECK ((attempts >= 0))`
- CHECK: `CHECK ((status = ANY (ARRAY['running'::text, 'success'::text, 'skipped'::text, 'partial'::text, 'failed'::text])))`
- CHECK: `CHECK ((rows_received >= 0))`
- CHECK: `CHECK (((status = 'running'::text) OR (finished_at IS NOT NULL)))`
- CHECK: `CHECK (((finished_at IS NULL) OR (finished_at >= started_at)))`
- CHECK: `CHECK ((rows_rejected >= 0))`
- CHECK: `CHECK ((rows_written >= 0))`
- PK: `PRIMARY KEY (run_id)`

### `ops.model_registry`

Реестр обученных моделей; активна не более одной модели на задачу

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `model_id` | integer | нет | nextval('ops.model_registry_model_id_seq'::regclass) |  |
| `task` | text | нет |  |  |
| `model_name` | text | нет |  |  |
| `version` | text | нет |  |  |
| `trained_at` | timestamp with time zone | нет | now() |  |
| `data_hash` | character | да |  |  |
| `params` | jsonb | нет | '{}'::jsonb |  |
| `metrics` | jsonb | нет | '{}'::jsonb |  |
| `artifact_path` | text | да |  |  |
| `is_active` | boolean | нет | false |  |

Ограничения:

- CHECK: `CHECK ((task = ANY (ARRAY['canton'::text, 'availability'::text])))`
- PK: `PRIMARY KEY (model_id)`
- UNIQUE: `UNIQUE (task, model_name, version)`

### `ops.quarantine`

Записи, не прошедшие проверки и не допущенные в core

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `quarantine_id` | bigint | нет | nextval('ops.quarantine_quarantine_id_seq'::regclass) |  |
| `run_id` | uuid | да |  |  |
| `table_name` | text | нет |  |  |
| `record` | jsonb | нет |  |  |
| `reason` | text | нет |  |  |
| `created_at` | timestamp with time zone | нет | now() |  |

Ограничения:

- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (quarantine_id)`

### `ops.raw_manifest`

Реестр неизменяемых raw-файлов: путь, хэш, запуск, который его создал

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `raw_path` | text | нет |  |  |
| `run_id` | uuid | нет |  |  |
| `source` | text | нет |  |  |
| `sha256` | character | нет |  |  |
| `size_bytes` | bigint | нет |  |  |
| `created_at` | timestamp with time zone | нет | now() |  |

Ограничения:

- CHECK: `CHECK ((size_bytes >= 0))`
- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id) ON DELETE RESTRICT`
- PK: `PRIMARY KEY (raw_path)`

### `ops.schema_version`

Журнал версий схемы БД: какой SQL-файл с какой контрольной суммой и когда применён

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `version_id` | integer | нет | nextval('ops.schema_version_version_id_seq'::regclass) |  |
| `file_name` | text | нет |  |  |
| `checksum` | character | нет |  |  |
| `applied_at` | timestamp with time zone | нет | now() |  |
| `applied_by` | text | нет | CURRENT_USER |  |

Ограничения:

- PK: `PRIMARY KEY (version_id)`
- UNIQUE: `UNIQUE (file_name, checksum)`

### `ops.source_schema`

Реестр структур источников: новая строка = источник изменил набор полей

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `source` | text | нет |  |  |
| `fields_hash` | character | нет |  |  |
| `fields` | jsonb | нет |  |  |
| `first_seen` | timestamp with time zone | нет | now() |  |
| `last_seen` | timestamp with time zone | нет | now() |  |
| `first_run_id` | uuid | да |  |  |

Ограничения:

- FK: `FOREIGN KEY (first_run_id) REFERENCES ops.load_run(run_id)`
- PK: `PRIMARY KEY (source, fields_hash)`

### `stg.archive_details`



| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `run_id` | uuid | нет |  |  |
| `evse_id` | text | да |  |  |
| `lat` | double precision | да |  |  |
| `lon` | double precision | да |  |  |
| `postal_code` | text | да |  |  |
| `power_kw` | numeric | да |  |  |
| `power_type` | text | да |  |  |

Ограничения:

- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id) ON DELETE CASCADE`

### `stg.evse_data`



| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `stg_id` | bigint | нет | nextval('stg.evse_data_stg_id_seq'::regclass) |  |
| `run_id` | uuid | нет |  |  |
| `evse_id` | text | да |  |  |
| `operator_id` | text | да |  |  |
| `operator_name` | text | да |  |  |
| `station_ext_id` | text | да |  |  |
| `pool_ext_id` | text | да |  |  |
| `station_name` | text | да |  |  |
| `street` | text | да |  |  |
| `postal_code` | text | да |  |  |
| `city` | text | да |  |  |
| `country` | text | да |  |  |
| `lat` | double precision | да |  |  |
| `lon` | double precision | да |  |  |
| `accessibility` | text | да |  |  |
| `access_location` | text | да |  |  |
| `is_open_24h` | boolean | да |  |  |
| `plugs` | ARRAY | да |  |  |
| `power_kw` | numeric | да |  |  |
| `power_type` | text | да |  |  |
| `auth_modes` | ARRAY | да |  |  |
| `last_update` | timestamp with time zone | да |  |  |
| `record_hash` | character | нет |  |  |

Ограничения:

- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id) ON DELETE CASCADE`
- PK: `PRIMARY KEY (stg_id)`

### `stg.evse_status`



| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `run_id` | uuid | нет |  |  |
| `slot_ts` | timestamp with time zone | нет |  |  |
| `evse_id` | text | да |  |  |
| `status` | text | да |  |  |
| `operator_id` | text | да |  |  |

Ограничения:

- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id) ON DELETE CASCADE`

### `stg.vehicle`



| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `run_id` | uuid | нет |  |  |
| `source` | text | нет |  |  |
| `ext_id` | text | да |  |  |
| `brand` | text | да |  |  |
| `model` | text | да |  |  |
| `variant` | text | да |  |  |
| `release_year` | integer | да |  |  |
| `battery_kwh` | numeric | да |  |  |
| `ac_max_kw` | numeric | да |  |  |
| `dc_max_kw` | numeric | да |  |  |
| `plugs` | ARRAY | да |  |  |

Ограничения:

- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id) ON DELETE CASCADE`

### `stg.weather_hourly`



| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `run_id` | uuid | нет |  |  |
| `canton_code` | text | нет |  |  |
| `hour_utc` | timestamp with time zone | нет |  |  |
| `kind` | text | нет |  |  |
| `temperature_c` | real | да |  |  |
| `precipitation_mm` | real | да |  |  |
| `snowfall_cm` | real | да |  |  |
| `wind_kmh` | real | да |  |  |

Ограничения:

- FK: `FOREIGN KEY (run_id) REFERENCES ops.load_run(run_id) ON DELETE CASCADE`

## Функции и процедуры

| Объект | Вид | Аргументы | Назначение |
|---|---|---|---|
| `core.fn_compatible_evses` | функция | `p_vehicle_id integer, p_lat double precision, p_lon double precision, p_radius_km double precision, p_has_own_cable boolean` | Совместимые с автомобилем активные точки в радиусе, с эффективной мощностью |
| `core.fn_effective_power_kw` | функция | `p_evse_id text, p_vehicle_id integer` | min(мощность точки, предел автомобиля по AC/DC), кВт |
| `core.fn_haversine_km` | функция | `lat1 double precision, lon1 double precision, lat2 double precision, lon2 double precision` | Расстояние по большому кругу между двумя точками, км |
| `core.fn_resolve_canton` | функция | `p_postal_code text, p_lat double precision, p_lon double precision` | Кантон станции: по почтовому индексу или ближайшему населённому пункту (до ~10 км) |
| `core.fn_station_key` | функция | `p_operator_id text, p_street text, p_postal_code text, p_lat double precision, p_lon double precision` | Ключ разрешения сущности «станция» (правило: одна станция = оператор + адрес) |
| `core.trg_evse_history_fn` | функция | `` |  |
| `core.trg_recommendation_count_fn` | функция | `` |  |
| `core.trg_recommendation_item_check_fn` | функция | `` |  |
| `core.sp_apply_evse_data` | процедура | `IN p_run_id uuid` | stg.evse_data → core: дедупликация, карантин, станции, точки (SCD2), разъёмы |
| `core.sp_apply_status_snapshot` | процедура | `IN p_run_id uuid` | stg.evse_status → core.status_snapshot; неизвестные точки — в карантин |
| `core.sp_apply_vehicles` | процедура | `IN p_run_id uuid` | stg.vehicle → core.vehicle + vehicle_plug; собственный каталог имеет приоритет |
| `core.sp_apply_weather` | процедура | `IN p_run_id uuid` | stg.weather_hourly → core.weather_hourly (upsert), выбросы — в карантин |
| `core.sp_build_archive_evse` | процедура | `IN p_run_id uuid` | Справочник точек архива (кантон, класс мощности) для расчёта витрин |
| `mart.sp_refresh_live_occupancy` | процедура | `IN p_from timestamp with time zone` | core.status_snapshot → mart.occupancy_hourly (source = live) |
| `ops.fn_source_age_hours` | функция | `p_source text` | Часы с последней успешной загрузки источника |
| `ops.trg_load_run_guard_fn` | функция | `` |  |

## Триггеры

| Таблица | Триггер | Момент | События |
|---|---|---|---|
| `core.evse` | `trg_evse_history` | AFTER | INSERT, UPDATE |
| `core.recommendation_item` | `trg_recommendation_count` | AFTER | DELETE, INSERT |
| `core.recommendation_item` | `trg_recommendation_item_check` | BEFORE | INSERT, UPDATE |
| `ops.load_run` | `trg_load_run_guard` | BEFORE | DELETE, UPDATE |

## Версии схемы (последние)

| Файл | Контрольная сумма | Применён |
|---|---|---|
| 04_functions.sql | `bc4474eef785` | 02.10.2026 10:47 |
| 01_create_tables.sql | `fc7df757ca85` | 02.10.2026 10:32 |
| 02_insert_data.sql | `d54a3f56a0fe` | 02.10.2026 10:32 |
| 04_functions.sql | `c16d72b57df8` | 02.10.2026 10:32 |
| 00_schemas.sql | `b199a51eaf9f` | 02.10.2026 10:32 |
| 06_triggers.sql | `03bd45a586c5` | 02.10.2026 10:32 |
| 07_roles.sql | `397deea91c03` | 02.10.2026 10:32 |
| 05_procedures.sql | `45521f882d39` | 02.10.2026 10:32 |
