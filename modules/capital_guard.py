"""
capital_guard.py
================================================
Guardián de capital progresivo.
Calcula el P&L diario, semanal y mensual
y determina si el bot debe seguir operando
o detenerse para proteger las ganancias.
================================================
"""

from datetime import datetime, timezone, timedelta


class CapitalGuard:
    """
    Rastrea el P&L en tres horizontes (día, semana, mes)
    y expone:
      - should_trade()  → bool: ¿está permitido operar ahora?
      - status_text()   → str:  resumen para inyectar en el prompt
    """

    # Objetivos configurables
    DAILY_TARGET   = 18.0
    WEEKLY_TARGET  = 125.0
    MONTHLY_TARGET = 500.0

    # Límites de pérdida (porcentaje del objetivo)
    DAILY_STOP_LOSS_PCT   = 0.50   # detiene si pierde >50% del objetivo diario
    WEEKLY_STOP_LOSS_PCT  = 0.48   # detiene si pierde >48% del objetivo semanal

    def __init__(self):
        self._operations: list[dict] = []   # {"pnl": float, "ts": datetime}

    # ── Registro ─────────────────────────────────────────────────────────────

    def record(self, pnl: float):
        """Registra el resultado (USD) de una operación cerrada."""
        self._operations.append({
            "pnl": pnl,
            "ts":  datetime.now(timezone.utc),
        })

    # ── Cálculos de P&L ──────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def pnl_day(self) -> float:
        """P&L acumulado desde medianoche UTC de hoy."""
        start = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    def pnl_week(self) -> float:
        """P&L acumulado desde el lunes de la semana actual (UTC)."""
        now   = self._now()
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    def pnl_month(self) -> float:
        """P&L acumulado desde el día 1 del mes actual (UTC)."""
        start = self._now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return sum(o["pnl"] for o in self._operations if o["ts"] >= start)

    # ── Lógica de protección ─────────────────────────────────────────────────

    def should_trade(self) -> tuple[bool, str]:
        """
        Retorna (puede_operar: bool, motivo: str).
        Evalúa protecciones en cascada: día → semana → mes.
        """
        day   = self.pnl_day()
        week  = self.pnl_week()
        month = self.pnl_month()

        daily_stop  = self.DAILY_TARGET  * self.DAILY_STOP_LOSS_PCT
        weekly_stop = self.WEEKLY_TARGET * self.WEEKLY_STOP_LOSS_PCT

        # 1. Protección diaria por pérdida excesiva
        if day < 0 and abs(day) > daily_stop:
            return False, (
                f"🛑 DAILY STOP: pérdida diaria ${abs(day):.2f} supera límite ${daily_stop:.2f}. "
                f"Operaciones suspendidas hasta mañana."
            )

        # 2. Protección semanal por pérdida excesiva
        if week < 0 and abs(week) > weekly_stop:
            return False, (
                f"🛑 WEEKLY STOP: pérdida semanal ${abs(week):.2f} supera límite ${weekly_stop:.2f}. "
                f"Operaciones suspendidas hasta el lunes."
            )

        # 3. Objetivo diario alcanzado: continúa pero en modo protección
        if day >= self.DAILY_TARGET:
            return True, (
                f"✅ Objetivo diario alcanzado: +${day:.2f}. "
                f"Modo protección: solo señales de alta convicción."
            )

        # 4. Objetivo semanal alcanzado: continúa en modo protección
        if week >= self.WEEKLY_TARGET:
            return True, (
                f"✅ Objetivo semanal alcanzado: +${week:.2f}. "
                f"Modo protección: no perder las ganancias semanales."
            )

        # 5. Objetivo mensual alcanzado: continúa en modo protección
        if month >= self.MONTHLY_TARGET:
            return True, (
                f"✅ Objetivo mensual alcanzado: +${month:.2f}. "
                f"Modo protección: no perder los ${self.MONTHLY_TARGET:.0f} consolidados."
            )

        # 6. Normal: operar con normalidad
        return True, (
            f"🟢 Capital OK | Día: +${day:.2f}/${self.DAILY_TARGET} | "
            f"Semana: +${week:.2f}/${self.WEEKLY_TARGET} | "
            f"Mes: +${month:.2f}/${self.MONTHLY_TARGET}"
        )

    def status_text(self) -> str:
        """
        Genera el bloque de texto de estado que se inyecta
        directamente en el user_message del prompt.
        """
        can, reason = self.should_trade()
        day   = self.pnl_day()
        week  = self.pnl_week()
        month = self.pnl_month()

        return (
            f"=== ESTADO DE CAPITAL (Capital Guard) ===\n"
            f"  pnl_dia:     ${day:.2f}  (objetivo: ${self.DAILY_TARGET})\n"
            f"  pnl_semana:  ${week:.2f}  (objetivo: ${self.WEEKLY_TARGET})\n"
            f"  pnl_mes:     ${month:.2f}  (objetivo: ${self.MONTHLY_TARGET})\n"
            f"  puede_operar: {'SI' if can else 'NO — ' + reason}\n"
            f"  estado:      {reason}\n"
        )
