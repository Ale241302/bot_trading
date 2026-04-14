"""
ai_analyst.py
────────────────────────────────────────────
Cliente de IA (OpenAI) que analiza el mercado
y decide la accion a ejecutar.
────────────────────────────────────────────
"""

import os
import json
from openai import OpenAI

from modules.myfxbook_client import MyfxbookClient

class AIAnalyst:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model  = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.myfxbook = MyfxbookClient()

        prompt_path = os.path.join(os.path.dirname(__file__), "..", "strategy", "prompt.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def analyze(
        self,
        symbol: str,
        candles: dict,
        history: list,
        open_positions: list,
        pinecone_context: str = "",
        capital_status: str = "",
        market_context: str = "",
        pending_orders: list = None,
    ) -> dict:
        """
        Analiza el mercado y retorna un dict con la decision de trading.
        """
        sentiment = self.myfxbook.get_sentiment(symbol)
        sentiment_text = (
            f"Sentimiento Myfxbook {symbol}: {sentiment['long_pct']}% long / "
            f"{sentiment['short_pct']}% short"
            if sentiment else "Sentimiento Myfxbook: no disponible"
        )

        # Serializar velas por timeframe
        candles_text = ""
        for tf, df in candles.items():
            candles_text += f"\n### Velas {tf} (ultimas {len(df)})\n"
            candles_text += df.tail(20).to_string(index=False)
            candles_text += "\n"

        # Serializar posiciones abiertas
        pos_text = "Sin posiciones abiertas."
        if open_positions:
            pos_lines = []
            for p in open_positions:
                pos_lines.append(
                    f"  Ticket={p.ticket} | {('BUY' if p.type == 0 else 'SELL')} | "
                    f"Lote={p.volume} | Abierto en {p.price_open:.5f} | "
                    f"SL={p.sl:.5f} | TP={p.tp:.5f} | PnL=${p.profit:.2f}"
                )
            pos_text = "\n".join(pos_lines)

        # Serializar ordenes pendientes
        pending_text = "Sin ordenes pendientes."
        if pending_orders:
            lines = []
            for o in pending_orders:
                lines.append(
                    f"  Ticket={o['ticket']} | {o['type']} | "
                    f"Precio={o['price']:.5f} | SL={o['sl']:.5f} | TP={o['tp']:.5f} | Lote={o['volume']}"
                )
            pending_text = "\n".join(lines)

        # Historial Notion
        hist_text = "Sin historial previo."
        if history:
            hist_text = "\n".join([
                f"  {op.get('action','?')} {op.get('symbol','?')} @ {op.get('price_open','?')} "
                f"| PnL: {op.get('pnl','pendiente')} | {op.get('date','')}"
                for op in history
            ])

        user_message = f"""
=== ESTADO DEL MERCADO ===
{capital_status}

=== SENTIMIENTO MYFXBOOK ===
{sentiment_text}

=== POSICIONES ABIERTAS (ejecutadas) ===
{pos_text}

=== ORDENES PENDIENTES (no ejecutadas aun) ===
{pending_text}

=== VELAS DE MERCADO ===
{candles_text}

=== HISTORIAL RECIENTE (Notion) ===
{hist_text}

=== CONTEXTO PINECONE ===
{pinecone_context}

=== CONTEXTO DE MERCADO (noticias) ===
{market_context}
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )

        raw = response.choices[0].message.content
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            print(f"Error al parsear JSON de IA: {raw}")
            return {"action": "HOLD", "reason": "Error parsing AI response"}
