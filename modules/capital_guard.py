"""
capital_guard.py
================================================
Guardian de capital progresivo — Estrategia WDC Hibrida
Weekly Double Compounding: $50 -> $100 -> $200 ...

Version WDC HIGH WR — TP reducido para maximizar Win Rate:
  Configuracion anterior:  WR~48.6%, PF=1.73, SL=8p, TP=16p, RR 1:2
  Configuracion actual:    WR>52%+,  SL=8p, TP=12p, RR 1:1.5

  Razon del cambio: al acercar el TP de 16 a 12 pips, el precio lo
  alcanza significativamente mas veces antes de revertirse, subiendo
  el Win Rate por encima del 50%. Con un 34% WR ya serías rentable
  con RR 1:1.5, por lo que cualquier WR > 40% es ganancia neta.

  Fase CRECIMIENTO  : 5% de riesgo por operacion  <- SWEET SPOT MC
  Fase CONSOLIDACION: 3% de riesgo (gano >50% del objetivo diario)
  Fase ESCUDO       : 1% de riesgo (meta diaria alcanzada o viernes)

  Horario operativo : 2:00 AM - 11:00 AM hora Colombia (UTC-5)
  SL: 8.0 pips | TP: 12.0 pips | RR 1:1.5

  MATH CHECK 5% (nuevo TP=12p):
    - Capital $50 | lote = $50*5% / (8*$10) = 0.03
    - Ganancia por TP: 0.03 * 12 * $10 = $3.60 (+7.2%)
    - Perdida por SL : 0.03 * 8  * $10 = $2.40 (-4.8%)
    - Para duplicar a $100: necesitas net +$50
    - Con RR 1:1.5: WR breakeven = 40% | con >52% WR = rentable
================================================
"""

from datetime import datetime, timezone, timedelta
import math
import json
import os

# -- Horario operativo Colombia (UTC-5) ------------------------------------
TRADE_HOUR_START_UTC = 7    # 02:00 AM Colombia
TRADE_HOUR_END_UTC   = 16   # 11:00 AM Colombia


class CapitalGuard:
    # SL y TP fijos para todas las fases (en pips)
    # TP reducido 16->12 para aumentar Win Rate (RR 1:2 -> 1:1.5)
    SL_PIPS = 8.0
    TP_PIPS = 12.0

    # -- Riesgos por fase (Sweet Spot confirmado por MC) -------------------
    RIESGO_CRECIMIENTO   = 0.05   # 5%  <- Sweet Spot: 90.1% duplican, 2% ruina
    RIESGO_CONSOLIDACION = 0.03   # 3%  <- frenado tactico
    RIESGO_ESCUDO        = 0.01   # 1%  <- proteccion maxima

    # -- Stop-loss diario: 3 SL al 5% = -15% capital (conservador)
    MAX_DAILY_LOSS_PCT   = 0.20   # 20% del capital -> corta el dia

    # -- Racha maxima de SL consecutivos antes de pausar
    MAX_CONSECUTIVE_SL   = 3     # 3 SL seguidos = pausa por hoy

    def __init__(self):
        self._file_path = "capital_state.json"
        self._operations: list[dict] = self._load_state()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _colombia_hour(self) -> int:
        return (self._now() - timedelta(hours=5)).hour

    def _load_state(self) -> list[dict]:
        if not os.path.exists(self._file_path):
            return []
        try:
            with open(self._file_path, "r") as f:
                data = json.load(f)
            now = self._now()
            threshold = now - timedelta(days=7)
            return [
                {"pnl": d["pnl"], "ts": datetime.fromisoformat(d["ts"])}
                for d in data
                if datetime.fromisoformat(d["ts"]) >= threshold
            ]
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
        self._operations.append({"pnl": pnl, "ts": self._now()})
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

    def _consecutive_sl_today(self) -> int:
        """Racha de SL consecutivos desde el ultimo TP del dia."""
        start = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
        ops_today = [o for o in self._operations if o["ts"] >= start]
        streak = 0
        for op in reversed(ops_today):
            if op["pnl"] < 0:
                streak += 1
            else:
                break
        return streak

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
        p_day   = self.pnl_day()
        now     = self._now()
        hora_co = self._colombia_hour()

        # 1) Horario Colombia 2–11 AM
        if not (2 <= hora_co < 11):
            return False, f"Fuera de horario Colombia ({hora_co:02d}:xx) — opera solo 2:00–11:00 AM"

        # 2) Cierre viernes
        if now.weekday() == 4 and hora_co >= 11:
            return False, "Cierre de viernes — stop after 11:00 AM Colombia"

        # 3) Stop-loss diario 20%
        max_daily_loss = capital_activo * self.MAX_DAILY_LOSS_PCT
        if p_day <= -max_daily_loss:
            return False, f"Max loss diario alcanzado (${-p_day:.2f} / limite ${max_daily_loss:.2f})"

        # 4) Racha de SL consecutivos
        sl_streak = self._consecutive_sl_today()
        if sl_streak >= self.MAX_CONSECUTIVE_SL:
            return False, f"Racha {sl_streak} SL consecutivos — pausa por hoy (max {self.MAX_CONSECUTIVE_SL})"

        return True, "OK — dentro de horario y limites"

    def get_phase(self, capital_activo: float) -> tuple[str, float]:
        """
        Fases segun PnL del dia vs objetivo diario (+20% = duplicar en 5 dias).
        """
        base, _      = self._get_base_and_target(capital_activo)
        daily_target = base * 0.20
        p_day        = self.pnl_day()
        now          = self._now()

        if now.weekday() == 4 and self._colombia_hour() >= 11:
            return "ESCUDO (CIERRE VIERNES)", self.RIESGO_ESCUDO

        if p_day >= daily_target:
            return "ESCUDO", self.RIESGO_ESCUDO

        if p_day >= daily_target * 0.50:
            return "CONSOLIDACION", self.RIESGO_CONSOLIDACION

        return "CRECIMIENTO", self.RIESGO_CRECIMIENTO

    def status_text(self, capital_activo: float) -> str:
        base, target      = self._get_base_and_target(capital_activo)
        daily_target      = base * 0.20
        p_day             = self.pnl_day()
        p_week            = self.pnl_week()
        phase, riesgo     = self.get_phase(capital_activo)
        can_trade, reason = self.should_trade(capital_activo)
        hora_co           = self._colombia_hour()
        sl_streak         = self._consecutive_sl_today()

        # Lote dinamico
        lote_calc     = (capital_activo * riesgo) / (self.SL_PIPS * 10)
        lote_sugerido = max(0.01, round(lote_calc, 2))

        # Ganancia por TP y TPs necesarios para la meta (TP=12p, RR 1:1.5)
        ganancia_tp    = lote_sugerido * self.TP_PIPS * 10
        restante       = max(0.0, daily_target - p_day)
        tps_necesarios = math.ceil(restante / ganancia_tp) if ganancia_tp > 0 else float("inf")

        # Progreso hacia meta semanal
        progreso_semana = (p_week / (base)) * 100  # % del camino a duplicar

        return (
            f"=== ESTADO WDC — SWEET SPOT 5% (MC: 90.1% duplicar | 2% ruina) ===\n"
            f"  Hora Colombia       : {hora_co:02d}:xx (operativo 02–11 AM)\n"
            f"  Capital Activo      : ${capital_activo:.2f} (Base: ${base} -> Objetivo: ${target})\n"
            f"  Progreso semana     : {progreso_semana:.1f}% hacia ${target}\n"
            f"  PNL Dia             : ${p_day:+.2f} (Meta diaria +20%: ${daily_target:.2f})\n"
            f"  PNL Semana          : ${p_week:+.2f}\n"
            f"  Fase Actual         : {phase}\n"
            f"  Riesgo por operacion: {riesgo*100:.0f}%\n"
            f"  Lote Sugerido       : {lote_sugerido}\n"
            f"  Ganancia por TP     : ${ganancia_tp:.2f} (RR 1:1.5)\n"
            f"  TPs para meta dia   : {tps_necesarios}\n"
            f"  Racha SL hoy        : {sl_streak}/{self.MAX_CONSECUTIVE_SL}\n"
            f"  SL={self.SL_PIPS}p | TP={self.TP_PIPS}p | RR 1:1.5\n"
            f"  Puede operar        : {'SI' if can_trade else 'NO'} — {reason}\n"
        )
