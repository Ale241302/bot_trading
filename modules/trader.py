"""
trader.py
────────────────────────────────────────────
Ejecutor de ordenes BUY/SELL en MT5.
  - SL (Stop Loss): 15 pips
  - TP (Take Profit): 30 pips (ratio 1:2)
────────────────────────────────────────────
"""

import os
import MetaTrader5 as mt5
from modules.mt5_connector import MT5Connector

class Trader:
    def __init__(self, connector: MT5Connector):
        self.connector = connector
        self.lot_size  = float(os.getenv("LOT_SIZE", 0.01))

    def execute(self, symbol: str, action: str) -> dict | None:
        """
        Ejecuta una orden de mercado.
        action: "BUY" o "SELL"
        Retorna dict con info de la orden o None si falla.
        """
        # Verificar posiciones abiertas antes de operar
        positions = mt5.positions_get(symbol=symbol)
        if positions and len(positions) > 0:
            print(f"Ya hay {len(positions)} posición(es) abierta(s) en {symbol}. HOLD.")
            return None

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print(f"No se pudo obtener tick para {symbol}")
            return None

        price       = tick.ask if action == "BUY" else tick.bid
        symbol_info = mt5.symbol_info(symbol)
        point       = symbol_info.point
        
        # Leer modo del capital guard o variables de entorno
        sl_pips = int(os.getenv("SL_PIPS", 15))
        tp_pips = int(os.getenv("TP_PIPS", 30))
        sl = round((price - sl_pips * 10 * point) if action == "BUY" else (price + sl_pips * 10 * point), symbol_info.digits)
        tp = round((price + tp_pips * 10 * point) if action == "BUY" else (price - tp_pips * 10 * point), symbol_info.digits)

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       self.lot_size,
            "type":         order_type,
            "price":        price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    20,
            "magic":        234000,
            "comment":      "AI Trading Bot",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Orden fallida: {result.retcode} - {result.comment}")
            return None

        print(f"Orden ejecutada: {action} {symbol} | Precio: {price} | Lotes: {self.lot_size}")
        return {"ticket": result.order, "price": price, "sl": sl, "tp": tp}
