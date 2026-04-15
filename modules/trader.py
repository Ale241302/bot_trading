"""
trader.py
────────────────────────────────────────────
Ejecutor centralizado de operaciones MT5.
Soporta: BUY, SELL, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP,
         CLOSE, CLOSE_PARTIAL, MODIFY_SL_TP, TRAILING_STOP.
────────────────────────────────────────────
"""

import MetaTrader5 as mt5
from modules.mt5_connector import MT5Connector

class Trader:
    def __init__(self, connector: MT5Connector):
        self.connector = connector

    def _get_point_and_tick(self, symbol: str):
        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)
        if tick is None or symbol_info is None:
            return None, None
        return tick, symbol_info

    def execute(self, action: str, symbol: str, lot_size: float, sl_pips: float, tp_pips: float, target_price: float = None, ticket: int = None) -> dict | None:
        """
        Punto de entrada universal. Redirige a la función de ejecución específica.
        """
        if action in ["BUY", "SELL"]:
            return self._execute_market(action, symbol, lot_size, sl_pips, tp_pips)
        elif action in ["BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"]:
            return self._execute_pending(action, symbol, lot_size, sl_pips, tp_pips, target_price)
        elif action == "CLOSE":
            return self._execute_close(ticket)
        elif action == "CLOSE_PARTIAL":
            return self._execute_close_partial(ticket)
        elif action == "MODIFY_SL_TP":
            return self._execute_modify(ticket, symbol, sl_pips, tp_pips)
        elif action == "TRAILING_STOP":
            return self._execute_trailing_stop(ticket, symbol)
        
        return None

    def _execute_market(self, action: str, symbol: str, lot_size: float, sl_pips: float, tp_pips: float) -> dict | None:
        tick, info = self._get_point_and_tick(symbol)
        if not tick: return None
        
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if action == "BUY" else tick.bid
        
        sl = round((price - sl_pips * 10 * info.point) if action == "BUY" else (price + sl_pips * 10 * info.point), info.digits)
        tp = round((price + tp_pips * 10 * info.point) if action == "BUY" else (price - tp_pips * 10 * info.point), info.digits)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot_size),
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 234000,
            "comment": "WDC Market",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"[{action}] Falló: {res.retcode} - {res.comment}")
            return None
            
        print(f"[{action}] Éxito | Tkt: {res.order} | Lote: {lot_size}")
        return {"ticket": res.order, "price": price, "sl": sl, "tp": tp}

    def _execute_pending(self, action: str, symbol: str, lot_size: float, sl_pips: float, tp_pips: float, target_price: float) -> dict | None:
        tick, info = self._get_point_and_tick(symbol)
        if not tick or not target_price: return None

        # Redondear target_price a los decimales exactos del símbolo (fix error 10015)
        target_price = round(float(target_price), info.digits)

        # Validar que la orden pendiente esté a un precio válido para MT5
        # BUY_LIMIT debe estar DEBAJO del ask actual; SELL_LIMIT ENCIMA del bid actual
        is_buy = "BUY" in action
        current_ref = tick.ask if is_buy else tick.bid
        min_distance = info.point * 10  # 1 pip mínimo de distancia

        if action == "BUY_LIMIT" and target_price >= tick.ask - min_distance:
            # Precio demasiado cerca o encima del ask → corregir a 3 pips debajo del bid
            target_price = round(tick.bid - 3 * 10 * info.point, info.digits)
        elif action == "SELL_LIMIT" and target_price <= tick.bid + min_distance:
            # Precio demasiado cerca o debajo del bid → corregir a 3 pips encima del ask
            target_price = round(tick.ask + 3 * 10 * info.point, info.digits)
        elif action == "BUY_STOP" and target_price <= tick.ask + min_distance:
            target_price = round(tick.ask + 3 * 10 * info.point, info.digits)
        elif action == "SELL_STOP" and target_price >= tick.bid - min_distance:
            target_price = round(tick.bid - 3 * 10 * info.point, info.digits)

        type_map = {
            "BUY_LIMIT": mt5.ORDER_TYPE_BUY_LIMIT,
            "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
            "BUY_STOP": mt5.ORDER_TYPE_BUY_STOP,
            "SELL_STOP": mt5.ORDER_TYPE_SELL_STOP
        }
        order_type = type_map[action]
        
        sl = round((target_price - sl_pips * 10 * info.point) if is_buy else (target_price + sl_pips * 10 * info.point), info.digits)
        tp = round((target_price + tp_pips * 10 * info.point) if is_buy else (target_price - tp_pips * 10 * info.point), info.digits)

        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": float(lot_size),
            "type": order_type,
            "price": target_price,
            "sl": sl,
            "tp": tp,
            "magic": 234001,
            "comment": f"WDC PEND {action}",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"[{action}] Falló: {res.retcode} - {res.comment}")
            return None
            
        print(f"[{action}] Éxito | Tkt: {res.order} | Precio: {target_price} | SL: {sl} | TP: {tp}")
        return {"ticket": res.order, "price": target_price, "sl": sl, "tp": tp}

    def _execute_close(self, ticket: int) -> dict | None:
        if not ticket: return None
        pos = mt5.positions_get(ticket=ticket)
        if not pos: return None
        pos = pos[0]
        
        tick, info = self._get_point_and_tick(pos.symbol)
        if not tick: return None

        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": pos.ticket,
            "price": price,
            "magic": 234002,
            "comment": "WDC CLOSE"
        }
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE: return None
        return {"ticket": ticket, "action_executed": "CLOSED"}

    def _execute_close_partial(self, ticket: int) -> dict | None:
        if not ticket: return None
        pos = mt5.positions_get(ticket=ticket)
        if not pos: return None
        pos = pos[0]
        
        tick, info = self._get_point_and_tick(pos.symbol)
        if not tick: return None
        
        close_vol = max(0.01, round(pos.volume / 2.0, 2))
        if close_vol >= pos.volume:
            return self._execute_close(ticket)

        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": close_vol,
            "type": order_type,
            "position": pos.ticket,
            "price": price,
            "magic": 234003,
            "comment": "WDC CLOSE PARTIAL"
        }
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE: return None
        return {"ticket": ticket, "action_executed": f"CLOSED_PARTIAL: {close_vol}"}

    def _execute_modify(self, ticket: int, symbol: str, sl_pips: float, tp_pips: float) -> dict | None:
        if not ticket: return None
        pos = mt5.positions_get(ticket=ticket)
        if not pos: return None
        pos = pos[0]
        tick, info = self._get_point_and_tick(symbol)
        if not tick: return None
        
        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        base_price = pos.price_open
        
        new_sl = round((base_price - sl_pips * 10 * info.point) if is_buy else (base_price + sl_pips * 10 * info.point), info.digits)
        new_tp = round((base_price + tp_pips * 10 * info.point) if is_buy else (base_price - tp_pips * 10 * info.point), info.digits)

        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
        }
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE: return None
        return {"ticket": ticket, "action_executed": "MODIFIED_SL_TP", "sl": new_sl, "tp": new_tp}

    def _execute_trailing_stop(self, ticket: int, symbol: str) -> dict | None:
        if not ticket: return None
        pos = mt5.positions_get(ticket=ticket)
        if not pos: return None
        pos = pos[0]
        tick, info = self._get_point_and_tick(symbol)
        if not tick: return None
        
        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        current_price = tick.bid if is_buy else tick.ask
        
        trail_pips = 3.5
        new_sl = round((current_price - trail_pips * 10 * info.point) if is_buy else (current_price + trail_pips * 10 * info.point), info.digits)
        
        if is_buy and new_sl <= pos.sl:
            return None
        if not is_buy and pos.sl > 0 and new_sl >= pos.sl:
            return None

        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": pos.tp,
        }
        res = mt5.order_send(req)
        if res.retcode != mt5.TRADE_RETCODE_DONE: return None
        return {"ticket": ticket, "action_executed": "TRAILING_STOP", "sl": new_sl}
