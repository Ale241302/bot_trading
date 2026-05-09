"""
test_signal_engine.py
================================================
Unit tests del motor de señales (funciones puras, sin I/O ni MT5).
Cubre: get_trend, detect_pattern, evaluate_confluence.

Ejecuta:
  python -m pytest tests/test_signal_engine.py -v
  o:
  python tests/test_signal_engine.py
================================================
"""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backtest.signal_engine import (
    detect_pattern,
    evaluate_confluence,
    get_trend,
    _is_pin_bar_bullish,
    _is_pin_bar_bearish,
    _is_engulfing_bullish,
    _is_engulfing_bearish,
)


def _make_candles(closes, highs=None, lows=None, opens=None) -> pd.DataFrame:
    """Helper para construir DataFrames OHLC desde una lista de closes."""
    n = len(closes)
    return pd.DataFrame({
        "Open":  opens  if opens  else closes,
        "High":  highs  if highs  else [c + 0.0005 for c in closes],
        "Low":   lows   if lows   else [c - 0.0005 for c in closes],
        "Close": closes,
    })


class TestGetTrend(unittest.TestCase):

    def test_neutral_with_short_data(self):
        df = _make_candles([1.10] * 10)   # < SMA50 period
        self.assertEqual(get_trend(df), "NEUTRAL")

    def test_buy_when_above_sma_threshold(self):
        # 50 velas en 1.10, vela final muy arriba → BUY
        closes = [1.10] * 49 + [1.115]   # 1.5% por encima
        df = _make_candles(closes)
        self.assertEqual(get_trend(df, threshold=0.0010), "BUY")

    def test_sell_when_below_sma_threshold(self):
        closes = [1.10] * 49 + [1.085]   # 1.4% por debajo
        df = _make_candles(closes)
        self.assertEqual(get_trend(df, threshold=0.0010), "SELL")

    def test_neutral_when_within_threshold(self):
        # cierre justo igual a SMA50 → NEUTRAL
        closes = [1.10] * 50
        df = _make_candles(closes)
        self.assertEqual(get_trend(df, threshold=0.0010), "NEUTRAL")


class TestPinBar(unittest.TestCase):

    def test_pin_bar_bullish(self):
        # mecha inferior larga (>=55%), cuerpo pequeño, mecha superior corta
        # rango total = 100. lower wick = 60 (mín-->open=1.0700, low=1.0640).
        # body small. close en parte alta.
        o, h, l, c = 1.0700, 1.0710, 1.0640, 1.0705
        # total = 0.0070, lower_wick = min(o,c)-l = 0.0700-0.0640 = 0.0060 (85%)
        # body = 5pts (7%). upper_wick = h - max(o,c) = 1.0710-1.0705 = 0.0005 (7%)
        # close > l + 0.55*total = 1.0640 + 0.00385 = 1.06785 → 1.0705 > 1.06785 ✅
        self.assertTrue(_is_pin_bar_bullish(o, h, l, c))

    def test_pin_bar_bearish(self):
        # mecha superior larga
        o, h, l, c = 1.0705, 1.0770, 1.0700, 1.0710
        # total = 0.0070, upper_wick = h - max(o,c) = 1.0770-1.0710 = 0.0060 (85%)
        # close < h - 0.55*total → 1.0710 < 1.0770 - 0.00385 = 1.07315 ✅
        self.assertTrue(_is_pin_bar_bearish(o, h, l, c))

    def test_pin_bar_rejects_doji(self):
        # vela sin mechas dominantes
        self.assertFalse(_is_pin_bar_bullish(1.10, 1.101, 1.099, 1.10))


class TestEngulfing(unittest.TestCase):

    def test_engulfing_bullish(self):
        prev_o, prev_c = 1.10, 1.095   # bajista
        curr_o, curr_c = 1.094, 1.102  # alcista que envuelve
        self.assertTrue(_is_engulfing_bullish(prev_o, prev_c, curr_o, curr_c))

    def test_engulfing_bearish(self):
        prev_o, prev_c = 1.095, 1.10   # alcista
        curr_o, curr_c = 1.101, 1.092  # bajista que envuelve
        self.assertTrue(_is_engulfing_bearish(prev_o, prev_c, curr_o, curr_c))


class TestDetectPattern(unittest.TestCase):

    def test_returns_none_with_few_candles(self):
        df = _make_candles([1.10, 1.10])  # < 3
        self.assertIsNone(detect_pattern(df, "BUY"))


class TestEvaluateConfluence(unittest.TestCase):

    def setUp(self):
        # 50 velas planas H1/H4 → NEUTRAL
        self.flat_h1 = _make_candles([1.10] * 50)
        self.flat_h4 = _make_candles([1.10] * 50)
        self.m15 = _make_candles([1.10] * 5)
        self.sentiment_neutral = {"long_pct": 50.0, "short_pct": 50.0}

    def test_news_block_short_circuits(self):
        action, reason, levels = evaluate_confluence(
            self.m15, self.flat_h1, self.flat_h4,
            self.sentiment_neutral, av_score=0.0, news_block=True,
        )
        self.assertEqual(action, "HOLD")
        self.assertIn("noticia", reason.lower())
        self.assertIn("FALLO", levels["nivel_1_noticias"])

    def test_neutral_trends_yields_hold(self):
        action, _, levels = evaluate_confluence(
            self.m15, self.flat_h1, self.flat_h4,
            self.sentiment_neutral, av_score=0.0, news_block=False,
        )
        self.assertEqual(action, "HOLD")
        self.assertIn("FALLO", levels["nivel_2_tendencia"])

    def test_levels_dict_uses_correct_names(self):
        # Verifica el rename C5: nivel_2 = tendencia, nivel_3 = sentimiento
        _, _, levels = evaluate_confluence(
            self.m15, self.flat_h1, self.flat_h4,
            self.sentiment_neutral, av_score=0.0, news_block=False,
        )
        self.assertIn("nivel_2_tendencia",   levels)
        self.assertIn("nivel_3_sentimiento", levels)
        self.assertNotIn("nivel_2_sentimiento", levels)
        self.assertNotIn("nivel_3_tendencia",   levels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
