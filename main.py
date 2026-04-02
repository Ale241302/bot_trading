"""
main.py
────────────────────────────────────────────
Punto de entrada del bot de trading.

Flujo por ciclo (cada INTERVAL segundos):
  1. CapitalGuard   → ¿puedo operar?
  2. MarketContext  → ¿hay noticia de alto impacto?
  3. MT5            → velas OHLCV
  4. Notion         → historial estructurado reciente
  5. Pinecone       → memoria vectorial semántica
  6. OpenAI         → decisión BUY/SELL/HOLD
  7. MT5            → ejecutar orden
  8. Notion+Pinecone→ registrar operación
────────────────────────────────────────────
"""

import time
import schedule
from dotenv import load_dotenv
import os

from modules.mt5_connector   import MT5Connector
from modules.ai_analyst       import AIAnalyst
from modules.notion_logger    import NotionLogger
from modules.trader           import Trader
from modules.pinecone_memory  import PineconeMemory
from modules.capital_guard    import CapitalGuard
from modules.market_context   import MarketContext

load_dotenv()

SYMBOL   = os.getenv("TRADING_SYMBOL", "EURUSD")
INTERVAL = int(os.getenv("LOOP_INTERVAL_SECONDS", 60))


def run_bot():
    """Ciclo principal de análisis y ejecución."""
    print("\n⏰ Analizando mercado...")

    # ── PASO 1: CapitalGuard ─────────────────────────────────────────────────
    can_trade, guard_reason = capital.should_trade()
    print(f"💰 Capital Guard : {guard_reason}")
    if not can_trade:
        print("⏸️  Capital Guard bloqueó el ciclo.")
        return

    # ── PASO 2: Bloqueo por noticias de alto impacto ─────────────────────────
    hold_news, news_reason = mktctx.should_hold_news()
    if hold_news:
        print(f"🚨 News Block     : {news_reason}")
        print("⏸️  Noticia de alto impacto próxima. No se llama a la IA.")
        return

    # ── PASO 3: Datos de mercado MT5 ─────────────────────────────────────────
    market_data = mt5.get_candles(SYMBOL, count=int(os.getenv("CANDLES_HISTORY", 50)))
    if market_data is None:
        print("❌ No se pudieron obtener datos de mercado.")
        return

    # ── PASO 4-6: Ensamblar contexto completo para la IA ────────────────────
    history        = notion.get_recent_operations(limit=10)
    pinecone_ctx   = memory.get_stats_context()
    capital_status = capital.status_text()
    market_ctx     = mktctx.get_context_text()   # Finnhub + jblanked

    # ── PASO 6: Decisión de la IA ────────────────────────────────────────────
    decision = ai.analyze(
        symbol         = SYMBOL,
        candles        = market_data,
        history        = history,
        pinecone_context = pinecone_ctx,
        capital_status = capital_status,
        market_context = market_ctx,
    )
    print(f"🤖 IA            : {decision['action']} | {decision['reason']}")

    # ── PASO 7-8: Ejecutar y registrar ──────────────────────────────────────
    if decision["action"] in ["BUY", "SELL"]:
        result = trader.execute(symbol=SYMBOL, action=decision["action"])
        if result:
            lot = float(os.getenv("LOT_SIZE", 0.01))

            notion.log_operation(
                symbol=SYMBOL, action=decision["action"],
                lot_size=lot, price_open=result["price"],
                reason=decision["reason"],
            )
            memory.log_operation(
                symbol=SYMBOL, action=decision["action"],
                lot_size=lot, price_open=result["price"],
                reason=decision["reason"], ticket=result.get("ticket"),
            )
            print("✅ Operación ejecutada y registrada en Notion + Pinecone.")
    else:
        print("⏸️  IA decidió HOLD en este ciclo.")


if __name__ == "__main__":
    print("🚀 Iniciando Trading Bot ASM...")
    print(f"   Símbolo  : {SYMBOL}")
    print(f"   Intervalo: {INTERVAL}s")
    print(f"   Objetivos: $9/día | $63/semana | $250/mes")

    mt5     = MT5Connector()
    ai      = AIAnalyst()
    notion  = NotionLogger()
    trader  = Trader(mt5)
    memory  = PineconeMemory()
    capital = CapitalGuard()
    mktctx  = MarketContext()

    if not mt5.connect():
        print("❌ No se pudo conectar a MT5. Abortando.")
        exit(1)

    print(f"✅ MT5 conectado      | {SYMBOL}")
    print(f"✅ Pinecone listo     | {os.getenv('PINECONE_INDEX_NAME', 'bottrading')}")
    print(f"✅ Finnhub listo      | {'configurado' if os.getenv('FINNHUB_API_KEY') else 'SIN KEY ⚠️'}")
    print(f"✅ jblanked News listo| {'configurado' if os.getenv('JBLANKED_API_KEY') else 'SIN KEY ⚠️'}")
    print(f"✅ CapitalGuard listo | $50 → $250 en 30 días")

    schedule.every(INTERVAL).seconds.do(run_bot)
    run_bot()

    while True:
        schedule.run_pending()
        time.sleep(1)
