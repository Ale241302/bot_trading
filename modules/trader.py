"""
trader.py
────────────────────────────────────────────
Ejecutor centralizado de operaciones MT5.
Soporta: BUY, SELL, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP,
         CLOSE, CLOSE_PARTIAL, MODIFY_SL_TP, TRAILING_STOP.
────────────────────────────────────────────
"""

import logging

import MetaTrader5 as mt5
from modules.mt5_connector import MT5Connector

logger = logging.getLogger(__name__)


class Trader:
    DEVIATION       = 20
    MAGIC_MARKET    = 234000
    MAGIC_PENDING   = 234001
    MAGIC_CLOSE     = 234002
    MAGIC_PARTIAL   = 234003
    TRAILING_PIPS   = 3.5
    MIN_DIST_PIPS   = 1.0
    REPRICE_PIPS    = 3.0

    def __init__(self, connector: MT5Connector):
        self.connector = connector

    @staticmethod
    def _pip_size(info) -> float:
        """
        Tamaño de 1 pip en precio.
        - 5 dígitos (EURUSD, GBPUSD): point=0.00001, pip=0.0001 → point*10
        - 3 dígitos (USDJPY):         point=0.001,   pip=0.01   → point*10
        - 4 dígitos:                  point=0.0001,  pip=0.0001 → point*1
        - 2 dígitos (JPY clásico):    point=0.01,    pip=0.01   → point*1
        """
        return info.point * (10 if info.digits in (5, 3) else 1)

    def _get_point_and_tick(self, symbol: str):
        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)
        if tick is None or symbol_info is None:
            return None, None
        return tick, symbol_info

    @staticmethod
    def _send(req: dict):
        """Wrapper de mt5.order_send que tolera res=None (broker desconectado)."""
        res = mt5.order_send(req)
        if res is None:
            err = mt5.last_error()
            logger.error(f"order_send devolvió None — last_error={err}")
        return res

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

        pip = self._pip_size(info)
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if action == "BUY" else tick.bid

        sl = round((price - sl_pips * pip) if action == "BUY" else (price + sl_pips * pip), info.digits)
        tp = round((price + tp_pips * pip) if action == "BUY" else (price - tp_pips * pip), info.digits)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot_size),
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.DEVIATION,
            "magic": self.MAGIC_MARKET,
            "comment": "WDC Market",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            rc = getattr(res, "retcode", "None")
            cm = getattr(res, "comment", "broker no respondió")
            logger.warning(f"[{action}] Falló: {rc} - {cm}")
            return None

        logger.info(f"[{action}] Éxito | Tkt: {res.order} | Lote: {lot_size}")
        return {"ticket": res.order, "price": price, "sl": sl, "tp": tp}

    def _execute_pending(self, action: str, symbol: str, lot_size: float, sl_pips: float, tp_pips: float, target_price: float) -> dict | None:
        tick, info = self._get_point_and_tick(symbol)
        if not tick or not target_price: return None

        pip = self._pip_size(info)
        original_target = round(float(target_price), info.digits)
        target_price    = original_target

        is_buy = "BUY" in action
        min_distance = pip * self.MIN_DIST_PIPS

        if action == "BUY_LIMIT" and target_price >= tick.ask - min_distance:
            target_price = round(tick.bid - self.REPRICE_PIPS * pip, info.digits)
        elif action == "SELL_LIMIT" and target_price <= tick.bid + min_distance:
            target_price = round(tick.ask + self.REPRICE_PIPS * pip, info.digits)
        elif action == "BUY_STOP" and target_price <= tick.ask + min_distance:
            target_price = round(tick.ask + self.REPRICE_PIPS * pip, info.digits)
        elif action == "SELL_STOP" and target_price >= tick.bid - min_distance:
            target_price = round(tick.bid - self.REPRICE_PIPS * pip, info.digits)

        if target_price != original_target:
            logger.warning(
                f"[{action}] Precio reajustado: {original_target} → {target_price} "
                f"(bid={tick.bid}, ask={tick.ask}, min_dist={self.MIN_DIST_PIPS}p, "
                f"reprice={self.REPRICE_PIPS}p). La IA pidió un precio fuera del rango válido."
            )

        type_map = {
            "BUY_LIMIT":  mt5.ORDER_TYPE_BUY_LIMIT,
            "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
            "BUY_STOP":   mt5.ORDER_TYPE_BUY_STOP,
            "SELL_STOP":  mt5.ORDER_TYPE_SELL_STOP,
        }
        order_type = type_map[action]

        sl = round((target_price - sl_pips * pip) if is_buy else (target_price + sl_pips * pip), info.digits)
        tp = round((target_price + tp_pips * pip) if is_buy else (target_price - tp_pips * pip), info.digits)

        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": float(lot_size),
            "type": order_type,
            "price": target_price,
            "sl": sl,
            "tp": tp,
            "magic": self.MAGIC_PENDING,
            "comment": f"WDC PEND {action}",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            rc = getattr(res, "retcode", "None")
            cm = getattr(res, "comment", "broker no respondió")
            logger.warning(f"[{action}] Falló: {rc} - {cm}")
            return None

        logger.info(f"[{action}] Éxito | Tkt: {res.order} | Precio: {target_price} | SL: {sl} | TP: {tp}")
        return {"ticket": res.order, "price": target_price, "sl": sl, "tp": tp}

    def _execute_close(self, ticket: int) -> dict | None:
        if not ticket:
            return None
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            # Ticket fantasma: la IA pidió cerrar algo que MT5 ya no reporta.
            # Probable causa: ya se cerró por SL/TP. No es fatal, pero TradeMonitor
            # debe enterarse en el próximo ciclo vía history_deals_get.
            logger.warning(f"_execute_close: ticket {ticket} no existe en positions_get (¿ya cerrado?)")
            return None
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
            "magic": self.MAGIC_CLOSE,
            "comment": "WDC CLOSE",
        }
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return None
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
            "magic": self.MAGIC_PARTIAL,
            "comment": "WDC CLOSE PARTIAL",
        }
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return None
        return {"ticket": ticket, "action_executed": f"CLOSED_PARTIAL: {close_vol}"}

    def _execute_modify(self, ticket: int, symbol: str, sl_pips: float, tp_pips: float) -> dict | None:
        if not ticket: return None
        pos = mt5.positions_get(ticket=ticket)
        if not pos: return None
        pos = pos[0]
        tick, info = self._get_point_and_tick(symbol)
        if not tick: return None

        pip = self._pip_size(info)
        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        base_price = pos.price_open

        new_sl = round((base_price - sl_pips * pip) if is_buy else (base_price + sl_pips * pip), info.digits)
        new_tp = round((base_price + tp_pips * pip) if is_buy else (base_price - tp_pips * pip), info.digits)

        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
        }
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return None
        return {"ticket": ticket, "action_executed": "MODIFIED_SL_TP", "sl": new_sl, "tp": new_tp}

    def _execute_trailing_stop(self, ticket: int, symbol: str) -> dict | None:
        if not ticket: return None
        pos = mt5.positions_get(ticket=ticket)
        if not pos: return None
        pos = pos[0]
        tick, info = self._get_point_and_tick(symbol)
        if not tick: return None

        pip = self._pip_size(info)
        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        current_price = tick.bid if is_buy else tick.ask

        new_sl = round(
            (current_price - self.TRAILING_PIPS * pip) if is_buy
            else (current_price + self.TRAILING_PIPS * pip),
            info.digits,
        )

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
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return None
        return {"ticket": ticket, "action_executed": "TRAILING_STOP", "sl": new_sl}
