"""
mt5_connector.py
────────────────────────────────────────────
Conexion con MetaTrader 5:
  - Conectar y desconectar
  - Obtener velas historicas (OHLCV)
  - Consultar posiciones abiertas
────────────────────────────────────────────
"""

import os
import MetaTrader5 as mt5
import pandas as pd

class MT5Connector:
    def __init__(self):
        self.login    = int(os.getenv("MT5_LOGIN"))
        self.password = os.getenv("MT5_PASSWORD")
        self.server   = os.getenv("MT5_SERVER")

    def connect(self) -> bool:
        if not mt5.initialize():
            print(f"Error MT5 initialize(): {mt5.last_error()}")
            return False

        authorized = mt5.login(self.login, password=self.password, server=self.server)
        if not authorized:
            print(f"Login fallido: {mt5.last_error()}")
            mt5.shutdown()
            return False

        info = mt5.account_info()
        print(f"MT5 conectado | Cuenta: {info.login} | Balance: {info.balance} {info.currency}")
        return True

    def disconnect(self):
        mt5.shutdown()
        print("MT5 desconectado.")

    def get_candles(self, symbol: str, count: int = 50, timeframe=mt5.TIMEFRAME_M15) -> pd.DataFrame | None:
        """
        Obtiene las ultimas `count` velas del simbolo.
        Timeframe por defecto: M15 (15 minutos).
        """
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            print(f"No se obtuvieron velas para {symbol}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df[["time", "open", "high", "low", "close", "tick_volume"]]

    def get_open_positions(self, symbol: str = None):
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        return positions if positions else []
