"""
test_mt5_connection.py
================================================
Test de conexion con MetaTrader 5 - Pepperstone

Que verifica este test:
  1. Que MT5 se inicializa correctamente
  2. Que el login con las credenciales funciona
  3. Que se puede obtener info de la cuenta
  4. Que se pueden obtener velas historicas (OHLCV)
  5. Que se puede consultar el precio actual del simbolo
  6. Que se pueden ver las posiciones abiertas

Como ejecutarlo:
  python tests/test_mt5_connection.py
================================================
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

import MetaTrader5 as mt5
from modules.mt5_connector import MT5Connector

# ------------------------------------------------
# Colores para la consola
# ------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}  [PASS]{RESET} {msg}")
def fail(msg): print(f"{RED}  [FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}  [INFO]{RESET} {msg}")


def test_initialize():
    print("\n--- Test 1: Inicializacion de MT5 ---")
    connector = MT5Connector()
    result = connector.connect()
    if result:
        ok("MT5 inicializado y login exitoso")
    else:
        fail("No se pudo inicializar MT5")
        print("  Verifica que MetaTrader 5 este instalado y las credenciales en .env sean correctas.")
        sys.exit(1)
    return connector


def test_account_info():
    print("\n--- Test 2: Informacion de la cuenta ---")
    info_data = mt5.account_info()
    if info_data is None:
        fail(f"No se pudo obtener info de cuenta: {mt5.last_error()}")
        return

    ok(f"Login:    {info_data.login}")
    ok(f"Nombre:   {info_data.name}")
    ok(f"Servidor: {info_data.server}")
    ok(f"Balance:  {info_data.balance} {info_data.currency}")
    ok(f"Equity:   {info_data.equity} {info_data.currency}")
    ok(f"Leverage: 1:{info_data.leverage}")


def test_get_candles(connector: MT5Connector):
    print("\n--- Test 3: Obtener velas historicas (OHLCV) ---")
    symbol = os.getenv("TRADING_SYMBOL", "EURUSD")
    df = connector.get_candles(symbol, count=10)

    if df is None or df.empty:
        fail(f"No se obtuvieron velas para {symbol}")
        return

    ok(f"Se obtuvieron {len(df)} velas de {symbol}")
    info(f"Columnas: {list(df.columns)}")
    info(f"Ultima vela:\n{df.tail(1).to_string(index=False)}")


def test_current_price():
    print("\n--- Test 4: Precio actual del simbolo ---")
    symbol = os.getenv("TRADING_SYMBOL", "EURUSD")
    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        fail(f"No se pudo obtener precio de {symbol}: {mt5.last_error()}")
        return

    ok(f"Simbolo: {symbol}")
    ok(f"BID (precio venta): {tick.bid}")
    ok(f"ASK (precio compra): {tick.ask}")
    ok(f"Spread: {round((tick.ask - tick.bid) * 100000, 1)} pips")


def test_open_positions(connector: MT5Connector):
    print("\n--- Test 5: Posiciones abiertas ---")
    positions = connector.get_open_positions()

    if len(positions) == 0:
        info("No hay posiciones abiertas en este momento (esto es normal).")
    else:
        ok(f"Posiciones abiertas: {len(positions)}")
        for pos in positions:
            info(f"  Ticket: {pos.ticket} | {pos.symbol} | {'BUY' if pos.type == 0 else 'SELL'} | Profit: {pos.profit}")


def test_disconnect(connector: MT5Connector):
    print("\n--- Test 6: Desconexion ---")
    connector.disconnect()
    ok("MT5 desconectado correctamente")


if __name__ == "__main__":
    print("================================================")
    print("  TEST DE CONEXION - MetaTrader 5 / Pepperstone")
    print("================================================")

    connector = test_initialize()
    test_account_info()
    test_get_candles(connector)
    test_current_price()
    test_open_positions(connector)
    test_disconnect(connector)

    print("\n================================================")
    print(f"{GREEN}  Todos los tests de MT5 completados.{RESET}")
    print("================================================\n")
