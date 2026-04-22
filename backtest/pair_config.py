"""
pair_config.py
Especificaciones centralizadas para cada par operado en el backtest y bot live.

v6 (2026-04-22):
- USDJPY trend_threshold: 0.0015→0.0020. Filtra señales falsas en zona lateral;
  el JPY tuvo muchos rangos sin dirección en 2025-2026 que generaban N2 falso.
- USDJPY sentiment_base_long: 43→38. Retail estuvo más short en JPY durante
  2025-2026 por apreciación del yen (safe-haven flows + política BoJ).

v5 (2026-04-22):
- GBPUSD: SL 10p→15p, TP 20p→30p. El ruido normal del GBP es ~12-13p;
  con SL=10p el mercado tocaba el SL antes de que la señal se desarrollara.
- USDJPY: SL 10p→15p, TP 20p→30p. Igual que GBP — rango intraday más amplio.
- GBPUSD threshold: 0.0010→0.0012. Tendencia más exigente para reducir señales falsas.
- GBPUSD sentiment_base_long: 58→57. Ajuste menor basado en datos históricos.
- USDJPY sentiment_base_long: 55→43. Retail en JPY tiende más a short (safe-haven).
"""

PAIR_SPECS = {
    "EURUSD": {
        "pip_size": 0.0001,
        "pip_value_per_lot": 10.0,     # $10/pip/lote estándar
        "sl_pips": 8.0,
        "tp_pips": 16.0,               # RR 1:2
        "sentiment_base_long": 62.0,   # retail ~62% long históricamente
        "trend_threshold": 0.0010,     # 0.10%
        "yf_symbol": "EURUSD=X",
    },
    "GBPUSD": {
        "pip_size": 0.0001,
        "pip_value_per_lot": 10.0,
        "sl_pips": 15.0,               # FIX v5: 10→15p (ruido GBP ~12-13p)
        "tp_pips": 30.0,               # FIX v5: 20→30p (RR 1:2 mantenido)
        "sentiment_base_long": 57.0,   # retail ~57% long
        "trend_threshold": 0.0012,     # FIX v5: 0.0010→0.0012 (filtro más exigente)
        "yf_symbol": "GBPUSD=X",
    },
    "USDJPY": {
        "pip_size": 0.01,              # JPY pair = 0.01
        "pip_value_per_lot": 6.50,     # ~$6.50/pip/lote
        "sl_pips": 15.0,               # FIX v5: 10→15p (rango intraday más amplio)
        "tp_pips": 30.0,               # FIX v5: 20→30p (RR 1:2 mantenido)
        "sentiment_base_long": 38.0,   # FIX v6: 43→38 (retail más short en JPY 2025-2026)
        "trend_threshold": 0.0020,     # FIX v6: 0.0015→0.0020 (filtra laterales JPY)
        "yf_symbol": "JPY=X",
    },
}

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]
