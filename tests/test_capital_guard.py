"""
test_capital_guard.py
================================================
Unit tests de CapitalGuard (funciones puras + fases + circuit breakers).

Ejecuta:
  python tests/test_capital_guard.py
o:
  python -m pytest tests/test_capital_guard.py -v
================================================
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.capital_guard import (
    CapitalGuard,
    MAX_CONSECUTIVE_SL,
    MAX_DAILY_LOSS_PCT,
    MAX_SL_WEEK,
    RIESGO_CRECIMIENTO,
    RIESGO_ESCUDO,
    RIESGO_CONSOLIDACION,
    SL_PIPS,
    TP_PIPS,
)


def _utc(year=2026, month=5, day=11, hour=10):
    """Helper: lunes 11 may 2026, hora UTC. (10 UTC = 5 AM Colombia)"""
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


class TestCapitalGuardConstants(unittest.TestCase):
    """Verifica que la API pública de constantes esté disponible."""

    def test_module_constants_match_class(self):
        self.assertEqual(CapitalGuard.SL_PIPS, SL_PIPS)
        self.assertEqual(CapitalGuard.TP_PIPS, TP_PIPS)
        self.assertEqual(CapitalGuard.RIESGO_CRECIMIENTO, RIESGO_CRECIMIENTO)
        self.assertEqual(CapitalGuard.MAX_CONSECUTIVE_SL, MAX_CONSECUTIVE_SL)
        self.assertEqual(CapitalGuard.MAX_DAILY_LOSS_PCT, MAX_DAILY_LOSS_PCT)

    def test_risk_phases_ordered(self):
        # La regla de WDC: Crecimiento > Consolidación > Escudo
        self.assertGreater(RIESGO_CRECIMIENTO,   RIESGO_CONSOLIDACION)
        self.assertGreater(RIESGO_CONSOLIDACION, RIESGO_ESCUDO)

    def test_sl_tp_ratio_is_1_to_2(self):
        self.assertEqual(TP_PIPS / SL_PIPS, 2.0)


class TestShouldTrade(unittest.TestCase):
    """Reglas de horario, racha de SL y stop diario."""

    def setUp(self):
        # Estado limpio en tmp dir para no chocar con capital_state.json del repo.
        self._tmp = tempfile.mkdtemp()
        self._oldcwd = os.getcwd()
        os.chdir(self._tmp)
        self.cg = CapitalGuard()

    def tearDown(self):
        os.chdir(self._oldcwd)

    def _set_now(self, dt):
        return patch.object(self.cg, "_now", return_value=dt)

    def test_outside_trading_hours_blocks(self):
        # 7 AM Colombia = 12 UTC ; 11 AM Colombia = 16 UTC. Probamos a las 1 AM Col
        with self._set_now(_utc(hour=6)):
            ok, reason = self.cg.should_trade(50.0)
            self.assertFalse(ok)
            self.assertIn("horario", reason.lower())

    def test_within_hours_allows(self):
        # 10 UTC = 5 AM Colombia (operativo)
        with self._set_now(_utc(hour=10)):
            ok, _ = self.cg.should_trade(50.0)
            self.assertTrue(ok)

    def test_friday_after_11am_colombia_blocks(self):
        # Viernes 17 UTC = 12 Col → fuera de horario por ambas reglas
        friday_late = _utc(year=2026, month=5, day=15, hour=17)
        with self._set_now(friday_late):
            ok, reason = self.cg.should_trade(50.0)
            self.assertFalse(ok)

    def test_max_daily_loss_blocks(self):
        # Marca pérdidas que superen el 20%
        now = _utc(hour=10)
        self.cg._operations = [
            {"pnl": -15.0, "ts": now - timedelta(hours=1)},
        ]
        with self._set_now(now):
            ok, reason = self.cg.should_trade(50.0)
            self.assertFalse(ok)
            self.assertIn("loss", reason.lower())

    def test_consecutive_sl_streak_blocks(self):
        now = _utc(hour=10)
        # 3 SL consecutivos hoy
        self.cg._operations = [
            {"pnl": -2.0, "ts": now - timedelta(minutes=30)},
            {"pnl": -2.0, "ts": now - timedelta(minutes=20)},
            {"pnl": -2.0, "ts": now - timedelta(minutes=10)},
        ]
        with self._set_now(now):
            ok, reason = self.cg.should_trade(50.0)
            self.assertFalse(ok)
            self.assertIn("racha", reason.lower())

    def test_weekly_circuit_breaker_blocks(self):
        # 5 SL repartidos en los últimos 7 días, no consecutivos hoy
        now = _utc(hour=10)
        self.cg._operations = [
            {"pnl": -2.0, "ts": now - timedelta(days=6)},
            {"pnl": +5.0, "ts": now - timedelta(days=5)},   # TP intermedio
            {"pnl": -2.0, "ts": now - timedelta(days=4)},
            {"pnl": -2.0, "ts": now - timedelta(days=3)},
            {"pnl": -2.0, "ts": now - timedelta(days=2)},
            {"pnl": -2.0, "ts": now - timedelta(days=1)},
        ]
        with self._set_now(now):
            ok, reason = self.cg.should_trade(50.0)
            self.assertFalse(ok)
            self.assertIn("semanal", reason.lower())


class TestPhases(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._oldcwd = os.getcwd()
        os.chdir(self._tmp)
        self.cg = CapitalGuard()

    def tearDown(self):
        os.chdir(self._oldcwd)

    def test_crecimiento_when_no_pnl_yet(self):
        with patch.object(self.cg, "_now", return_value=_utc(hour=10)):
            phase, risk = self.cg.get_phase(50.0)
            self.assertEqual(phase, "CRECIMIENTO")
            self.assertEqual(risk, RIESGO_CRECIMIENTO)

    def test_consolidacion_at_50pct_target(self):
        # base 50, daily target 20% = 10. PnL día = 5 → 50% → consolidación
        now = _utc(hour=10)
        self.cg._operations = [{"pnl": 5.0, "ts": now - timedelta(minutes=10)}]
        with patch.object(self.cg, "_now", return_value=now):
            phase, risk = self.cg.get_phase(50.0)
            self.assertEqual(phase, "CONSOLIDACION")
            self.assertEqual(risk, RIESGO_CONSOLIDACION)

    def test_escudo_when_target_reached(self):
        now = _utc(hour=10)
        self.cg._operations = [{"pnl": 12.0, "ts": now - timedelta(minutes=10)}]
        with patch.object(self.cg, "_now", return_value=now):
            phase, risk = self.cg.get_phase(50.0)
            self.assertEqual(phase, "ESCUDO")
            self.assertEqual(risk, RIESGO_ESCUDO)


if __name__ == "__main__":
    unittest.main(verbosity=2)
