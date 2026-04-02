"""
main.py
────────────────────────────────────────────
Punto de entrada del bot de trading.
Inicia la conexión con MT5, carga la estrategia
y arranca el loop principal de análisis.
Ahora registra cada operación tanto en Notion
como en Pinecone (memoria vectorial).
────────────────────────────────────────────
"""

import time
import schedule
from dotenv import load_dotenv
import os

from modules.mt5_connector import MT5Connector
from modules.ai_analyst import AIAnalyst
from modules.notion_logger import NotionLogger
from modules.trader import Trader
from modules.pinecone_memory import PineconeMemory

load_dotenv()

SYMBOL   = os.getenv("TRADING_SYMBOL", "EURUSD")
INTERVAL = int(os.getenv("LOOP_INTERVAL_SECONDS", 60))

def run_bot():
    """Ciclo principal: analiza el mercado y ejecuta operaciones."""
    print("\n⏰ Analizando mercado...")

    market_data = mt5.get_candles(SYMBOL, count=int(os.getenv("CANDLES_HISTORY", 50)))
    if market_data is None:
        print("❌ No se pudieron obtener datos de mercado.")
        return

    # Historial desde Notion (structured) + Pinecone (semantic context)
    history        = notion.get_recent_operations(limit=10)
    pinecone_ctx   = memory.get_stats_context()

    decision = ai.analyze(symbol=SYMBOL, candles=market_data, history=history)
    print(f"🤖 Decisión IA: {decision['action']} | Motivo: {decision['reason']}")

    if decision["action"] in ["BUY", "SELL"]:
        result = trader.execute(symbol=SYMBOL, action=decision["action"])
        if result:
            lot = float(os.getenv("LOT_SIZE", 0.01))

            # 1. Registrar en Notion
            notion.log_operation(
                symbol=SYMBOL,
                action=decision["action"],
                lot_size=lot,
                price_open=result["price"],
                reason=decision["reason"],
            )

            # 2. Registrar en Pinecone como vector
            memory.log_operation(
                symbol=SYMBOL,
                action=decision["action"],
                lot_size=lot,
                price_open=result["price"],
                reason=decision["reason"],
                ticket=result.get("ticket"),
            )

            print(f"✅ Operación guardada en Notion y Pinecone.")
    else:
        print("⏸️  IA decidió no operar en este ciclo.")
        # Consulta semántica opcional: operaciones similares a HOLD
        similar = memory.query_similar(
            f"mercado lateral sin señal clara {SYMBOL}", top_k=3
        )
        if similar:
            print(f"   📌 Operaciones similares en historial: {len(similar)} encontradas")


if __name__ == "__main__":
    print("🚀 Iniciando Trading Bot...")

    mt5     = MT5Connector()
    ai      = AIAnalyst()
    notion  = NotionLogger()
    trader  = Trader(mt5)
    memory  = PineconeMemory()   # ← nueva instancia de memoria vectorial

    if not mt5.connect():
        print("❌ No se pudo conectar a MT5. Abortando.")
        exit(1)

    print(f"✅ Conectado a MT5 | Símbolo: {SYMBOL} | Intervalo: {INTERVAL}s")
    print(f"✅ Pinecone listo  | Índice: trading-operations")

    schedule.every(INTERVAL).seconds.do(run_bot)
    run_bot()

    while True:
        schedule.run_pending()
        time.sleep(1)
