"""
pair_config.py
Especificaciones centralizadas para cada par operado en el backtest y bot live.

Cada par define:
  - pip_size:            tamaño de 1 pip (0.0001 para majors, 0.01 para JPY pairs)
  - pip_value_per_lot:   valor en USD de 1 pip por lote estándar
  - sl_pips / tp_pips:   SL y TP en pips (mantiene RR 1:2)
  - sentiment_base_long: sesgo histórico retail (% long promedio)
  - trend_threshold:     umbral para get_trend() (distancia a SMA50)
  - yf_symbol:           ticker de yfinance para descargar datos
"""

PAIR_SPECS = {
    "EURUSD": {
        "pip_size": 0.0001,
        "pip_value_per_lot": 10.0,    # $10/pip/lote estándar
        "sl_pips": 8.0,
        "tp_pips": 16.0,              # RR 1:2
        "sentiment_base_long": 62.0,  # retail tiende ~62% long
        "trend_threshold": 0.0010,    # 0.10%
        "yf_symbol": "EURUSD=X",
    },
    "GBPUSD": {
        "pip_size": 0.0001,
        "pip_value_per_lot": 10.0,
        "sl_pips": 10.0,              # más volátil → SL más amplio
        "tp_pips": 20.0,              # RR 1:2
        "sentiment_base_long": 58.0,  # retail tiende ~58% long
        "trend_threshold": 0.0010,    # 0.10%
        "yf_symbol": "GBPUSD=X",
    },
    "USDJPY": {
        "pip_size": 0.01,             # JPY pair = 0.01
        "pip_value_per_lot": 6.50,    # ~$6.50/pip/lote (varía con tasa USD/JPY)
        "sl_pips": 10.0,
        "tp_pips": 20.0,              # RR 1:2
        "sentiment_base_long": 55.0,  # retail tiende ~55% long
        "trend_threshold": 0.0015,    # 0.15% — mayor para JPY
        "yf_symbol": "JPY=X",
    },
}

DEFAULT_PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]
