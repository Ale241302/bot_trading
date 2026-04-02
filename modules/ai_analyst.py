"""
ai_analyst.py
────────────────────────────────────────────
Modulo de inteligencia artificial.
Usa OpenAI para analizar el mercado y decidir:
  BUY  -> comprar
  SELL -> vender
  HOLD -> no hacer nada

La estrategia se lee de: strategy/prompt.md
El contexto dinámico (capital, historial, velas)
se inyecta en el user_message en cada ciclo.
────────────────────────────────────────────
"""

import os
import json
from openai import OpenAI
import pandas as pd


class AIAnalyst:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model  = os.getenv("OPENAI_MODEL", "gpt-4o")

        # Leer prompt desde .md (estrategia estática + reglas)
        prompt_path = os.path.join(
            os.path.dirname(__file__), "..", "strategy", "prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def analyze(
        self,
        symbol: str,
        candles: pd.DataFrame,
        history: list,
        pinecone_context: str = "",
        capital_status: str  = "",
    ) -> dict:
        """
        Envía datos de mercado, historial, contexto vectorial
        y estado de capital a OpenAI.
        Retorna: {"action": "BUY"|"SELL"|"HOLD", "reason": "..."}
        """
        candles_text = candles.tail(50).to_string(index=False)

        # Historial estructurado de Notion
        if history:
            history_text = "\n".join([
                f"- [{op['date']}] {op['type']} {op['symbol']} "
                f"| Resultado: {op['result']} USD | Motivo: {op['reason']}"
                for op in history
            ])
        else:
            history_text = "Sin operaciones previas."

        # Contexto semántico de Pinecone (opcional)
        pinecone_block = ""
        if pinecone_context:
            pinecone_block = f"\n{pinecone_context}\n"

        # Estado de capital del CapitalGuard (opcional)
        capital_block = ""
        if capital_status:
            capital_block = f"\n{capital_status}\n"

        user_message = f"""Simbolo: {symbol}

{capital_block}
Ultimas velas OHLCV (M1, mas reciente al final):
{candles_text}

Historial reciente de operaciones (Notion):
{history_text}
{pinecone_block}
Responde UNICAMENTE con JSON valido:
{{"action": "BUY" | "SELL" | "HOLD", "reason": "explicacion con filtros cumplidos y estado capital"}}
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.2,          # más determinista para trading
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        return json.loads(raw)
