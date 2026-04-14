"""
main.py
────────────────────────────────────────────
Punto de entrada del bot de trading WDC.
────────────────────────────────────────────
"""

import time
import schedule
from dotenv import load_dotenv
import os
import MetaTrader5 as mt5_api

from modules.mt5_connector   import MT5Connector
from modules.ai_analyst      import AIAnalyst
from modules.notion_logger   import NotionLogger
from modules.trader          import Trader
from modules.trade_monitor   import TradeMonitor
from modules.pinecone_memory import PineconeMemory
from modules.capital_guard   import CapitalGuard
from modules.market_context  import MarketContext

load_dotenv()

SYMBOL   = os.getenv("TRADING_SYMBOL", "EURUSD")
INTERVAL = int(os.getenv("LOOP_INTERVAL_SECONDS", 60))


def run_bot():
    """Ciclo principal de análisis y ejecución."""
    print("\n⏰ Analizando mercado...")
    
    acc_info = mt5_api.account_info()
    if not acc_info:
        print("❌ No se pudo obtener información de la cuenta MT5.")
        return
    capital_activo = acc_info.balance

    # ── PASO 1: CapitalGuard ─────────────────────────────────────────────────
    can_trade, guard_reason = capital.should_trade(capital_activo)
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

    # ── PASO 3: Datos de mercado y posiciones MT5 ────────────────────────────
    market_data = mt5.get_candles(SYMBOL, count=int(os.getenv("CANDLES_HISTORY", 50)))
    if market_data is None:
        print("❌ No se pudieron obtener datos de mercado.")
        return
        
    open_positions = mt5.get_open_positions(SYMBOL)

    # ── PASO 3.5: Monitor de operaciones cerradas ────────────────────────────
    trade_monitor.check_closed_trades()

    # ── PASO 4-6: Ensamblar contexto completo para la IA ────────────────────
    history        = notion.get_recent_operations(limit=10)
    pinecone_ctx   = memory.get_stats_context(SYMBOL)
    capital_status = capital.status_text(capital_activo)
    market_ctx     = mktctx.get_context_text()

    # ── PASO 6: Decisión de la IA ────────────────────────────────────────────
    decision = ai.analyze(
        symbol         = SYMBOL,
        candles        = market_data,
        history        = history,
        open_positions = open_positions,
        pinecone_context = pinecone_ctx,
        capital_status = capital_status,
        market_context = market_ctx,
    )
    print(f"🤖 IA            : {decision.get('action', 'HOLD')} | {decision.get('reason', '')}")

    # ── PASO 7-8: Ejecutar y registrar ──────────────────────────────────────
    action = decision.get("action", "HOLD")
    if action != "HOLD":
        symbol_dec = decision.get("symbol", SYMBOL)
        lot    = float(decision.get("lot", 0.01))
        sl_p   = float(decision.get("sl_pips", 15))
        tp_p   = float(decision.get("tp_pips", 30))
        price  = decision.get("price")
        ticket = decision.get("ticket")

        result = trader.execute(
            action=action,
            symbol=symbol_dec,
            lot_size=lot,
            sl_pips=sl_p,
            tp_pips=tp_p,
            target_price=price,
            ticket=ticket
        )
        if result:
            notion_id = notion.log_operation(
                symbol=symbol_dec, action=action,
                lot_size=lot, price_open=result.get("price", 0),
                reason=decision.get("reason", ""),
            )
            memory.log_operation(
                symbol=symbol_dec, action=action,
                lot_size=lot, price_open=result.get("price", 0),
                reason=decision.get("reason", ""), ticket=result.get("ticket"),
            )
            if "ticket" in result:
                trade_monitor.add_trade(
                    ticket=result["ticket"], symbol=symbol_dec,
                    notion_page_id=notion_id, action=action,
                    lot_size=lot, price_open=result.get("price", 0),
                    reason=decision.get("reason", "")
                )
            print("✅ Operación ejecutada y registrada en Notion + Pinecone.")
    else:
        print("⏸️  IA decidió HOLD en este ciclo.")


if __name__ == "__main__":
    print("🚀 Iniciando Trading Bot WDC...")
    print(f"   Símbolo  : {SYMBOL}")
    print(f"   Intervalo: {INTERVAL}s")
    print(f"   Objetivos: Estrategia Weekly Double Compounding")

    mt5     = MT5Connector()
    ai      = AIAnalyst()
    notion  = NotionLogger()
    trader  = Trader(mt5)
    memory  = PineconeMemory()
    trade_monitor = TradeMonitor(memory, notion)
    capital = CapitalGuard()
    mktctx  = MarketContext()

    if not mt5.connect():
        print("❌ No se pudo conectar a MT5. Abortando.")
        exit(1)

    print(f"✅ MT5 conectado      | {SYMBOL}")
    print(f"✅ Pinecone listo     | {os.getenv('PINECONE_INDEX_NAME', 'bottrading')}")
    print(f"✅ Finnhub listo      | {'configurado' if os.getenv('FINNHUB_API_KEY') else 'SIN KEY ⚠️'}")
    print(f"✅ jblanked News listo| {'configurado' if os.getenv('JBLANKED_API_KEY') else 'SIN KEY ⚠️'}")
    print(f"✅ CapitalGuard listo | Base Dinámica (x2 Semanal)")

    schedule.every(INTERVAL).seconds.do(run_bot)
    run_bot()

    while True:
        schedule.run_pending()
        time.sleep(1)
