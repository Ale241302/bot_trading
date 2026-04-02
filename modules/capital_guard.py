"""
capital_guard.py
================================================
Guardián de capital progresivo — Estrategia ASM
Objetivos realistas ajustados a $50 capital:
  Diario  : $9   (~90 pips con 0.01 lotes, alcanzable)
  Semanal : $63  (7 días x $9)
  Mensual : $250 (rendimiento del 400% — alto pero posible)

Lógica "The Shield":
  - Al alcanzar el objetivo, activa modo blindaje.
  - Si el P&L retrocede por debajo del objetivo, STOP.
  - Límite de pérdida diaria: $4.50 (50% del objetivo)
  - Límite de pérdida semanal: $30 (48% del objetivo)
================================================
"""

from datetime import datetime, timezone, timedelta


class CapitalGuard:
    """
    Rastrea el P&L en tres horizontes (día, semana, mes)
    y expone:
      - should_trade()       → (bool, str): ¿puede operar?
      - status_text()        → str: resumen para el prompt
      - get_max_sl_pips()    → float: SL máximo permitido en pips
      - get_tp_pips()        → float: TP sugerido (ratio 1:2)
    """

    # ── Objetivos ──────────────────────────────────────────────────────────
    DAILY_TARGET   =  9.0    # $9/día   → 90 pips con 0.01 lotes
    WEEKLY_TARGET  = 63.0    # $63/semana
    MONTHLY_TARGET = 250.0   # $250/mes → rendimiento 400% sobre $50

    # ── Protección de ganancias (The Shield) ───────────────────────────────
    # Si después de alcanzar el objetivo el P&L cae por debajo → STOP
    DAILY_SHIELD_FLOOR   = DAILY_TARGET    # no perder los $9 ganados
    WEEKLY_SHIELD_FLOOR  = WEEKLY_TARGET   # no perder los $63 ganados
    MONTHLY_SHIELD_FLOOR = MONTHLY_TARGET  # no perder los $250 ganados

    # ── Límites de pérdida (antes de alcanzar el objetivo) ─────────────────
    DAILY_MAX_LOSS   = 4.50   # 50% del objetivo diario
    WEEKLY_MAX_LOSS  = 30.0   # 48% del objetivo semanal

    # ── Parámetros técnicos (0.01 lotes EURUSD, 1 pip = $0.10) ────────────
    # SL fijo máximo: 15 pips = $1.50 por operación
    # TP fijo: 30 pips = $3.00 → ratio 1:2
    SL_PIPS_DEFAULT = 15.0
    TP_PIPS_DEFAULT = 30.0
    # Modo blindaje (objetivo alcanzado): SL más ajustado
    SL_PIPS_SHIELD  = 10.0
    TP_PIPS_SHIELD  = 20.0

    def __init__(self):
        self._operations: list[dict] = []  # {"pnl": float, "ts": datetime}
        self._shield_day_activated   = False
        self._shield_week_activated  = False
        self._shield_month_activated = False

    # ── Registro ──────────────────────────────────────────────────────────

    def record(self, pnl: float):
        """Registra el resultado (USD) de una operación cerrada."""
        self._operations.append({
            "pnl": pnl,
            "ts":  datetime.now(timezone.utc),
        })
        # Activar escudos si se alcanza objetivo
        if self.pnl_day()   >= self.DAILY_TARGET:   self._shield_day_activated   = True
        if self.pnl_week()  >= self.WEEKLY_TARGET:  self._shield_week_activated  = True
        if self.pnl_month() >= self.MONTHLY_TARGET: self._shield_month_activated = True

    # ── Cálculos P&L ──────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def pnl_day(self) -> float:
        start = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    def pnl_week(self) -> float:
        now   = self._now()
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    def pnl_month(self) -> float:
        start = self._now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    # ── Lógica principal ──────────────────────────────────────────────────

    def should_trade(self) -> tuple[bool, str]:
        """
        Evalúa protecciones en cascada: día → semana → mes.
        Retorna (puede_operar, motivo).
        """
        day   = self.pnl_day()
        week  = self.pnl_week()
        month = self.pnl_month()

        # ── STOPS por pérdida excesiva (antes de alcanzar objetivo) ──
        if day < 0 and abs(day) >= self.DAILY_MAX_LOSS:
            return False, (
                f"🛑 DAILY STOP: pérdida ${abs(day):.2f} ≥ límite ${self.DAILY_MAX_LOSS:.2f}. "
                f"Suspendido hasta mañana."
            )

        if week < 0 and abs(week) >= self.WEEKLY_MAX_LOSS:
            return False, (
                f"🛑 WEEKLY STOP: pérdida ${abs(week):.2f} ≥ límite ${self.WEEKLY_MAX_LOSS:.2f}. "
                f"Suspendido hasta el lunes."
            )

        # ── SHIELD: objetivo alcanzado y ahora está retrocediendo ──
        if self._shield_day_activated and day < self.DAILY_SHIELD_FLOOR:
            return False, (
                f"🛡️ DAILY SHIELD: objetivo alcanzado pero P&L cayó a ${day:.2f}. "
                f"Protegiendo los ${self.DAILY_SHIELD_FLOOR:.0f}. Suspendido hasta mañana."
            )

        if self._shield_week_activated and week < self.WEEKLY_SHIELD_FLOOR:
            return False, (
                f"🛡️ WEEKLY SHIELD: objetivo alcanzado pero P&L cayó a ${week:.2f}. "
                f"Protegiendo los ${self.WEEKLY_SHIELD_FLOOR:.0f}. Suspendido hasta el lunes."
            )

        if self._shield_month_activated and month < self.MONTHLY_SHIELD_FLOOR:
            return False, (
                f"🛡️ MONTHLY SHIELD: objetivo alcanzado pero P&L cayó a ${month:.2f}. "
                f"Protegiendo los ${self.MONTHLY_SHIELD_FLOOR:.0f}. Suspendido hasta el mes próximo."
            )

        # ── Modo blindaje activo (objetivo alcanzado, operando con excedente) ──
        if self._shield_day_activated:
            return True, (
                f"✅ SHIELD ACTIVO (día): +${day:.2f}. "
                f"Solo señales de alta convicción. SL ajustado a {self.SL_PIPS_SHIELD} pips."
            )

        if self._shield_week_activated:
            return True, (
                f"✅ SHIELD ACTIVO (semana): +${week:.2f}. "
                f"Solo señales de alta convicción. SL ajustado a {self.SL_PIPS_SHIELD} pips."
            )

        if self._shield_month_activated:
            return True, (
                f"✅ SHIELD ACTIVO (mes): +${month:.2f}. "
                f"Solo señales de alta convicción. SL ajustado a {self.SL_PIPS_SHIELD} pips."
            )

        # ── Normal: operando hacia el objetivo ──
        pct_day = (day / self.DAILY_TARGET * 100) if self.DAILY_TARGET else 0
        return True, (
            f"🟢 Normal | Día: ${day:.2f}/{self.DAILY_TARGET} ({pct_day:.0f}%) | "
            f"Semana: ${week:.2f}/{self.WEEKLY_TARGET} | "
            f"Mes: ${month:.2f}/{self.MONTHLY_TARGET}"
        )

    def is_shield_mode(self) -> bool:
        """True si algún objetivo ya fue alcanzado (opera con excedente)."""
        return (
            self._shield_day_activated or
            self._shield_week_activated or
            self._shield_month_activated
        )

    def get_sl_pips(self) -> float:
        """SL recomendado en pips según el modo actual."""
        return self.SL_PIPS_SHIELD if self.is_shield_mode() else self.SL_PIPS_DEFAULT

    def get_tp_pips(self) -> float:
        """TP recomendado en pips (ratio 1:2 siempre)."""
        return self.TP_PIPS_SHIELD if self.is_shield_mode() else self.TP_PIPS_DEFAULT

    def status_text(self) -> str:
        """Bloque de texto listo para inyectar en el user_message del prompt."""
        can, reason = self.should_trade()
        day   = self.pnl_day()
        week  = self.pnl_week()
        month = self.pnl_month()
        sl    = self.get_sl_pips()
        tp    = self.get_tp_pips()

        return (
            f"=== ESTADO DE CAPITAL (Capital Guard / ASM) ===\n"
            f"  pnl_dia:        ${day:.2f}  (objetivo: ${self.DAILY_TARGET})\n"
            f"  pnl_semana:     ${week:.2f}  (objetivo: ${self.WEEKLY_TARGET})\n"
            f"  pnl_mes:        ${month:.2f}  (objetivo: ${self.MONTHLY_TARGET})\n"
            f"  modo_escudo:    {'ACTIVO' if self.is_shield_mode() else 'inactivo'}\n"
            f"  SL_recomendado: {sl} pips (${sl * 0.10:.2f})\n"
            f"  TP_recomendado: {tp} pips (${tp * 0.10:.2f}) — ratio 1:2\n"
            f"  puede_operar:   {'SI' if can else 'NO'}\n"
            f"  estado:         {reason}\n"
        )
