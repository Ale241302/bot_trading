"""
main.py
────────────────────────────────────────────
Punto de entrada del bot de trading.
Loop principal: analiza el mercado cada INTERVAL
segundos y ejecuta operaciones según la IA.

Integraciones activas:
  - MT5     : broker (solo Windows)
  - OpenAI  : decisión (BUY/SELL/HOLD)
  - Notion  : log estructurado
  - Pinecone: memoria vectorial semántica
  - CapitalGuard: protección de ganancias
────────────────────────────────────────────
"""

import time
import schedule
from dotenv import load_dotenv
import os

from modules.mt5_connector import MT5Connector
from modules.ai_analyst    import AIAnalyst
from modules.notion_logger  import NotionLogger
from modules.trader         import Trader
from modules.pinecone_memory import PineconeMemory
from modules.capital_guard  import CapitalGuard

load_dotenv()

SYMBOL   = os.getenv("TRADING_SYMBOL", "EURUSD")
INTERVAL = int(os.getenv("LOOP_INTERVAL_SECONDS", 60))


def run_bot():
    """Ciclo principal de análisis y ejecución."""
    print("\n⏰ Analizando mercado...")

    # 1. Verificar si el CapitalGuard permite operar ANTES de llamar a la IA
    can_trade, guard_reason = capital.should_trade()
    print(f"💰 Capital Guard: {guard_reason}")
    if not can_trade:
        print("⏸️  Capital Guard bloqueó el ciclo. No se llama a la IA.")
        return

    # 2. Datos de mercado
    market_data = mt5.get_candles(SYMBOL, count=int(os.getenv("CANDLES_HISTORY", 50)))
    if market_data is None:
        print("❌ No se pudieron obtener datos de mercado.")
        return

    # 3. Contextos para la IA
    history        = notion.get_recent_operations(limit=10)
    pinecone_ctx   = memory.get_stats_context()
    capital_status = capital.status_text()

    # 4. Decisión de la IA
    decision = ai.analyze(
        symbol=SYMBOL,
        candles=market_data,
        history=history,
        pinecone_context=pinecone_ctx,
        capital_status=capital_status,
    )
    print(f"🤖 Decisión IA: {decision['action']} | Motivo: {decision['reason']}")

    # 5. Ejecutar si es BUY o SELL
    if decision["action"] in ["BUY", "SELL"]:
        result = trader.execute(symbol=SYMBOL, action=decision["action"])
        if result:
            lot = float(os.getenv("LOT_SIZE", 0.01))

            # Registrar en Notion
            notion.log_operation(
                symbol=SYMBOL,
                action=decision["action"],
                lot_size=lot,
                price_open=result["price"],
                reason=decision["reason"],
            )

            # Registrar en Pinecone
            memory.log_operation(
                symbol=SYMBOL,
                action=decision["action"],
                lot_size=lot,
                price_open=result["price"],
                reason=decision["reason"],
                ticket=result.get("ticket"),
            )

            # Simular P&L estimado para el CapitalGuard
            # (en producción debes cerrar la orden y calcular el P&L real)
            # Con 0.01 lotes en EURUSD, 1 pip = ~$0.10
            # El TP es 40 pips → estimado +$4 por operación ganadora
            # Aquí se registra 0 hasta que el sistema detecte cierre real
            # TODO: conectar con el cierre real de MT5 para actualizar capital
            print(f"✅ Operación ejecutada y registrada.")
    else:
        print("⏸️  IA decidió no operar en este ciclo.")


if __name__ == "__main__":
    print("🚀 Iniciando Trading Bot...")
    print(f"   Símbolo : {SYMBOL}")
    print(f"   Intervalo: {INTERVAL}s")
    print(f"   Objetivos: $18/día | $125/semana | $500/mes")

    mt5     = MT5Connector()
    ai      = AIAnalyst()
    notion  = NotionLogger()
    trader  = Trader(mt5)
    memory  = PineconeMemory()
    capital = CapitalGuard()

    if not mt5.connect():
        print("❌ No se pudo conectar a MT5. Abortando.")
        exit(1)

    print(f"✅ MT5 conectado     | {SYMBOL}")
    print(f"✅ Pinecone listo    | índice: {os.getenv('PINECONE_INDEX_NAME', 'bottrading')}")
    print(f"✅ CapitalGuard listo | $50 → $500 en 30 días")

    schedule.every(INTERVAL).seconds.do(run_bot)
    run_bot()  # primer ciclo inmediato

    while True:
        schedule.run_pending()
        time.sleep(1)
