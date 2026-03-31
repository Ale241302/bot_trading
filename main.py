"""
main.py
────────────────────────────────────────────
Punto de entrada del bot de trading.
Inicia la conexión con MT5, carga la estrategia
y arranca el loop principal de análisis.
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

    history = notion.get_recent_operations(limit=10)

    decision = ai.analyze(symbol=SYMBOL, candles=market_data, history=history)
    print(f"🤖 Decisión IA: {decision['action']} | Motivo: {decision['reason']}")

    if decision["action"] in ["BUY", "SELL"]:
        result = trader.execute(symbol=SYMBOL, action=decision["action"])
        if result:
            notion.log_operation(
                symbol=SYMBOL,
                action=decision["action"],
                lot_size=float(os.getenv("LOT_SIZE", 0.01)),
                price_open=result["price"],
                reason=decision["reason"],
            )
    else:
        print("⏸️  IA decidió no operar en este ciclo.")


if __name__ == "__main__":
    print("🚀 Iniciando Trading Bot...")

    mt5     = MT5Connector()
    ai      = AIAnalyst()
    notion  = NotionLogger()
    trader  = Trader(mt5)

    if not mt5.connect():
        print("❌ No se pudo conectar a MT5. Abortando.")
        exit(1)

    print(f"✅ Conectado a MT5 | Símbolo: {SYMBOL} | Intervalo: {INTERVAL}s")

    schedule.every(INTERVAL).seconds.do(run_bot)
    run_bot()

    while True:
        schedule.run_pending()
        time.sleep(1)
