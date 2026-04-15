"""
main.py
────────────────────────────────────────────
Punto de entrada del bot de trading WDC.
────────────────────────────────────────────
"""

import time
import schedule
from datetime import datetime, timezone
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

DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def run_bot():
    """Ciclo principal de análisis y ejecución."""

    # ── LOG UTC — primera línea de cada ciclo ────────────────────────
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()   # solo referencia visual, NO se usa para lógica
    day_name  = DAYS_ES[now_utc.weekday()]
    is_friday = now_utc.weekday() == 4
    friday_warning = " ⚠️  VIERNES — cierre automático a las 17:00 UTC" if is_friday else ""
    print(
        f"\n⏰ Ciclo UTC   : {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
        f"({day_name}){friday_warning}"
    )
    print(
        f"   Hora local : {now_local.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(sólo referencia — la lógica usa UTC)"
    )
    if is_friday and now_utc.hour >= 14:
        print(f"   ⏳ Ventana viernes: {17 - now_utc.hour}h {60 - now_utc.minute}min para cierre total.")
    print("⏳ Analizando mercado...")
    # ─────────────────────────────────────────────────────

    acc_info = mt5_api.account_info()
    if not acc_info:
        print("❌ No se pudo obtener información de la cuenta MT5.")
        return
        
    capital_techo = float(os.getenv("CAPITAL_TRABAJO", 50))
    capital_activo = min(capital_techo, acc_info.balance)
    if capital_techo < acc_info.balance:
        print(f"💼 Usando CAPITAL_TRABAJO límite: ${capital_techo} (Ignorando balance demo de ${acc_info.balance})")

    # ── PASO 1: CapitalGuard ──────────────────────────────────────────
    can_trade, guard_reason = capital.should_trade(capital_activo)
    print(f"💰 Capital Guard : {guard_reason}")
    if not can_trade:
        print("⏸️  Capital Guard bloqueó el ciclo.")
        return

    # ── PASO 2: Bloqueo por noticias ────────────────────────────────
    hold_news, news_reason = mktctx.should_hold_news()
    if hold_news:
        print(f"🚨 News Block     : {news_reason}")
        print("⏸️  Noticia de alto impacto próxima. No se llama a la IA.")
        return

    # ── PASO 3: Datos de mercado y posiciones MT5 ────────────────────
    count_candles = int(os.getenv("CANDLES_HISTORY", 50))
    candles_m15 = mt5.get_candles(SYMBOL, count=count_candles, timeframe=mt5_api.TIMEFRAME_M15)
    candles_h1  = mt5.get_candles(SYMBOL, count=count_candles, timeframe=mt5_api.TIMEFRAME_H1)
    candles_h4  = mt5.get_candles(SYMBOL, count=count_candles, timeframe=mt5_api.TIMEFRAME_H4)

    if candles_m15 is None or candles_h1 is None or candles_h4 is None:
        print("❌ No se pudieron obtener datos de mercado (M15, H1, H4).")
        return
        
    multi_candles = {
        "M15": candles_m15,
        "H1": candles_h1,
        "H4": candles_h4
    }
        
    open_positions  = mt5.get_open_positions(SYMBOL)
    pending_orders  = mt5.get_pending_orders(SYMBOL)

    total_operaciones = len(open_positions) + len(pending_orders)
    print(f"📊 Posiciones abiertas: {len(open_positions)} | Órdenes pendientes: {len(pending_orders)} | Total: {total_operaciones}")

    # ── PASO 3.5: Monitor de operaciones cerradas ──────────────────
    trade_monitor.check_closed_trades()

    # ── PASO 4-6: Contexto completo para la IA ──────────────────────
    history        = notion.get_recent_operations(limit=10)
    pinecone_ctx   = memory.get_stats_context(SYMBOL)
    capital_status = capital.status_text(capital_activo)
    market_ctx     = mktctx.get_context_text()

    # ── PASO 6: Decisión de la IA ──────────────────────────────
    decision = ai.analyze(
        symbol           = SYMBOL,
        candles          = multi_candles,
        history          = history,
        open_positions   = open_positions,
        pending_orders   = pending_orders,
        pinecone_context = pinecone_ctx,
        capital_status   = capital_status,
        market_context   = market_ctx,
    )
    print(f"🤖 IA            : {decision.get('action', 'HOLD')} | {decision.get('reason', '')}")

    # ── PASO 7-8: Ejecutar y registrar ───────────────────────────
    action = decision.get("action", "HOLD")
    if action != "HOLD":
        symbol_dec = decision.get("symbol", SYMBOL)
        lot    = float(decision.get("lot", 0.01))
        sl_p   = float(decision.get("sl_pips", 15))
        tp_p   = float(decision.get("tp_pips", 15))
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
    print(f"   Objetivos: Estrategia Weekly Double Compounding Híbrida")
    print(f"   Hora UTC  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC  "
          f"(hora local del sistema: {datetime.now().strftime('%H:%M:%S')})")
    print("   ⚠️  Toda la lógica de tiempo usa UTC estricto. La hora local es solo referencia.")

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
    print(f"✅ CapitalGuard listo | Base Dinámica (x2 Semanal) — SL 8 pips / TP 16 pips")

    schedule.every(INTERVAL).seconds.do(run_bot)
    run_bot()

    while True:
        schedule.run_pending()
        time.sleep(1)
