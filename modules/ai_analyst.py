"""
ai_analyst.py
────────────────────────────────────────────
Cliente de IA (OpenAI) que analiza el mercado
y decide la accion a ejecutar.
────────────────────────────────────────────
"""

import json
import logging
import os
import random
import time

from openai import OpenAI, APIConnectionError, APITimeoutError, RateLimitError, APIStatusError

logger = logging.getLogger(__name__)


class AIAnalyst:
    """
    Cliente de IA para decisiones de trading.

    NOTA sobre prompt caching:
        OpenAI cachea automáticamente prompts ≥1024 tokens en gpt-4o, gpt-4o-mini
        y gpt-4-turbo. El descuento es ~50% en input tokens cacheados.
        El system prompt actual (`strategy/prompt.md`) ronda 600-800 tokens y NO
        califica para caching. Para activarlo:
          1) Aumentar el system prompt a >1024 tokens (no recomendado solo por ahorro).
          2) Migrar a Anthropic (cache_control explícito) o a un modelo gpt-4o.1
             que reporte el campo `prompt_tokens_details.cached_tokens` en la respuesta.
        El usage normal devuelve `cached_tokens=0` hoy. Verifica con:
            resp.usage.prompt_tokens_details.cached_tokens
    """

    OPENAI_TIMEOUT       = 60       # segundos por intento
    OPENAI_MAX_ATTEMPTS  = 3
    OPENAI_BASE_DELAY    = 2.0      # 2s → 4s → 8s con jitter

    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=self.OPENAI_TIMEOUT,
        )
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

        prompt_path = os.path.join(os.path.dirname(__file__), "..", "strategy", "prompt.md")
        if not os.path.exists(prompt_path):
            raise FileNotFoundError(
                f"AIAnalyst: no encuentro el system prompt en {prompt_path}. "
                f"Verifica que strategy/prompt.md exista."
            )
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    def _call_openai_with_retry(self, messages: list) -> str | None:
        """
        Llama a OpenAI con retry exponencial + jitter.
        Solo reintenta errores transientes (red, timeout, rate-limit, 5xx).
        Devuelve el contenido raw o None si agotó intentos.
        """
        last_err = None
        for attempt in range(1, self.OPENAI_MAX_ATTEMPTS + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                return response.choices[0].message.content
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                last_err = e
            except APIStatusError as e:
                if 500 <= getattr(e, "status_code", 0) < 600:
                    last_err = e
                else:
                    logger.error(f"OpenAI error fatal ({e.status_code}): {e}")
                    return None
            except Exception as e:
                logger.exception(f"OpenAI excepción no esperada: {e}")
                return None

            if attempt < self.OPENAI_MAX_ATTEMPTS:
                delay = self.OPENAI_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    f"OpenAI intento {attempt}/{self.OPENAI_MAX_ATTEMPTS} falló "
                    f"({type(last_err).__name__}). Reintentando en {delay:.1f}s…"
                )
                time.sleep(delay)

        logger.error(f"OpenAI agotó {self.OPENAI_MAX_ATTEMPTS} intentos: {last_err}")
        return None

    # Acciones válidas que la IA puede devolver. Cualquier otra → HOLD.
    VALID_ACTIONS = {
        "BUY", "SELL", "BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP",
        "CLOSE", "CLOSE_PARTIAL", "MODIFY_SL_TP", "TRAILING_STOP", "HOLD",
    }

    def _validate_decision(self, decision: dict) -> dict:
        """
        Valida y sanea el JSON de la IA. Garantiza:
          - `action` está en VALID_ACTIONS (default HOLD).
          - `lot`, `sl_pips`, `tp_pips` son floats > 0.
          - `reason` siempre es string.
        Si algo no encaja, degrada a HOLD con motivo explícito (no crashea).
        """
        if not isinstance(decision, dict):
            return {"action": "HOLD", "reason": "AI response no es dict"}

        action = str(decision.get("action", "HOLD")).upper().strip()
        if action not in self.VALID_ACTIONS:
            logger.warning(f"AI devolvió action inválida '{action}' → HOLD")
            return {"action": "HOLD", "reason": f"action inválida: {action}"}

        out = {"action": action, "reason": str(decision.get("reason", ""))}
        if action == "HOLD":
            return out

        # Numéricos: validar solo los que apliquen al action.
        for field, default in (("lot", 0.01), ("sl_pips", 8.0), ("tp_pips", 16.0)):
            val = decision.get(field, default)
            try:
                fval = float(val) if val is not None else default
                if fval <= 0:
                    raise ValueError(f"{field} <= 0")
                out[field] = fval
            except (TypeError, ValueError) as e:
                logger.warning(f"AI {field}={val!r} inválido ({e}) → HOLD")
                return {"action": "HOLD", "reason": f"{field} inválido: {val}"}

        # Pasar campos opcionales sin validación adicional.
        for field in ("symbol", "price", "ticket", "phase", "confidence"):
            if field in decision:
                out[field] = decision[field]

        return out

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
        myfxbook_sentiment: dict | None = None,
        phase_context: str | None = None,
    ) -> dict:
        """
        Analiza el mercado y retorna un dict con la decisión de trading.

        - `myfxbook_sentiment` se pasa pre-consultado desde main.py para
          evitar una segunda llamada a Myfxbook por ciclo.
        - `phase_context` (opcional) refuerza al modelo cuál es la fase actual
          y el riesgo/lote sugerido para evitar drift prompt↔código.
        - El JSON devuelto pasa por `_validate_decision` antes de retornar.
        """
        if myfxbook_sentiment and myfxbook_sentiment.get("long_pct") is not None:
            sentiment_text = (
                f"Sentimiento Myfxbook {symbol}: {myfxbook_sentiment['long_pct']}% long / "
                f"{myfxbook_sentiment['short_pct']}% short"
            )
        else:
            sentiment_text = "Sentimiento Myfxbook: no disponible"

        phase_block = (
            f"\n=== FASE Y RIESGO (REGLA INQUEBRANTABLE) ===\n{phase_context}\n"
            if phase_context else ""
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
{phase_block}
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

        raw = self._call_openai_with_retry([
            {"role": "system", "content": self.system_prompt},
            {"role": "user",   "content": user_message},
        ])

        if raw is None:
            return {"action": "HOLD", "reason": "OpenAI no respondió tras reintentos"}

        try:
            decision = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"AI devolvió JSON inválido: {raw}")
            return {"action": "HOLD", "reason": "Error parsing AI response"}

        return self._validate_decision(decision)
