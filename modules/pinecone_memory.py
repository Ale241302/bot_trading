"""
pinecone_memory.py
================================================
Módulo de memoria vectorial con Pinecone.
Usa el índice con embedding INTEGRADO (llama-text-embed-v2 / NVIDIA)
de manera que Pinecone convierte el texto a vector automáticamente.
No se necesita OpenAI ni llamadas externas para embeddings.

NOTA: El reranker bge-reranker-v2-m3 fue desactivado porque el plan
gratuito de Pinecone tiene un límite de 500 requests/mes. La búsqueda
semántica por similitud coseno es suficiente para el bot.
================================================
"""

import os
from datetime import datetime, timezone
from pinecone import Pinecone


class PineconeMemory:
    """
    Almacenamiento y búsqueda vectorial de operaciones de trading.

    El índice debe estar creado en Pinecone con:
      - Modelo integrado: llama-text-embed-v2  (NVIDIA Hosted)
      - Tipo: Dense
      - Modo: Serverless
      - Field map: el campo "text" es el que Pinecone embebe automáticamente

    Al hacer upsert se envía el texto plano; Pinecone genera el vector.
    Al hacer query también se envía texto plano.
    """

    def __init__(self):
        api_key    = os.getenv("PINECONE_API_KEY")
        index_name = os.getenv("PINECONE_INDEX_NAME", "trading-operations")

        if not api_key:
            raise ValueError("PINECONE_API_KEY no está definida en las variables de entorno.")

        self.pc    = Pinecone(api_key=api_key)
        self.index = self.pc.Index(index_name)
        print(f"Pinecone: conectado al índice '{index_name}' (llama-text-embed-v2).")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _operation_to_text(self, operation: dict) -> str:
        """
        Convierte los campos de una operación a un texto descriptivo
        que será embebido automáticamente por Pinecone.
        """
        parts = [
            f"Operación: {operation.get('action', '')} {operation.get('symbol', '')}",
            f"Precio entrada: {operation.get('price_open', '')}",
            f"Lotes: {operation.get('lot_size', '')}",
            f"Estado: {operation.get('status', 'Abierta')}",
            f"Motivo: {operation.get('reason', '')}",
        ]
        if operation.get("price_close") is not None:
            parts.append(f"Precio cierre: {operation['price_close']}")
        if operation.get("result_usd") is not None:
            parts.append(f"Resultado USD: {operation['result_usd']}")
        if operation.get("date"):
            parts.append(f"Fecha: {operation['date']}")
        return " | ".join(parts)

    # ── Escritura ─────────────────────────────────────────────────────────────

    def log_operation(
        self,
        symbol: str,
        action: str,
        lot_size: float,
        price_open: float,
        reason: str,
        price_close: float = None,
        result_usd: float  = None,
        status: str        = "Abierta",
        ticket: int        = None,
    ) -> str:
        """
        Guarda la operación en Pinecone con embedding integrado.
        El campo 'text' es el que llama-text-embed-v2 convierte a vector.
        Retorna el vector_id generado.
        """
        now = datetime.now(timezone.utc)
        vector_id = f"{symbol}_{action}_{now.strftime('%Y%m%d_%H%M%S')}"
        if ticket:
            vector_id = f"ticket_{ticket}"

        operation = {
            "symbol":      symbol,
            "action":      action,
            "lot_size":    lot_size,
            "price_open":  price_open,
            "reason":      reason,
            "price_close": price_close,
            "result_usd":  result_usd,
            "status":      status,
            "ticket":      ticket,
            "date":        now.isoformat(),
        }

        # Texto que será embebido automáticamente por Pinecone
        text = self._operation_to_text(operation)

        # Metadata: valores planos que se devuelven en los resultados
        metadata = {
            "symbol":     symbol,
            "action":     action,
            "lot_size":   lot_size,
            "price_open": price_open,
            "reason":     reason[:500],
            "status":     status,
            "date":       now.isoformat(),
        }
        if price_close is not None:
            metadata["price_close"] = price_close
        if result_usd is not None:
            metadata["result_usd"] = result_usd
        if ticket is not None:
            metadata["ticket"] = ticket

        # Con embedding integrado: upsert recibe 'text' (no 'values')
        self.index.upsert_records(
            namespace="operations",
            records=[
                {
                    "id":       vector_id,
                    "text":     text,       # ← campo que Pinecone embebe
                    **metadata,
                }
            ],
        )
        print(f"Pinecone: operación guardada -> {vector_id}")
        return vector_id

    # ── Lectura ───────────────────────────────────────────────────────────────

    def query_similar(
        self,
        query: str,
        top_k: int = 5,
        filter_by: dict = None,
    ) -> list[dict]:
        """
        Búsqueda semántica con texto plano (similitud coseno).
        Pinecone embebe el query automáticamente.
        Reranker desactivado: límite gratuito 500 req/mes agotado fácilmente.
        """
        kwargs = {
            "namespace": "operations",
            "query":     {"top_k": top_k, "inputs": {"text": query}},
            "fields":    ["symbol", "action", "lot_size", "price_open",
                          "price_close", "result_usd", "status", "date",
                          "reason", "ticket"],
        }
        if filter_by:
            kwargs["query"]["filter"] = filter_by

        response = self.index.search(**kwargs)
        results  = []
        for hit in response.get("result", {}).get("hits", []):
            entry = {
                "score": round(hit.get("_score", 0), 4),
                "id":    hit.get("_id"),
            }
            entry.update(hit.get("fields", {}))
            results.append(entry)
        return results

    def get_operations_by_symbol(self, symbol: str, limit: int = 10) -> list[dict]:
        """
        Recupera operaciones recientes filtrando por símbolo exacto.
        """
        return self.query_similar(
            query=f"operación de trading reciente BUY SELL resultado precio {symbol}",
            top_k=limit,
            filter_by={"symbol": {"$eq": symbol}}
        )

    def get_stats_context(self, symbol: str) -> str:
        """
        Devuelve un resumen textual del historial para pasárselo
        como contexto al modelo de IA.
        """
        ops = self.get_operations_by_symbol(symbol=symbol, limit=15)
        if not ops:
            return "Sin historial de operaciones en Pinecone."

        lines = ["=== Historial reciente (Pinecone / llama-text-embed-v2) ==="]
        for op in ops:
            result_str = f"USD {op['result_usd']}" if op.get("result_usd") else "abierta"
            lines.append(
                f"  [{str(op.get('date', ''))[:10]}] {op.get('action','')} "
                f"{op.get('symbol','')} @ {op.get('price_open','')} "
                f"| {op.get('status','')} | Resultado: {result_str} "
                f"| Score: {op.get('score', '')}"
            )
        return "\n".join(lines)

    def update_operation(self, ticket: int, symbol: str, action: str, lot_size: float, price_open: float, reason: str, price_close: float, result_usd: float):
        """
        Actualiza los datos de resolución enviando la misma operación a memoria.
        El id está basado en el ticket y Pinecone lo actualiza encima.
        """
        self.log_operation(
            symbol=symbol,
            action=action,
            lot_size=lot_size,
            price_open=price_open,
            reason=reason,
            price_close=price_close,
            result_usd=result_usd,
            status="Cerrada",
            ticket=ticket
        )
