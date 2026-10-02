"""Метаданные происхождения: config/lineage.yaml → ops.lineage_node / ops.lineage_edge.

Для страницы «Происхождение показателя» строится цепочка предков выбранного узла
и связывается с фактическими запусками загрузчиков (ops.load_run).
"""

from __future__ import annotations

import yaml

from evadvisor.config import ROOT
from evadvisor.db import connect, session

LOADER_SOURCE = {
    "load.archive": "archive", "load.evse_data": "evse_data", "load.evse_status": "evse_status",
    "load.weather": "weather", "load.vehicles": "vehicles", "load.postal": "postal",
}


def load_graph() -> tuple[list[dict], list[tuple[str, str]]]:
    with open(ROOT / "config" / "lineage.yaml", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    edges = []
    for e in doc["edges"]:
        a, b = (x.strip() for x in e.split("->"))
        edges.append((a, b))
    return doc["nodes"], edges


def sync() -> int:
    nodes, edges = load_graph()
    ids = {n["id"] for n in nodes}
    missing = {x for e in edges for x in e if x not in ids}
    if missing:
        raise ValueError(f"lineage.yaml: рёбра ссылаются на неизвестные узлы: {sorted(missing)}")
    with session("engineer") as conn:
        conn.execute("DELETE FROM ops.lineage_edge")
        conn.execute("DELETE FROM ops.lineage_node")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ops.lineage_node (node_id, node_type, title, ref) VALUES (%s, %s, %s, %s)",
                [(n["id"], n["type"], n["title"], n.get("ref")) for n in nodes])
            cur.executemany("INSERT INTO ops.lineage_edge (from_node, to_node) VALUES (%s, %s)", edges)
    return len(edges)


def upstream(node_id: str, role: str = "engineer") -> tuple[list[dict], list[dict]]:
    """Узлы и рёбра — все предки узла (рекурсивный CTE)."""
    with connect(role) as conn:
        edges = conn.execute(
            """WITH RECURSIVE up(from_node, to_node) AS (
                   SELECT from_node, to_node FROM ops.lineage_edge WHERE to_node = %s
                   UNION
                   SELECT e.from_node, e.to_node FROM ops.lineage_edge e JOIN up ON e.to_node = up.from_node)
               SELECT * FROM up""", (node_id,)).fetchall()
        ids = {node_id} | {e["from_node"] for e in edges}
        nodes = conn.execute("SELECT * FROM ops.lineage_node WHERE node_id = ANY(%s)", (list(ids),)).fetchall()
        runs = {r["source"]: r for r in conn.execute(
            """SELECT DISTINCT ON (source) source, run_id, status, finished_at, raw_path, rows_written
                 FROM ops.load_run WHERE status IN ('success', 'partial')
                ORDER BY source, finished_at DESC""").fetchall()}
    for n in nodes:
        n["last_run"] = runs.get(LOADER_SOURCE.get(n["node_id"], ""))
    return nodes, edges


def mermaid(nodes: list[dict], edges: list[dict]) -> str:
    def nid(x: str) -> str:
        return x.replace(".", "_")

    lines = ["flowchart LR"]
    for n in nodes:
        title = n["title"].replace('"', "'")
        lines.append(f'  {nid(n["node_id"])}["{n["node_type"]}: {title}"]')
    for e in edges:
        lines.append(f"  {nid(e['from_node'])} --> {nid(e['to_node'])}")
    return "\n".join(lines)
