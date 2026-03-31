"""
notion_logger.py
================================================
Modulo de registro en Notion.
Escribe cada operacion como fila en la DB
y lee el historial para darselo como contexto a la IA.
================================================
"""

import os
from datetime import datetime, timezone
from notion_client import Client

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

        self.client.pages.create(
            parent={"database_id": self.db_id},
            properties=properties
        )
        print(f"Notion: operacion registrada -> {title}")

    def get_recent_operations(self, limit: int = 10) -> list:
        response = self.client.databases.query(
            database_id=self.db_id,
            sorts=[{"timestamp": "created_time", "direction": "descending"}],
            page_size=limit
        )

        operations = []
        for page in response.get("results", []):
            props = page["properties"]
            try:
                operations.append({
                    "date":   props["Fecha"]["date"]["start"][:10]                                       if props["Fecha"]["date"] else "",
                    "type":   props["Tipo"]["select"]["name"]                                            if props["Tipo"]["select"] else "",
                    "symbol": props["Par"]["rich_text"][0]["text"]["content"]                            if props["Par"]["rich_text"] else "",
                    "result": props["Resultado (USD)"]["number"]                                         if props["Resultado (USD)"]["number"] is not None else 0,
                    "reason": props["Motivo / An\u00e1lisis IA"]["rich_text"][0]["text"]["content"]        if props["Motivo / An\u00e1lisis IA"]["rich_text"] else ""
                })
            except Exception:
                continue

        return operations
