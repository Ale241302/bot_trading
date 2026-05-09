# test_order.py — borrar tras la prueba
import os
from dotenv import load_dotenv
import MetaTrader5 as mt5

load_dotenv()
mt5.initialize()
mt5.login(int(os.getenv("MT5_LOGIN")), os.getenv("MT5_PASSWORD"), os.getenv("MT5_SERVER"))

# Intenta una orden BUY de 0.01 lotes con SL muy cerca para que se cierre rápido
tick = mt5.symbol_info_tick("EURUSD")
req = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": "EURUSD",
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "sl": round(tick.ask - 0.0008, 5),  # SL 8 pips
    "tp": round(tick.ask + 0.0016, 5),  # TP 16 pips
    "deviation": 20,
    "magic": 234000,
    "comment": "test_python_api",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC,
}
res = mt5.order_send(req)
print("retcode:", res.retcode, "(10009 = OK)")
print("comment:", res.comment)
mt5.shutdown()