"""
ai_analyst.py
────────────────────────────────────────────
Modulo de inteligencia artificial.
Usa OpenAI para analizar el mercado y decidir:
  BUY  -> comprar
  SELL -> vender
  HOLD -> no hacer nada

La estrategia se configura en: strategy/prompt.txt
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

        prompt_path = os.path.join(os.path.dirname(__file__), "..", "strategy", "prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def analyze(self, symbol: str, candles: pd.DataFrame, history: list) -> dict:
        """
        Envia datos de mercado e historial a OpenAI.
        Retorna: {"action": "BUY"|"SELL"|"HOLD", "reason": "..."}
        """
        candles_text = candles.tail(20).to_string(index=False)

        history_text = "Sin operaciones previas."
        if history:
            history_text = "\n".join([
                f"- [{op['date']}] {op['type']} {op['symbol']} | Resultado: {op['result']} USD | Motivo: {op['reason']}"
                for op in history
            ])

        user_message = f"""
Simbolo: {symbol}

Ultimas velas (OHLCV):
{candles_text}

Historial de operaciones recientes:
{history_text}

Responde UNICAMENTE con un JSON valido:
{{"action": "BUY" | "SELL" | "HOLD", "reason": "explicacion breve"}}
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        raw = response.choices[0].message.content
        return json.loads(raw)
