"""
csm_runner.py
Motor de simulación de la estrategia "Francotirador de Fuerza Relativa
con Compounding Agresivo".

Loop semanal:
  - Cada lunes 00:00 UTC: pick_pair_of_week(...) sobre H4.
  - Mar-Jue (y resto del lunes): scan M15 buscando pullback hasta que
    haya UNA entrada o llegue viernes 21:00 UTC.
  - Ejecutado el trade: simulación intra-vela hasta SL/TP/cierre forzado.
  - Solo UN trade por semana. Tras SL o TP, se espera al lunes siguiente.
  - Recálculo de capital al cerrar el trade.

Salida: DataFrame de trades con todas las columnas necesarias para el
reporte y el Monte Carlo (pnl_usd, sl_pips, tp_pips, pip_value, side,
symbol, entry/exit timestamps, capital_start/end).
"""
from __future__ import annotations

import logging
from dataclasses import asdict

import pandas as pd

from .csm_pair_specs import CSM_PAIR_SPECS
from .strategies.csm_sniper import (
    EntrySignal,
    WeekPick,
    find_pullback_entry,
    is_friday_close_time,
    pick_pair_of_week,
)

logger = logging.getLogger(__name__)


def _simulate_outcome(
    m15_df: pd.DataFrame,
    entry: EntrySignal,
    week_end: pd.Timestamp,
) -> dict:
    """
    Simula vela por vela qué pasa con la operación: si el SL o TP se tocan
    intra-vela, asumimos worst-case para el broker (SL primero si la vela
    contiene ambos, sesgo conservador) — práctica estándar de backtesting.

    Retorna dict con resultado, exit_price, exit_ts, exit_reason.
    """
    # Velas estrictamente DESPUÉS de la señal y hasta el cierre semanal
    after = m15_df.loc[(m15_df.index > entry.timestamp) & (m15_df.index <= week_end)]
    for ts, row in after.iterrows():
        high = float(row["High"])
        low = float(row["Low"])

        if entry.side == "BUY":
            hit_sl = low <= entry.sl_price
            hit_tp = high >= entry.tp_price
            if hit_sl and hit_tp:
                # Worst-case: SL primero
                return {"exit_price": entry.sl_price, "exit_ts": ts, "reason": "SL", "won": False}
            if hit_sl:
                return {"exit_price": entry.sl_price, "exit_ts": ts, "reason": "SL", "won": False}
            if hit_tp:
                return {"exit_price": entry.tp_price, "exit_ts": ts, "reason": "TP", "won": True}
        else:  # SELL
            hit_sl = high >= entry.sl_price
            hit_tp = low <= entry.tp_price
            if hit_sl and hit_tp:
                return {"exit_price": entry.sl_price, "exit_ts": ts, "reason": "SL", "won": False}
            if hit_sl:
                return {"exit_price": entry.sl_price, "exit_ts": ts, "reason": "SL", "won": False}
            if hit_tp:
                return {"exit_price": entry.tp_price, "exit_ts": ts, "reason": "TP", "won": True}

        if is_friday_close_time(ts):
            close = float(row["Close"])
            if entry.side == "BUY":
                won = close > entry.entry_price
            else:
                won = close < entry.entry_price
            return {"exit_price": close, "exit_ts": ts, "reason": "FRIDAY_CLOSE", "won": won}

    # No tocó ningún nivel ni hubo viernes en la ventana → cierre al final
    if not after.empty:
        last_row = after.iloc[-1]
        close = float(last_row["Close"])
        won = (entry.side == "BUY" and close > entry.entry_price) or (
            entry.side == "SELL" and close < entry.entry_price
        )
        return {"exit_price": close, "exit_ts": last_row.name, "reason": "EOW", "won": won}

    return {"exit_price": entry.entry_price, "exit_ts": entry.timestamp, "reason": "NO_DATA", "won": False}


def _pnl_usd(entry: EntrySignal, outcome: dict, pip_size: float, pip_value_per_lot: float) -> float:
    """PnL en USD basado en pips ganados/perdidos."""
    if entry.side == "BUY":
        diff = outcome["exit_price"] - entry.entry_price
    else:
        diff = entry.entry_price - outcome["exit_price"]
    pips = diff / pip_size
    return pips * entry.lot * pip_value_per_lot


def _week_iterator(timeline: pd.DatetimeIndex) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Devuelve lista de (lunes 00:00 UTC, viernes 21:00 UTC) de cada semana
    cubierta por la timeline.
    """
    if len(timeline) == 0:
        return []
    start = timeline.min().tz_convert("UTC")
    end = timeline.max().tz_convert("UTC")
    # Primer lunes <= start
    first_monday = (start - pd.Timedelta(days=start.weekday())).normalize()
    weeks = []
    cur = first_monday
    while cur <= end:
        week_end = cur + pd.Timedelta(days=4, hours=21)  # vie 21:00 UTC
        weeks.append((cur, week_end))
        cur += pd.Timedelta(days=7)
    return weeks


def run_csm_backtest(
    multi_frames: dict[str, dict[str, pd.DataFrame]],
    initial_capital: float = 50.0,
    risk_pct: float = 0.30,
    seed: int = 42,
    excluded_currencies: set[str] | None = None,
) -> tuple[pd.DataFrame, list[WeekPick]]:
    """
    Ejecuta el backtest semanal y devuelve (trades_df, week_picks).

    `multi_frames` debe contener al menos H4 para todos los pares (CSM
    scoring) y M15 + H1 para los pares operables.

    `risk_pct` = fracción del capital arriesgada en cada trade. 0.30 = 30%.
    """
    # Timeline = unión de todos los index M15 (será el "reloj" del bot)
    m15_indices = [
        f["M15"].index for f in multi_frames.values()
        if "M15" in f and not f["M15"].empty
    ]
    if not m15_indices:
        raise ValueError("No hay datos M15 en multi_frames.")
    timeline = m15_indices[0]
    for ix in m15_indices[1:]:
        timeline = timeline.union(ix)
    timeline = timeline.sort_values()

    # Subset H4 para CSM
    h4_data = {sym: f["H4"] for sym, f in multi_frames.items() if "H4" in f}

    # Recorrido semanal
    weeks = _week_iterator(timeline)
    logger.info(f"[CSM] semanas a simular: {len(weeks)} | seed={seed} | risk={risk_pct*100:.0f}%")

    capital = float(initial_capital)
    trades: list[dict] = []
    picks: list[WeekPick] = []

    for monday, week_end in weeks:
        # 1. Selección del par
        pick = pick_pair_of_week(h4_data, monday, excluded_currencies)
        if pick is None:
            continue
        picks.append(pick)

        # 2. Verificar que tenemos M15 + H1 del par elegido
        sym = pick.pair_symbol
        frames = multi_frames.get(sym)
        if frames is None or frames.get("M15", pd.DataFrame()).empty:
            logger.debug(f"[CSM] {monday.date()} → {sym}: sin M15, salto.")
            continue
        h1 = frames.get("H1", pd.DataFrame())
        if h1.empty:
            continue

        spec = CSM_PAIR_SPECS[sym]
        m15 = frames["M15"]

        # 3. Buscar entrada vela por vela (M15) entre lunes y viernes 21:00
        window = m15.loc[(m15.index >= monday) & (m15.index <= week_end)]
        entry: EntrySignal | None = None
        for ts in window.index:
            entry = find_pullback_entry(
                m15_df=m15,
                h1_df=h1,
                direction=pick.direction,
                pip_size=spec["pip_size"],
                pip_value_per_lot=spec["pip_value_per_lot"],
                capital=capital,
                risk_pct=risk_pct,
                asof=ts,
            )
            if entry is not None:
                break
            if is_friday_close_time(ts):
                break

        if entry is None:
            continue  # esta semana no operamos

        # 4. Simular outcome
        outcome = _simulate_outcome(m15, entry, week_end)
        pnl = _pnl_usd(entry, outcome, spec["pip_size"], spec["pip_value_per_lot"])
        capital_end = capital + pnl

        trades.append({
            "week_start": monday,
            "symbol": sym,
            "side": entry.side,
            "strongest": pick.strongest,
            "weakest": pick.weakest,
            "entry_ts": entry.timestamp,
            "exit_ts": outcome["exit_ts"],
            "entry_price": entry.entry_price,
            "exit_price": outcome["exit_price"],
            "sl_price": entry.sl_price,
            "tp_price": entry.tp_price,
            "sl_pips": entry.sl_pips,
            "tp_pips": entry.tp_pips,
            "pip_size": spec["pip_size"],
            "pip_value_per_lot": spec["pip_value_per_lot"],
            "lot": entry.lot,
            "pnl_usd": round(pnl, 2),
            "exit_reason": outcome["reason"],
            "won": outcome["won"],
            "capital_start": round(capital, 2),
            "capital_end": round(capital_end, 2),
            "risk_pct": risk_pct,
        })

        capital = max(0.0, capital_end)
        # Si el capital cae a casi cero, paramos para no producir trades patológicos
        if capital < 1.0:
            logger.info(f"[CSM] Cuenta agotada en {monday.date()}. Detenido.")
            break

    df = pd.DataFrame(trades)
    return df, picks
