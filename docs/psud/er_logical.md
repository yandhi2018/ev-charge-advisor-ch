# ПР № 5. Логическая модель данных

Модель core и ops, приведённая к 3НФ. Связи M:N «точка — тип разъёма» и «автомобиль — тип разъёма» разрешены ассоциативными сущностями `EVSE_PLUG` и `VEHICLE_PLUG`. Полное описание атрибутов и ограничений сгенерировано из физической БД: [data_dictionary.md](../data_dictionary.md).

```mermaid
erDiagram
    CANTON ||--o{ POSTAL_CODE : "содержит"
    CANTON |o--o{ STATION : "расположена в"
    OPERATOR ||--o{ STATION : "обслуживает"
    OPERATOR ||--o{ EVSE : "владеет"
    STATION ||--|{ EVSE : "состоит из"
    EVSE ||--|{ EVSE_HISTORY : "версии"
    EVSE ||--|{ EVSE_PLUG : ""
    PLUG_TYPE ||--o{ EVSE_PLUG : ""
    PLUG_TYPE ||--o{ PLUG_ALIAS : "названия в источниках"
    VEHICLE ||--|{ VEHICLE_PLUG : ""
    PLUG_TYPE ||--o{ VEHICLE_PLUG : ""
    EVSE ||--o{ STATUS_SNAPSHOT : "статусы"
    CANTON ||--o{ WEATHER_HOURLY : "погода"
    VEHICLE ||--o{ RECOMMENDATION_REQUEST : "для"
    RECOMMENDATION_REQUEST ||--o{ RECOMMENDATION_ITEM : "выдача"
    STATION ||--o{ RECOMMENDATION_ITEM : "рекомендована"
    LOAD_RUN ||--o{ STATUS_SNAPSHOT : "загрузил"
    LOAD_RUN ||--o{ WEATHER_HOURLY : "загрузил"
    LOAD_RUN ||--o{ RAW_MANIFEST : "создал файл"
    LOAD_RUN ||--o{ DQ_RESULT : "проверка в запуске"
    DQ_CHECK ||--o{ DQ_RESULT : "результаты"

    CANTON {
        char2 canton_code PK
        string name UK
        double lat
        double lon
    }
    POSTAL_CODE {
        int zip_id PK
        char4 postal_code
        string locality
        char2 canton_code FK
    }
    OPERATOR {
        string operator_id PK
        string name
    }
    STATION {
        int station_id PK
        char32 station_key UK
        string street
        string postal_code
        char2 canton_code FK
        string operator_id FK
        double lat
        double lon
    }
    EVSE {
        string evse_id PK
        int station_id FK
        string operator_id FK
        decimal power_kw
        string power_type
        bool is_active
        char64 row_hash
    }
    EVSE_HISTORY {
        bigint history_id PK
        string evse_id FK
        timestamp valid_from
        timestamp valid_to
        string change_type
    }
    PLUG_TYPE {
        string plug_code PK
        string name_ru UK
        string current_type
        bool needs_own_cable
    }
    EVSE_PLUG {
        string evse_id PK, FK
        string plug_code PK, FK
    }
    PLUG_ALIAS {
        string source PK
        string source_name PK
        string plug_code PK, FK
    }
    VEHICLE {
        int vehicle_id PK
        string brand
        string model
        string variant
        int release_year
        decimal ac_max_kw
        decimal dc_max_kw
    }
    VEHICLE_PLUG {
        int vehicle_id PK, FK
        string plug_code PK, FK
    }
    STATUS_SNAPSHOT {
        timestamp slot_ts PK
        string evse_id PK, FK
        string status
        uuid run_id FK
    }
    WEATHER_HOURLY {
        char2 canton_code PK, FK
        timestamp hour_utc PK
        string kind PK
        real temperature_c
        uuid run_id FK
    }
    RECOMMENDATION_REQUEST {
        uuid request_id PK
        int vehicle_id FK
        decimal lat_round
        decimal lon_round
        decimal energy_kwh
    }
    RECOMMENDATION_ITEM {
        uuid request_id PK, FK
        int rank PK
        int station_id FK
        decimal p_free
        decimal expected_total_min
    }
    LOAD_RUN {
        uuid run_id PK
        string source
        string status
        timestamp started_at
    }
    RAW_MANIFEST {
        string raw_path PK
        uuid run_id FK
        char64 sha256
    }
    DQ_CHECK {
        string check_id PK
        string check_type
        string severity
    }
    DQ_RESULT {
        bigint result_id PK
        string check_id FK
        uuid run_id FK
        bool passed
    }
```

## Нормализация

- **1НФ:** атрибуты атомарны. Исключение — массив `filters` (jsonb) в журнале запросов: это неразбираемый снимок параметров запроса, по нему не выполняется поиск.
- **2НФ:** в таблицах с составным ключом (`evse_plug`, `vehicle_plug`, `status_snapshot`, `weather_hourly`) неключевые атрибуты зависят от всего ключа.
- **3НФ:** транзитивных зависимостей нет. Кантон станции хранится в `station`, а не в `evse`; оператор точки — в `evse` и `station`, потому что у точки может быть иной оператор, чем у станции (роуминговые операторы). Это обоснованное отступление от строгой 3НФ.
- **Обоснованная денормализация:** `evse.power_class` — вычисляемая колонка (GENERATED) от `power_type`, нужна для индексации и фильтров; витрины `mart.*` денормализованы намеренно под потребителей.
