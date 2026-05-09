"""
notion_logger.py
================================================
Modulo de registro en Notion.
Escribe cada operacion como fila en la DB
y lee el historial para darselo como contexto a la IA.
================================================
"""

import logging
import os
from datetime import datetime, timezone

from notion_client import Client

logger = logging.getLogger(__name__)


def _safe_get(node, path, default=None):
    """
    Recorre `node` siguiendo la lista de claves/índices `path`.
    Devuelve `default` si cualquier acceso falla (KeyError/IndexError/TypeError)
    o si el valor final es None.
    """
    try:
        for p in path:
            node = node[p]
    except (KeyError, IndexError, TypeError):
        return default
    return default if node is None else node


class NotionLogger:
    def __init__(self):
        self.client = Client(auth=os.getenv("NOTION_TOKEN"))
        self.db_id  = os.getenv("NOTION_DB_ID")

    def log_operation(
        self,
        symbol: str,
        action: str,
        lot_size: float,
        price_open: float,
        reason: str,
        price_close: float = None,
        result_usd: float  = None,
        status: str        = "Abierta"
    ):
        now   = datetime.now(timezone.utc).isoformat()
        title = f"{action} {symbol} @ {price_open} | {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        properties = {
            "Operaci\u00f3n":              {"title":     [{"text": {"content": title}}]},
            "Fecha":                   {"date":      {"start": now}},
            "Tipo":                    {"select":    {"name": action}},
            "Par":                     {"rich_text": [{"text": {"content": symbol}}]},
            "Cantidad (Lotes)":        {"number":    lot_size},
            "Precio Entrada":          {"number":    price_open},
            "Motivo / An\u00e1lisis IA": {"rich_text": [{"text": {"content": reason}}]},
            "Estado":                  {"select":    {"name": status}},
        }

        if price_close is not None:
            properties["Precio Cierre"] = {"number": price_close}
        if result_usd is not None:
            properties["Resultado (USD)"] = {"number": result_usd}

        response = self.client.pages.create(
            parent={"database_id": self.db_id},
            properties=properties
        )
        logger.info(f"Notion: operación registrada -> {title}")
        return response.get("id")

    def update_operation(self, page_id: str, price_close: float, result_usd: float):
        if not page_id: return
        properties = {
            "Estado": {"select": {"name": "Cerrada"}},
        }
        if price_close is not None:
            properties["Precio Cierre"] = {"number": price_close}
        if result_usd is not None:
            properties["Resultado (USD)"] = {"number": result_usd}
            
        try:
            self.client.pages.update(page_id=page_id, properties=properties)
            logger.info(f"Notion: operación {page_id} actualizada a Cerrada.")
        except Exception:
            # logger.exception incluye el stacktrace completo (vital para distinguir
            # 404 page-not-found vs 403 token revocado vs 500 transitorio).
            logger.exception(f"Notion error actualizando page_id={page_id}")


    def get_recent_operations(self, limit: int = 10) -> list:
        response = self.client.databases.query(
            database_id=self.db_id,
            sorts=[{"timestamp": "created_time", "direction": "descending"}],
            page_size=limit,
        )

        operations = []
        for page in response.get("results", []):
            props = page["properties"]
            date_start = _safe_get(props, ["Fecha", "date", "start"], default="")
            operations.append({
                "date":   date_start[:10] if date_start else "",
                "type":   _safe_get(props, ["Tipo", "select", "name"], default=""),
                "symbol": _safe_get(props, ["Par", "rich_text", 0, "text", "content"], default=""),
                "result": _safe_get(props, ["Resultado (USD)", "number"], default=0) or 0,
                "reason": _safe_get(props, ["Motivo / An\u00e1lisis IA", "rich_text", 0, "text", "content"], default=""),
            })

        return operations
