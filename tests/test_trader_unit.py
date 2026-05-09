"""
test_trader_unit.py
================================================
Unit tests de Trader._pip_size() y AIAnalyst._validate_decision().
Funciones puras — no requieren MT5 ni red.

Ejecuta:
  python tests/test_trader_unit.py
================================================
"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestPipSize(unittest.TestCase):
    """
    Cubre el bug crítico P0: distancia SL/TP debe ser correcta para JPY (3 dígitos).
    """

    def setUp(self):
        # Importar localmente para no requerir MetaTrader5 si no está disponible.
        try:
            from modules.trader import Trader
            self.Trader = Trader
        except ImportError:
            self.skipTest("MetaTrader5 no disponible en este entorno")

    def test_5_digit_pair_eurusd(self):
        info = SimpleNamespace(digits=5, point=0.00001)
        # 1 pip EURUSD = 0.0001 = point * 10
        self.assertAlmostEqual(self.Trader._pip_size(info), 0.0001, places=8)

    def test_3_digit_pair_usdjpy(self):
        info = SimpleNamespace(digits=3, point=0.001)
        # 1 pip USDJPY = 0.01 = point * 10
        self.assertAlmostEqual(self.Trader._pip_size(info), 0.01, places=8)

    def test_4_digit_pair(self):
        info = SimpleNamespace(digits=4, point=0.0001)
        # point * 1
        self.assertAlmostEqual(self.Trader._pip_size(info), 0.0001, places=8)

    def test_2_digit_pair(self):
        info = SimpleNamespace(digits=2, point=0.01)
        self.assertAlmostEqual(self.Trader._pip_size(info), 0.01, places=8)

    def test_sl_distance_usdjpy_correct(self):
        """
        Regresión del bug pre-P0:
        Para USDJPY a 150.000 con SL=15 pips, el SL debe quedar a 149.85
        (no a 148.50 como salía con el cálculo viejo `* 10 * info.point` mal aplicado).
        """
        info = SimpleNamespace(digits=3, point=0.001)
        pip = self.Trader._pip_size(info)
        entry = 150.000
        sl_pips = 15.0
        sl_buy = entry - sl_pips * pip
        self.assertAlmostEqual(sl_buy, 149.850, places=4)


class TestValidateDecision(unittest.TestCase):
    """
    Schema validation de la respuesta de OpenAI (H4).
    """

    def setUp(self):
        try:
            from modules.ai_analyst import AIAnalyst
            self.AIAnalyst = AIAnalyst
        except ImportError:
            self.skipTest("openai no disponible")
        # Construir instancia sin __init__ (no queremos invocar OpenAI).
        self.ai = self.AIAnalyst.__new__(self.AIAnalyst)

    def test_hold_passes(self):
        out = self.ai._validate_decision({"action": "HOLD", "reason": "no setup"})
        self.assertEqual(out["action"], "HOLD")

    def test_buy_with_valid_numbers(self):
        out = self.ai._validate_decision({
            "action": "BUY", "lot": 0.05, "sl_pips": 8, "tp_pips": 16,
            "reason": "ok",
        })
        self.assertEqual(out["action"], "BUY")
        self.assertEqual(out["lot"], 0.05)

    def test_invalid_action_falls_back_to_hold(self):
        out = self.ai._validate_decision({"action": "GIBBERISH"})
        self.assertEqual(out["action"], "HOLD")
        self.assertIn("inválida", out["reason"])

    def test_negative_lot_falls_back_to_hold(self):
        out = self.ai._validate_decision({
            "action": "BUY", "lot": -0.05, "sl_pips": 8, "tp_pips": 16,
        })
        self.assertEqual(out["action"], "HOLD")

    def test_missing_sl_pips_falls_back_to_hold(self):
        # sl_pips no provisto explícitamente → usa default. Lot 0.05 OK.
        # PERO si la IA manda explícitamente null/string, debe degradar.
        out = self.ai._validate_decision({
            "action": "BUY", "lot": 0.05, "sl_pips": "abc", "tp_pips": 16,
        })
        self.assertEqual(out["action"], "HOLD")

    def test_non_dict_input_returns_hold(self):
        out = self.ai._validate_decision("hello")
        self.assertEqual(out["action"], "HOLD")

    def test_lowercase_action_normalized(self):
        out = self.ai._validate_decision({
            "action": "buy", "lot": 0.05, "sl_pips": 8, "tp_pips": 16,
        })
        self.assertEqual(out["action"], "BUY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
