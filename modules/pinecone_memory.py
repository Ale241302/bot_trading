"""
pinecone_memory.py
================================================
Módulo de memoria vectorial con Pinecone.
Guarda cada operación como vector (usando embeddings de OpenAI)
y permite búsqueda semántica eficiente del historial.
================================================
"""

import os
import json
from datetime import datetime, timezone
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec


class PineconeMemory:
    """
    Maneja el almacenamiento y búsqueda vectorial de operaciones
    en Pinecone usando embeddings de OpenAI text-embedding-3-small.
    """

    INDEX_NAME   = "trading-operations"
    DIMENSION    = 1536           # text-embedding-3-small
    METRIC       = "cosine"
    CLOUD        = "aws"
    REGION       = "us-east-1"    # región gratuita de Pinecone Serverless

    def __init__(self):
        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            raise ValueError("PINECONE_API_KEY no está definida en las variables de entorno.")

        self.pc     = Pinecone(api_key=api_key)
        self.openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.index  = self._get_or_create_index()

    # ── Índice ────────────────────────────────────────────────────────────────

    def _get_or_create_index(self):
        """Crea el índice si no existe y retorna la referencia."""
        existing = [i.name for i in self.pc.list_indexes()]
        if self.INDEX_NAME not in existing:
            self.pc.create_index(
                name=self.INDEX_NAME,
                dimension=self.DIMENSION,
                metric=self.METRIC,
                spec=ServerlessSpec(cloud=self.CLOUD, region=self.REGION),
            )
            print(f"Pinecone: índice '{self.INDEX_NAME}' creado.")
        else:
            print(f"Pinecone: usando índice existente '{self.INDEX_NAME}'.")

        return self.pc.Index(self.INDEX_NAME)

    # ── Embeddings ────────────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        """Genera embedding para un texto con text-embedding-3-small."""
        response = self.openai.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding

    def _operation_to_text(self, operation: dict) -> str:
        """
        Convierte los campos de una operación a texto descriptivo
        para generar un embedding semánticamente rico.
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
        Guarda la operación como vector en Pinecone.
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

        text      = self._operation_to_text(operation)
        embedding = self._embed(text)

        # Pinecone solo acepta valores JSON serializables en metadata
        metadata = {
            "symbol":     symbol,
            "action":     action,
            "lot_size":   lot_size,
            "price_open": price_open,
            "reason":     reason[:500],   # límite recomendado en metadata
            "status":     status,
            "date":       now.isoformat(),
        }
        if price_close is not None:
            metadata["price_close"] = price_close
        if result_usd is not None:
            metadata["result_usd"] = result_usd
        if ticket is not None:
            metadata["ticket"] = ticket

        self.index.upsert(vectors=[{"id": vector_id, "values": embedding, "metadata": metadata}])
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
        Búsqueda semántica: dado un texto de consulta devuelve las
        operaciones más similares ordenadas por relevancia.

        Ejemplo:
            memory.query_similar("operaciones BUY en EURUSD con resultado positivo")
            memory.query_similar("SELL GBPUSD", filter_by={"action": {"$eq": "SELL"}})
        """
        embedding = self._embed(query)
        kwargs = {"vector": embedding, "top_k": top_k, "include_metadata": True}
        if filter_by:
            kwargs["filter"] = filter_by

        response = self.index.query(**kwargs)
        results  = []
        for match in response.get("matches", []):
            entry = {"score": round(match["score"], 4), "id": match["id"]}
            entry.update(match.get("metadata", {}))
            results.append(entry)
        return results

    def get_recent_operations(self, limit: int = 10) -> list[dict]:
        """
        Recupera las operaciones más recientes usando búsqueda
        semántica con contexto de trading general.
        """
        return self.query_similar(
            query="operación de trading reciente BUY SELL resultado",
            top_k=limit,
        )

    def get_stats_context(self) -> str:
        """
        Devuelve un resumen textual del historial reciente para
        pasárselo como contexto al modelo de IA.
        """
        ops = self.get_recent_operations(limit=15)
        if not ops:
            return "Sin historial de operaciones en Pinecone."

        lines = ["=== Historial reciente (Pinecone) ==="]
        for op in ops:
            result = f"USD {op.get('result_usd', 'N/A')}" if op.get("result_usd") else "abierta"
            lines.append(
                f"  [{op.get('date', '')[:10]}] {op.get('action','')} {op.get('symbol','')} "
                f"@ {op.get('price_open','')} | {op.get('status','')} | Resultado: {result} "
                f"| Similitud: {op.get('score', '')}"
            )
        return "\n".join(lines)
