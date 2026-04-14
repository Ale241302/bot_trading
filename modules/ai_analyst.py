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
        candles:          dict,       # ← ahora recibe dict con M15, H1, H4
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
        # Convertir a cadena solo 20 velas de cada timeframe para ahorrar tokens
        candles_text = ""
        if "M15" in candles and candles["M15"] is not None:
            candles_text += "--- Velas M15 ---\n" + candles["M15"].tail(20).to_string(index=False) + "\n\n"
        if "H1" in candles and candles["H1"] is not None:
            candles_text += "--- Velas H1 ---\n" + candles["H1"].tail(20).to_string(index=False) + "\n\n"
        if "H4" in candles and candles["H4"] is not None:
            candles_text += "--- Velas H4 ---\n" + candles["H4"].tail(20).to_string(index=False) + "\n"

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

        blocks.append(f"Velas OHLCV Multi-timeframe (más reciente al final):\n{candles_text}")
        blocks.append(f"Historial de operaciones recientes (Notion):\n{history_text}")

        if pinecone_context:
            blocks.append(pinecone_context)

        blocks.append(
            'Responde UNICAMENTE con JSON válido:\n'
            '{"action": "BUY|SELL|BUY_LIMIT|SELL_LIMIT|BUY_STOP|SELL_STOP|CLOSE|CLOSE_PARTIAL|MODIFY_SL_TP|TRAILING_STOP|HOLD", '
            '"symbol": "EURUSD", "lot": 0.01, "sl_pips": 15, "tp_pips": 30, '
            '"price": null, "ticket": null, "reason": "...", "confidence": 85, "phase": "CRECIMIENTO|CONSOLIDACION|ESCUDO"}'
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
