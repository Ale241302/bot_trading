"""
capital_guard.py
================================================
Guardián de capital progresivo — Estrategia WDC Híbrida
Weekly Double Compounding:
- Duplicar el capital semanalmente (50 -> 100 -> 200).
- Lote sugerido dinámico por fase.

Cambios versión híbrida:
  SL: 3.5 pips  ->  8.0 pips  (absorbe spread retail + slippage + latencia GPT)
  TP: 7.5 pips  -> 16.0 pips  (mantiene RR 1:2 estricto)
================================================
"""

from datetime import datetime, timezone, timedelta
import math
import json
import os

class CapitalGuard:
    # SL y TP fijos para todas las fases (en pips) — Estrategia Híbrida
    SL_PIPS = 8.0
    TP_PIPS = 16.0

    def __init__(self):
        self._file_path = "capital_state.json"
        self._operations: list[dict] = self._load_state()
        self.is_friday_closing = False

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

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

    def should_trade(self, capital_activo: float) -> tuple[bool, str]:
        p_day = self.pnl_day()
        now = self._now()
        is_friday_late = now.weekday() == 4 and now.hour >= 17

        max_daily_loss = capital_activo * 0.06
        if p_day <= -max_daily_loss:
            return False, f"Max loss diario superado (-{max_daily_loss:.2f})"
        elif is_friday_late:
            return False, "Cierre de viernes > 17:00 UTC."
        else:
            return True, "Condiciones normales"

    def status_text(self, capital_activo: float) -> str:
        base, target = self._get_base_and_target(capital_activo)
        daily_target = base / 5.0
        p_day = self.pnl_day()
        p_week = self.pnl_week()

        now = self._now()
        is_friday_late = now.weekday() == 4 and now.hour >= 17

        # Determinar Fase (SL/TP son fijos en todas las fases)
        if is_friday_late:
            phase = "ESCUDO (CIERRE TOTAL VIERNES)"
            riesgo = 0.01
        elif p_day >= daily_target:
            phase = "ESCUDO"
            riesgo = 0.01
        elif p_day >= (daily_target * 0.5):
            phase = "CONSOLIDACION"
            riesgo = 0.015
        else:
            phase = "CRECIMIENTO"
            riesgo = 0.02

        sl_pips = self.SL_PIPS
        tp_pips = self.TP_PIPS

        can_trade, reason = self.should_trade(capital_activo)

        # Lote dinámico: lote = (capital_activo * riesgo) / (sl_pips * 10)
        lote_calc = (capital_activo * riesgo) / (sl_pips * 10)
        lote_sugerido = max(0.01, round(lote_calc, 2))

        return (
            f"=== ESTADO WDC (ESTRATEGIA HÍBRIDA) ===\n"
            f"  Capital Activo  : ${capital_activo:.2f} (Base semanal: ${base}, Objetivo: ${target})\n"
            f"  PNL Día         : ${p_day:.2f} (Objetivo diario: ${daily_target:.2f})\n"
            f"  PNL Semana      : ${p_week:.2f}\n"
            f"  Fase Actual     : {phase}\n"
            f"  Riesgo Permitido: {riesgo*100}%\n"
            f"  Lote Sugerido   : {lote_sugerido}\n"
            f"  SL={sl_pips} pips, TP={tp_pips} pips (RR 1:2)\n"
            f"  Puede operar    : {'SI' if can_trade else 'NO'} ({reason})\n"
        )
