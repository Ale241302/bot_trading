"""
mt5_connector.py
────────────────────────────────────────────
Conexion con MetaTrader 5:
  - Conectar y desconectar
  - Obtener velas historicas (OHLCV)
  - Consultar posiciones abiertas
  - Consultar ordenes pendientes
────────────────────────────────────────────
"""

import logging
import os

import MetaTrader5 as mt5
import pandas as pd

logger = logging.getLogger(__name__)


class MT5Connector:
    def __init__(self):
        login_raw     = os.getenv("MT5_LOGIN", "").strip()
        password      = os.getenv("MT5_PASSWORD", "").strip()
        server        = os.getenv("MT5_SERVER", "").strip()

        missing = [name for name, val in
                   [("MT5_LOGIN", login_raw), ("MT5_PASSWORD", password), ("MT5_SERVER", server)]
                   if not val]
        if missing:
            raise RuntimeError(
                f"MT5Connector: faltan variables de entorno: {', '.join(missing)}. "
                f"Verifica el archivo .env."
            )

        try:
            self.login = int(login_raw)
        except ValueError:
            raise RuntimeError(
                f"MT5Connector: MT5_LOGIN debe ser numérico, recibido: '{login_raw}'."
            )

        self.password = password
        self.server   = server

    def is_connected(self) -> bool:
        """Verifica si el terminal MT5 está inicializado y conectado."""
        terminal_info = mt5.terminal_info()
        if terminal_info is None:
            return False
        
        # También verificamos si la cuenta está conectada
        account_info = mt5.account_info()
        if account_info is None:
            return False
            
        return True

    def connect(self) -> bool:
        """
        Asegura la conexión con MT5. Si ya está conectado, solo verifica.
        Si no, inicializa y hace login.
        """
        # Si ya parece estar conectado, intentamos un ping rápido
        if self.is_connected():
            return True

        # Si no, intentamos (re)inicializar
        if not mt5.initialize():
            logger.error(f"Error MT5 initialize(): {mt5.last_error()}")
            return False

        authorized = mt5.login(self.login, password=self.password, server=self.server)
        if not authorized:
            logger.error(f"Login MT5 fallido: {mt5.last_error()}")
            # Liberamos la sesión para no dejar terminal zombie con login inválido.
            # Si el problema es transitorio, el siguiente connect() reintenta limpio.
            try:
                mt5.shutdown()
            except Exception as e:
                logger.warning(f"shutdown() tras login fallido falló: {e}")
            return False

        info = mt5.account_info()
        if info:
            logger.info(f"✅ MT5 conectado | Cuenta: {info.login} | {info.server}")
            return True

        # initialize() OK pero account_info() devolvió None: estado inconsistente,
        # liberamos para no dejar sesión a medio iniciar.
        try:
            mt5.shutdown()
        except Exception:
            pass
        return False

    def disconnect(self):
        mt5.shutdown()
        logger.info("MT5 desconectado.")

    def get_candles(self, symbol: str, count: int = 50, timeframe=mt5.TIMEFRAME_M15) -> pd.DataFrame | None:
        """
        Obtiene las ultimas `count` velas del simbolo.
        Timeframe por defecto: M15 (15 minutos).
        """
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning(f"No se obtuvieron velas para {symbol}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df[["time", "open", "high", "low", "close", "tick_volume"]]

    def get_account_info(self):
        """Retorna información de la cuenta. Reconecta si es necesario."""
        if not self.is_connected():
            self.connect()
        return mt5.account_info()

    def get_terminal_info(self):
        """Retorna información del terminal. Reconecta si es necesario."""
        if not self.is_connected():
            self.connect()
        return mt5.terminal_info()

    def get_open_positions(self, symbol: str = None):
        """Retorna lista de posiciones abiertas (ejecutadas)."""
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        return positions if positions else []

    def get_pending_orders(self, symbol: str = None) -> list:
        """
        Retorna lista de ordenes pendientes activas (BUY_LIMIT, SELL_LIMIT,
        BUY_STOP, SELL_STOP) aun no ejecutadas.
        Cada elemento tiene: ticket, type, symbol, volume, price_open, sl, tp.
        """
        orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        if not orders:
            return []

        type_map = {
            mt5.ORDER_TYPE_BUY_LIMIT:  "BUY_LIMIT",
            mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
            mt5.ORDER_TYPE_BUY_STOP:   "BUY_STOP",
            mt5.ORDER_TYPE_SELL_STOP:  "SELL_STOP",
        }
        result = []
        for o in orders:
            result.append({
                "ticket":     o.ticket,
                "type":       type_map.get(o.type, f"TYPE_{o.type}"),
                "symbol":     o.symbol,
                "volume":     o.volume_initial,
                "price":      o.price_open,
                "sl":         o.sl,
                "tp":         o.tp,
            })
        return result
