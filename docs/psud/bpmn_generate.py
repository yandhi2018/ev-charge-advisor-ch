"""Генерация BPMN 2.0 (с диаграммой DI) процесса «Подбор станции для зарядки» — ПР № 3 ПСУД.

Запуск: python docs/psud/bpmn_generate.py → docs/psud/bpmn_recommendation.bpmn
Открывается в https://demo.bpmn.io или Camunda Modeler (там же экспорт в PNG/SVG для отчёта).
"""

from pathlib import Path
from xml.sax.saxutils import escape

LANES = [("lane_driver", "Водитель", 170), ("lane_app", "Система (веб-приложение, рекомендатель)", 260),
         ("lane_data", "Хранилище и внешние источники", 190)]
COL_W, X0 = 150, 110
# id, тип, подпись, дорожка, колонка
NODES = [
    ("start", "startEvent", "Нужна зарядка", "lane_driver", 0),
    ("t1", "userTask", "Выбрать автомобиль", "lane_driver", 1),
    ("t2", "userTask", "Указать местоположение", "lane_driver", 2),
    ("t3", "userTask", "Отправить запрос с фильтрами", "lane_driver", 3),
    ("t4", "serviceTask", "Проверить возраст снимка статусов", "lane_app", 4),
    ("g1", "exclusiveGateway", "Снимок старше 5 мин?", "lane_app", 5),
    ("t5", "serviceTask", "Получить снимок EVSEStatus", "lane_data", 6),
    ("t6", "serviceTask", "Сохранить снимок (sp_apply_status_snapshot)", "lane_data", 7),
    ("g2", "exclusiveGateway", "", "lane_app", 8),
    ("t7", "serviceTask", "Подобрать совместимые точки (fn_compatible_evses)", "lane_app", 9),
    ("g3", "exclusiveGateway", "Найдено ≥ 5 станций?", "lane_app", 10),
    ("t8", "serviceTask", "Расширить радиус поиска", "lane_app", 10.5),
    ("g4", "exclusiveGateway", "Радиус ≤ 50 км?", "lane_app", 11.5),
    ("t9", "serviceTask", "Рассчитать время в пути", "lane_app", 12),
    ("t10", "serviceTask", "Оценить вероятность свободной точки (модель B)", "lane_app", 13),
    ("t11", "serviceTask", "Ранжировать по ожидаемому времени и сформировать пояснения", "lane_app", 14),
    ("t12", "serviceTask", "Записать журнал запроса", "lane_data", 15),
    ("t13", "userTask", "Просмотреть рекомендации на карте", "lane_driver", 16),
    ("g5", "exclusiveGateway", "Станция подходит?", "lane_driver", 17),
    ("t14", "userTask", "Изменить фильтры", "lane_driver", 17.5),
    ("t15", "userTask", "Открыть маршрут к станции", "lane_driver", 18),
    ("end_ok", "endEvent", "Станция выбрана", "lane_driver", 19),
    ("end_none", "endEvent", "Подходящих станций нет", "lane_app", 12.5),
]
FLOWS = [
    ("start", "t1", ""), ("t1", "t2", ""), ("t2", "t3", ""), ("t3", "t4", ""), ("t4", "g1", ""),
    ("g1", "t5", "да"), ("g1", "g2", "нет"), ("t5", "t6", ""), ("t6", "g2", ""), ("g2", "t7", ""),
    ("t7", "g3", ""), ("g3", "t9", "да"), ("g3", "t8", "нет"), ("t8", "g4", ""), ("g4", "t7", "да"),
    ("g4", "end_none", "нет"), ("t9", "t10", ""), ("t10", "t11", ""), ("t11", "t12", ""), ("t12", "t13", ""),
    ("t13", "g5", ""), ("g5", "t15", "да"), ("g5", "t14", "нет"), ("t14", "t3", ""), ("t15", "end_ok", ""),
]
DATA = [  # объекты данных: id, подпись, колонка, связанная задача, направление
    ("d_vehicle", "core.vehicle", 1, "t1", "in"),
    ("d_snapshot", "core.status_snapshot", 7, "t6", "out"),
    ("d_profile", "mart.evse_profile, weather_hourly", 13, "t10", "in"),
    ("d_log", "core.recommendation_request / item", 15, "t12", "out"),
]
SIZE = {"startEvent": (36, 36), "endEvent": (36, 36), "exclusiveGateway": (50, 50)}


def lane_y(lane):
    y = 0
    for lid, _, h in LANES:
        if lid == lane:
            return y, h
        y += h
    raise KeyError(lane)


def box(n):
    nid, typ, _, lane, col = n
    w, h = SIZE.get(typ, (120, 70))
    ly, lh = lane_y(lane)
    cx = X0 + col * COL_W
    cy = ly + lh / 2 + (45 if nid in ("t8", "g4", "end_none", "t14") else 0)
    return cx - w / 2, cy - h / 2, w, h


def main():
    nodes = {n[0]: n for n in NODES}
    total_h = sum(h for *_, h in LANES)
    width = X0 + 20 * COL_W
    proc, shapes = [], []
    lanes_xml = []
    for lid, name, _ in LANES:
        refs = "".join(f"<bpmn:flowNodeRef>{n[0]}</bpmn:flowNodeRef>" for n in NODES if n[3] == lid)
        lanes_xml.append(f'<bpmn:lane id="{lid}" name="{escape(name)}">{refs}</bpmn:lane>')
        y, h = lane_y(lid)
        shapes.append(f'<bpmndi:BPMNShape id="{lid}_di" bpmnElement="{lid}" isHorizontal="true">'
                      f'<dc:Bounds x="30" y="{y}" width="{width}" height="{h}"/></bpmndi:BPMNShape>')
    incoming, outgoing = {}, {}
    for i, (a, b, _) in enumerate(FLOWS):
        outgoing.setdefault(a, []).append(f"f{i}")
        incoming.setdefault(b, []).append(f"f{i}")
    for nid, typ, label, _, _ in NODES:
        inner = "".join(f"<bpmn:incoming>{f}</bpmn:incoming>" for f in incoming.get(nid, []))
        inner += "".join(f"<bpmn:outgoing>{f}</bpmn:outgoing>" for f in outgoing.get(nid, []))
        assoc = "".join(
            f'<bpmn:dataInputAssociation id="a_{d}"><bpmn:sourceRef>{d}_ref</bpmn:sourceRef></bpmn:dataInputAssociation>'
            if di == "in" else
            f'<bpmn:dataOutputAssociation id="a_{d}"><bpmn:targetRef>{d}_ref</bpmn:targetRef></bpmn:dataOutputAssociation>'
            for d, _, _, t, di in DATA if t == nid)
        proc.append(f'<bpmn:{typ} id="{nid}" name="{escape(label)}">{inner}{assoc}</bpmn:{typ}>')
        x, y, w, h = box(nodes[nid])
        shapes.append(f'<bpmndi:BPMNShape id="{nid}_di" bpmnElement="{nid}"'
                      f'{" isMarkerVisible=\"true\"" if typ == "exclusiveGateway" else ""}>'
                      f'<dc:Bounds x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}"/></bpmndi:BPMNShape>')
    for i, (a, b, label) in enumerate(FLOWS):
        name = f' name="{escape(label)}"' if label else ""
        proc.append(f'<bpmn:sequenceFlow id="f{i}" sourceRef="{a}" targetRef="{b}"{name}/>')
        ax, ay, aw, ah = box(nodes[a])
        bx, by, bw, bh = box(nodes[b])
        if bx > ax:      # вперёд: из правого края в левый, с изломом по вертикали
            p1, p4 = (ax + aw, ay + ah / 2), (bx, by + bh / 2)
            mid = (p1[0] + p4[0]) / 2
            pts = [p1, (mid, p1[1]), (mid, p4[1]), p4]
        else:            # возврат: снизу/сверху в обход
            p1, p4 = (ax + aw / 2, ay + ah), (bx + bw / 2, by + bh)
            low = max(p1[1], p4[1]) + 25
            pts = [p1, (p1[0], low), (p4[0], low), p4]
        wps = "".join(f'<di:waypoint x="{x:.0f}" y="{y:.0f}"/>' for x, y in pts)
        shapes.append(f'<bpmndi:BPMNEdge id="f{i}_di" bpmnElement="f{i}">{wps}</bpmndi:BPMNEdge>')
    for d, label, col, task, direction in DATA:
        proc.append(f'<bpmn:dataObjectReference id="{d}_ref" name="{escape(label)}" dataObjectRef="{d}"/>'
                    f'<bpmn:dataObject id="{d}"/>')
        tx, ty, tw, th = box(nodes[task])
        x, y = X0 + col * COL_W - 18, total_h - 70 if nodes[task][3] != "lane_data" else ty + th + 15
        if nodes[task][3] == "lane_driver":
            y = ty - 65 if ty - 65 > 0 else ty + th + 10
        shapes.append(f'<bpmndi:BPMNShape id="{d}_di" bpmnElement="{d}_ref"><dc:Bounds x="{x:.0f}" y="{y:.0f}" '
                      f'width="36" height="50"/></bpmndi:BPMNShape>')
        a, b = ((x + 18, y + (50 if y < ty else 0)), (tx + tw / 2, ty if y < ty else ty + th))
        if direction == "out":
            a, b = b, a
        shapes.append(f'<bpmndi:BPMNEdge id="a_{d}_di" bpmnElement="a_{d}"><di:waypoint x="{a[0]:.0f}" y="{a[1]:.0f}"/>'
                      f'<di:waypoint x="{b[0]:.0f}" y="{b[1]:.0f}"/></bpmndi:BPMNEdge>')
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="defs" targetNamespace="https://github.com/yandhi2018/ev-charge-advisor-ch">
<bpmn:collaboration id="collab"><bpmn:participant id="pool" name="EV Charge Advisor CH: подбор станции для зарядки" processRef="proc"/></bpmn:collaboration>
<bpmn:process id="proc" isExecutable="false"><bpmn:laneSet id="lanes">{"".join(lanes_xml)}</bpmn:laneSet>
{chr(10).join(proc)}
</bpmn:process>
<bpmndi:BPMNDiagram id="diagram"><bpmndi:BPMNPlane id="plane" bpmnElement="collab">
<bpmndi:BPMNShape id="pool_di" bpmnElement="pool" isHorizontal="true"><dc:Bounds x="0" y="0" width="{width + 30}" height="{total_h}"/></bpmndi:BPMNShape>
{chr(10).join(shapes)}
</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
</bpmn:definitions>
'''
    out = Path(__file__).with_name("bpmn_recommendation.bpmn")
    out.write_text(xml, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
