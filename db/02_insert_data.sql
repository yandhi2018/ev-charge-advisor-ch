-- 02_insert_data.sql — справочные данные, необходимые до первой загрузки.
-- Основное наполнение (станции, точки, погода, автомобили) выполняют загрузчики
-- из открытых источников; см. README, раздел «Запуск».
-- Скрипт идемпотентен: ON CONFLICT DO UPDATE.

-- Кантоны: код, название, административный центр (опорная точка погоды)
INSERT INTO core.canton (canton_code, name, lat, lon) VALUES
    ('ZH', 'Zürich',               47.3769, 8.5417),
    ('BE', 'Bern',                 46.9480, 7.4474),
    ('LU', 'Luzern',               47.0502, 8.3093),
    ('UR', 'Uri',                  46.8802, 8.6437),
    ('SZ', 'Schwyz',               47.0207, 8.6524),
    ('OW', 'Obwalden',             46.8965, 8.2458),
    ('NW', 'Nidwalden',            46.9581, 8.3654),
    ('GL', 'Glarus',               47.0404, 9.0680),
    ('ZG', 'Zug',                  47.1662, 8.5155),
    ('FR', 'Fribourg',             46.8065, 7.1619),
    ('SO', 'Solothurn',            47.2088, 7.5323),
    ('BS', 'Basel-Stadt',          47.5596, 7.5886),
    ('BL', 'Basel-Landschaft',     47.4838, 7.7357),
    ('SH', 'Schaffhausen',         47.6970, 8.6349),
    ('AR', 'Appenzell Ausserrhoden', 47.3854, 9.2792),
    ('AI', 'Appenzell Innerrhoden',  47.3311, 9.4090),
    ('SG', 'St. Gallen',           47.4245, 9.3767),
    ('GR', 'Graubünden',           46.8499, 9.5330),
    ('AG', 'Aargau',               47.3925, 8.0442),
    ('TG', 'Thurgau',              47.5590, 8.8992),
    ('TI', 'Ticino',               46.1944, 9.0175),
    ('VD', 'Vaud',                 46.5197, 6.6323),
    ('VS', 'Valais',               46.2331, 7.3606),
    ('NE', 'Neuchâtel',            46.9900, 6.9293),
    ('GE', 'Genève',               46.2044, 6.1432),
    ('JU', 'Jura',                 47.3659, 7.3459)
ON CONFLICT (canton_code) DO UPDATE
    SET name = EXCLUDED.name, lat = EXCLUDED.lat, lon = EXCLUDED.lon;

-- Канонические типы разъёмов станции
INSERT INTO core.plug_type (plug_code, name_ru, current_type, needs_own_cable) VALUES
    ('TYPE2_SOCKET', 'Type 2 (розетка, нужен свой кабель)', 'AC', true),
    ('TYPE2_CABLE',  'Type 2 (с кабелем)',                  'AC', false),
    ('TYPE1_CABLE',  'Type 1 (с кабелем)',                  'AC', false),
    ('CCS2',         'CCS Combo 2',                         'DC', false),
    ('CCS1',         'CCS Combo 1',                         'DC', false),
    ('CHADEMO',      'CHAdeMO',                             'DC', false),
    ('TESLA',        'Tesla Supercharger',                  'DC', false),
    ('DOMESTIC_J',   'Бытовая розетка Type J (T13)',        'AC', true),
    ('DOMESTIC_G',   'Бытовая розетка Type G',              'AC', true)
ON CONFLICT (plug_code) DO UPDATE
    SET name_ru = EXCLUDED.name_ru, current_type = EXCLUDED.current_type,
        needs_own_cable = EXCLUDED.needs_own_cable;

-- Названия разъёмов в источниках → канонические коды
INSERT INTO core.plug_alias (source, source_name, plug_code) VALUES
    -- станции (OICP, ich-tanke-strom.ch)
    ('oicp', 'Type 2 Outlet',                      'TYPE2_SOCKET'),
    ('oicp', 'Type 2 Connector (Cable Attached)',  'TYPE2_CABLE'),
    ('oicp', 'Type 1 Connector (Cable Attached)',  'TYPE1_CABLE'),
    ('oicp', 'CCS Combo 2 Plug (Cable Attached)',  'CCS2'),
    ('oicp', 'CCS Combo 1 Plug (Cable Attached)',  'CCS1'),
    ('oicp', 'CHAdeMO',                            'CHADEMO'),
    ('oicp', 'Tesla Connector',                    'TESLA'),
    ('oicp', 'Type J Swiss Standard',              'DOMESTIC_J'),
    ('oicp', 'Type G British Standard',            'DOMESTIC_G'),
    -- автомобили (Open EV Data): вход автомобиля → разъёмы станций, которыми он может пользоваться
    ('open-ev-data', 'type2',     'TYPE2_SOCKET'),
    ('open-ev-data', 'type2',     'TYPE2_CABLE'),
    ('open-ev-data', 'type1',     'TYPE1_CABLE'),
    ('open-ev-data', 'ccs',       'CCS2'),
    ('open-ev-data', 'chademo',   'CHADEMO'),
    ('open-ev-data', 'tesla_suc', 'TESLA'),
    ('open-ev-data', 'tesla_ccs', 'TESLA'),
    ('open-ev-data', 'tesla_ccs', 'CCS2')
ON CONFLICT DO NOTHING;
