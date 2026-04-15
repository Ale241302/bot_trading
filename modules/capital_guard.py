"""
capital_guard.py
================================================
Guardián de capital progresivo — Estrategia WDC Híbrida
Weekly Double Compounding:
- Duplicar el capital semanalmente (50 -> 100 -> 200).
- Lote sugerido dinámico por fase.

Versión WDC AGRESIVA:
  Fase CRECIMIENTO  : 10% de riesgo por operación (acelerador a fondo)
  Fase CONSOLIDACIÓN: 5%  de riesgo (ganó >50% del objetivo diario)
  Fase ESCUDO       : 1%  de riesgo (meta diaria alcanzada o cierre viernes)
  Horario operativo : 2:00 AM – 11:00 AM hora Colombia (UTC-5) → 7:00–16:00 UTC
  SL: 8.0 pips | TP: 16.0 pips | RR 1:2
================================================
"""

from datetime import datetime, timezone, timedelta
import math
import json
import os

# ── Horario operativo Colombia (UTC-5) ──────────────────────────────────────
# 02:00 AM Colombia = 07:00 UTC
# 11:00 AM Colombia = 16:00 UTC
TRADE_HOUR_START_UTC = 7   # 02:00 AM Colombia
TRADE_HOUR_END_UTC   = 16  # 11:00 AM Colombia


class CapitalGuard:
    # SL y TP fijos para todas las fases (en pips)
    SL_PIPS = 8.0
    TP_PIPS = 16.0

    # ── Riesgos por fase (WDC agresivo) ───────────────────────────────────
    RIESGO_CRECIMIENTO   = 0.10   # 10% — acelerador a fondo
    RIESGO_CONSOLIDACION = 0.05   # 5%  — frenado táctico
    RIESGO_ESCUDO        = 0.01   # 1%  — modo protección

    # ── Stop-loss diario: máximo 3 pérdidas consecutivas al 10% ≈ 27% capital
    MAX_DAILY_LOSS_PCT   = 0.27

    def __init__(self):
        self._file_path = "capital_state.json"
        self._operations: list[dict] = self._load_state()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _colombia_hour(self) -> int:
        """Hora actual en Colombia (UTC-5)."""
        return (self._now() - timedelta(hours=5)).hour

    def _load_state(self) -> list[dict]:
        if not os.path.exists(self._file_path):
            return []
        try:
            with open(self._file_path, "r") as f:
                data = json.load(f)
            now = self._now()
            threshold = now - timedelta(days=7)
            ops = []
            for d in data:
                ts = datetime.fromisoformat(d["ts"])
                if ts >= threshold:
                    ops.append({"pnl": d["pnl"], "ts": ts})
            return ops
        except Exception as e:
            print(f"Error cargando estado de capital: {e}")
            return []

    def _save_state(self):
        try:
            data = [{"pnl": o["pnl"], "ts": o["ts"].isoformat()} for o in self._operations]
            with open(self._file_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error guardando estado: {e}")

    def record(self, pnl: float):
        self._operations.append({
            "pnl": pnl,
            "ts":  self._now(),
        })
        self._save_state()

    def pnl_day(self) -> float:
        start = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    def pnl_week(self) -> float:
        now   = self._now()
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    def _get_base_and_target(self, capital_activo: float):
        bases = [50, 100, 200, 400, 800, 1600, 3200, 6400, 12800]
        base = 50
        for b in bases:
            if capital_activo >= b:
                base = b
            else:
                break
        return base, base * 2

    def _is_trading_hours(self) -> bool:
        """Verifica si estamos dentro del horario Colombia: 2 AM – 11 AM."""
        hora_co = self._colombia_hour()
        return TRADE_HOUR_START_UTC - 5 <= hora_co < TRADE_HOUR_END_UTC - 5
        # Equivalente: 2 <= hora_co < 11

    def should_trade(self, capital_activo: float) -> tuple[bool, str]:
        p_day  = self.pnl_day()
        now    = self._now()
        hora_co = self._colombia_hour()

        # 1) Horario Colombia
        if not (2 <= hora_co < 11):
            return False, f"Fuera de horario Colombia ({hora_co:02d}:xx) — opera solo 2:00–11:00 AM"

        # 2) Cierre de viernes tarde
        is_friday_late = now.weekday() == 4 and hora_co >= 11
        if is_friday_late:
            return False, "Cierre de viernes — stop after 11:00 AM Colombia"

        # 3) Stop-loss diario
        max_daily_loss = capital_activo * self.MAX_DAILY_LOSS_PCT
        if p_day <= -max_daily_loss:
            return False, f"Max loss diario alcanzado (${-p_day:.2f} / límite ${max_daily_loss:.2f})"

        return True, "OK — dentro de horario y límites"

    def get_phase(self, capital_activo: float) -> tuple[str, float]:
        """
        Retorna (nombre_fase, porcentaje_riesgo) según PnL del día.
        Objetivo diario = base / 5 → +20% diario para duplicar en 5 días.
        """
        base, target    = self._get_base_and_target(capital_activo)
        daily_target    = base * 0.20   # +20% diario = duplicar en 5 días
        p_day           = self.pnl_day()
        now             = self._now()
        is_friday_late  = now.weekday() == 4 and self._colombia_hour() >= 11

        if is_friday_late:
            return "ESCUDO (CIERRE VIERNES)", self.RIESGO_ESCUDO

        # Meta diaria alcanzada → proteger ganancias
        if p_day >= daily_target:
            return "ESCUDO", self.RIESGO_ESCUDO

        # Mitad de la meta diaria → consolidar
        if p_day >= (daily_target * 0.50):
            return "CONSOLIDACION", self.RIESGO_CONSOLIDACION

        # Bajo la mitad → máxima aceleración
        return "CRECIMIENTO", self.RIESGO_CRECIMIENTO

    def status_text(self, capital_activo: float) -> str:
        base, target     = self._get_base_and_target(capital_activo)
        daily_target     = base * 0.20
        p_day            = self.pnl_day()
        p_week           = self.pnl_week()
        phase, riesgo    = self.get_phase(capital_activo)
        can_trade, reason = self.should_trade(capital_activo)
        hora_co          = self._colombia_hour()

        # Lote dinámico: lote = (capital * riesgo) / (SL_pips * $10/pip por lote estándar)
        lote_calc    = (capital_activo * riesgo) / (self.SL_PIPS * 10)
        lote_sugerido = max(0.01, round(lote_calc, 2))

        return (
            f"=== ESTADO WDC — MODO AGRESIVO ===\n"
            f"  Hora Colombia       : {hora_co:02d}:xx (operativo 02–11 AM)\n"
            f"  Capital Activo      : ${capital_activo:.2f} (Base: ${base} → Objetivo: ${target})\n"
            f"  PNL Día             : ${p_day:.2f} (Meta diaria +20%: ${daily_target:.2f})\n"
            f"  PNL Semana          : ${p_week:.2f}\n"
            f"  Fase Actual         : {phase}\n"
            f"  Riesgo por operación: {riesgo*100:.0f}%\n"
            f"  Lote Sugerido       : {lote_sugerido}\n"
            f"  SL={self.SL_PIPS} pips | TP={self.TP_PIPS} pips | RR 1:2\n"
            f"  Puede operar        : {'SI' if can_trade else 'NO'} — {reason}\n"
            f"  Stop-loss diario    : {self.MAX_DAILY_LOSS_PCT*100:.0f}% del capital\n"
        )
