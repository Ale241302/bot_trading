"""
ai_analyst.py
────────────────────────────────────────────
Decisión de trading vía OpenAI.
Recibe el contexto completo ensamblado y retorna
{"action": "BUY"|"SELL"|"HOLD", "reason": "..."}

Arquitectura del prompt:
  system  → strategy/prompt.md  (estático, se carga al init)
  user    → ensamblado dinámico cada ciclo:
              [capital_status]    CapitalGuard
              [market_context]    Finnhub + jblanked
              [candles]           MT5 OHLCV
              [historial Notion]  últimas 10 ops
              [Pinecone context]  memoria vectorial
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

        prompt_path = os.path.join(
            os.path.dirname(__file__), "..", "strategy", "prompt.md"
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def analyze(
        self,
        symbol:           str,
        candles:          pd.DataFrame,
        history:          list,
        open_positions:   list,      # ← nuevo: tickets activos
        pinecone_context: str = "",
        capital_status:   str = "",
        market_context:   str = "",
    ) -> dict:
        """
        Ensambla el user_message con todos los bloques de contexto
        y llama a OpenAI para obtener la decisión.
        """
        candles_text = candles.tail(50).to_string(index=False)

        history_text = "Sin operaciones previas."
        if history:
            history_text = "\n".join([
                f"- [{op['date']}] {op['type']} {op['symbol']} "
                f"| Resultado: {op['result']} USD | Motivo: {op['reason']}"
                for op in history
            ])

        # Construir bloques opcionales (solo si tienen contenido)
        blocks = [f"Símbolo: {symbol}"]

        positions_text = "Sin posiciones abiertas actualmente."
        if open_positions and len(open_positions) > 0:
            pos_lines = []
            for p in open_positions:
                pos_lines.append(
                    f"- Ticket: {p.ticket} | Tipo: {'BUY' if p.type == 0 else 'SELL'} "
                    f"| Volumen: {p.volume} | Open: {p.price_open} | SL: {p.sl} | TP: {p.tp} "
                    f"| Profit Actual: {p.profit}"
                )
            positions_text = "\n".join(pos_lines)
            
        blocks.append(f"Posiciones Abiertas (Tickets Reales):\n{positions_text}")

        if capital_status:
            blocks.append(capital_status)

        if market_context:
            blocks.append(market_context)

        blocks.append(f"Ultimas 50 velas OHLCV (M1, más reciente al final):\n{candles_text}")
        blocks.append(f"Historial de operaciones recientes (Notion):\n{history_text}")

        if pinecone_context:
            blocks.append(pinecone_context)

        blocks.append(
            'Responde UNICAMENTE con JSON válido:\n'
            '{"action": "BUY" | "SELL" | "HOLD", '
            '"reason": "qué filtros ASM se cumplieron, patrón detectado y estado capital"}'
        )

        user_message = "\n\n".join(blocks)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        return json.loads(response.choices[0].message.content)
