"""
ai_backtest.py
Backtest AI-driven que combina:
  - El filtro técnico existente (signal_engine.evaluate_confluence para WDC,
    o el detector de pullback CSM) como pre-filtro.
  - Una llamada a AIAnalyst.analyze() en cada candidato técnico, con
    contexto realista (sentimiento OANDA + capital + fase + historial).
  - Captura completa de la decisión IA por trade (action, reason, lot, SL, TP)
    para inspección posterior.

Modos:
  - run_ai_wdc(...)  → corre WDC clásico con IA decidiendo.
  - run_ai_csm(...)  → corre CSM Sniper con IA confirmando cada pullback.

Estrategia anti-coste:
  - La IA solo se invoca cuando el filtro técnico ya autorizó (no se quema API
    en mercado lateral o sin patrón).
  - `max_ai_calls` corta el experimento si se exceden N llamadas.

Sentimiento:
  - OandaClient consulta PositionBook real (≤90 días) o devuelve simulado.
  - El campo `sentiment_source` del trade indica el origen.

Si OPENAI_API_KEY no está configurado o se pasa `dry_run=True`, la IA usa un
mock determinista (`MockAIAnalyst`) que aprueba la decisión técnica con
SL/TP del par. Útil para smoke tests sin gastar API ni red.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

# Imports tempranos para evitar resolución dinámica defectuosa
from .signal_engine import evaluate_confluence, simulate_sentiment
from .pair_config import PAIR_SPECS
from .csm_pair_specs import CSM_PAIR_SPECS
from .strategies.csm_sniper import (
    find_pullback_entry, pick_pair_of_week, is_friday_close_time,
)

# NEWS_PROB se define localmente para no depender de backtest_runner
NEWS_PROB = 0.008

logger = logging.getLogger(__name__)


# ────────────────────────── Mock AI para tests ─────────────────────────
class MockAIAnalyst:
    """
    Doble determinista de AIAnalyst para smoke tests sin gastar API.

    Comportamiento: aprueba la acción técnica que se le pasa por
    `tech_action` en el contexto de market_context, con SL/TP del par.
    Si tech_action == HOLD, devuelve HOLD.
    """
    def __init__(self, default_lot: float = 0.01):
        self.default_lot = default_lot
        self.calls = 0

    def analyze(
        self,
        symbol: str,
        candles: dict,
        history: list,
        open_positions: list,
        pinecone_context: str = "",
        capital_status: str = "",
        market_context: str = "",
        pending_orders: list = None,
        myfxbook_sentiment: dict | None = None,
        phase_context: str | None = None,
    ) -> dict:
        self.calls += 1
        # Extraer la acción técnica del market_context inyectado
        tech_action = "HOLD"
        if "TECH_ACTION=BUY" in market_context:
            tech_action = "BUY"
        elif "TECH_ACTION=SELL" in market_context:
            tech_action = "SELL"

        if tech_action == "HOLD":
            return {"action": "HOLD", "reason": "[mock] técnico=HOLD"}

        return {
            "action": tech_action,
            "lot": self.default_lot,
            "sl_pips": 8.0,
            "tp_pips": 16.0,
            "reason": f"[mock] confirma técnico {tech_action}",
        }


# ────────────────────────── helpers de contexto ────────────────────────
def _build_phase_context(capital: float, capital_initial: float, risk_pct: float) -> str:
    """String que refuerza la fase al modelo (evita drift prompt↔código)."""
    progress = (capital - capital_initial) / capital_initial
    if progress >= 0.5:
        phase = "ESCUDO (capital >50% sobre inicial — riesgo reducido)"
        risk_eff = max(0.01, risk_pct * 0.20)
    elif progress >= 0.20:
        phase = "CONSOLIDACION (entre +20% y +50%)"
        risk_eff = max(0.02, risk_pct * 0.60)
    else:
        phase = "CRECIMIENTO (capital <120% del inicial)"
        risk_eff = risk_pct
    return (
        f"Fase: {phase}\n"
        f"Capital actual: ${capital:.2f} (inicial ${capital_initial:.2f})\n"
        f"Riesgo sugerido por trade: {risk_eff*100:.1f}%\n"
        f"Recuerda: respeta SL_PIPS / TP_PIPS del par."
    )


def _build_capital_status(capital: float, capital_initial: float, trades_today: int) -> str:
    pnl_pct = (capital - capital_initial) / capital_initial * 100
    return (
        f"Capital: ${capital:.2f} | "
        f"PnL acumulado: {pnl_pct:+.1f}% | "
        f"Trades hoy: {trades_today}"
    )


def _slice_candles_at(df: pd.DataFrame, ts: pd.Timestamp, n: int = 50) -> pd.DataFrame:
    """Últimas n velas con cierre <= ts."""
    if df.empty:
        return df
    idx = df.index.searchsorted(ts, side="right") - 1
    start = max(0, idx - n + 1)
    return df.iloc[start: idx + 1]


# ── P3: SL/TP dinámicos por ATR M15 ─────────────────────────────────
def _atr_sl_tp_pips(m15: pd.DataFrame, pip_size: float, period: int = 14, mult_sl: float = 1.5, rr: float = 2.0) -> tuple[float, float] | None:
    """
    Calcula SL/TP en pips a partir del ATR(period) de las últimas velas M15.
    Retorna None si no hay suficientes velas.
    """
    if len(m15) < period + 1:
        return None
    high = m15["High"]
    low = m15["Low"]
    close_prev = m15["Close"].shift(1)
    tr = pd.concat([
        (high - low),
        (high - close_prev).abs(),
        (low - close_prev).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period, min_periods=period).mean().iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None
    sl_pips = max(8.0, round((atr * mult_sl) / pip_size, 1))
    tp_pips = sl_pips * rr
    return sl_pips, tp_pips


# ── P5: filtro horas Londres+NY ─────────────────────────────────────
def _is_active_session(ts: pd.Timestamp) -> bool:
    """Devuelve True si la vela cae en horas Londres+NY (8-16 UTC)."""
    return 8 <= ts.hour < 16


def _simulate_trade_outcome(
    m15: pd.DataFrame,
    entry_ts: pd.Timestamp,
    side: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    horizon_hours: int = 72,
) -> dict:
    """
    Simula el outcome del trade vela por vela (M15) hasta horizon_hours.
    Si la vela contiene SL y TP, asume worst-case (SL primero).
    """
    end_ts = entry_ts + pd.Timedelta(hours=horizon_hours)
    after = m15.loc[(m15.index > entry_ts) & (m15.index <= end_ts)]
    for ts, row in after.iterrows():
        h, l = float(row["High"]), float(row["Low"])
        if side == "BUY":
            hit_sl = l <= sl_price
            hit_tp = h >= tp_price
            if hit_sl:
                return {"exit_price": sl_price, "exit_ts": ts, "reason": "SL", "won": False}
            if hit_tp:
                return {"exit_price": tp_price, "exit_ts": ts, "reason": "TP", "won": True}
        else:
            hit_sl = h >= sl_price
            hit_tp = l <= tp_price
            if hit_sl:
                return {"exit_price": sl_price, "exit_ts": ts, "reason": "SL", "won": False}
            if hit_tp:
                return {"exit_price": tp_price, "exit_ts": ts, "reason": "TP", "won": True}

    if not after.empty:
        last = after.iloc[-1]
        close = float(last["Close"])
        won = (side == "BUY" and close > entry_price) or (side == "SELL" and close < entry_price)
        return {"exit_price": close, "exit_ts": last.name, "reason": "TIMEOUT", "won": won}
    return {"exit_price": entry_price, "exit_ts": entry_ts, "reason": "NO_DATA", "won": False}


# ────────────────────────── runner WDC + IA ────────────────────────────
def run_ai_wdc(
    multi_frames: dict[str, dict[str, pd.DataFrame]],
    initial_capital: float,
    risk_pct: float,
    ai_analyst,
    seed: int = 42,
    max_ai_calls: int = 200,
    eval_every_n_bars: int = 16,  # 16 velas M15 = 4 horas
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Backtest WDC + IA, sin sentimiento externo.

    En cada salto de `eval_every_n_bars` velas M15 (default 4h):
      1. signal_engine.evaluate_confluence → action_técnico
      2. Si action_técnico != HOLD:
         - Llama AIAnalyst.analyze(...) con `myfxbook_sentiment=None`
           (la IA verá "Sentimiento Myfxbook: no disponible").
         - Si IA aprueba (BUY/SELL), simula trade con sus parámetros.
      3. Cap: una posición a la vez.

    Devuelve (trades_df, decisions_log).
    decisions_log incluye también las decisiones HOLD (para auditoría).
    """
    rng = np.random.default_rng(seed)

    pairs = list(multi_frames.keys())
    # Timeline unificado
    indices = [f["M15"].index for f in multi_frames.values() if not f["M15"].empty]
    if not indices:
        raise ValueError("multi_frames vacío.")
    timeline = indices[0]
    for ix in indices[1:]:
        timeline = timeline.union(ix)
    timeline = timeline.sort_values()

    capital = float(initial_capital)
    trades = []
    decisions = []
    open_trade = None  # {"symbol", "side", "sl", "tp", "entry_price", "entry_ts", "lot", "spec"}
    ai_calls_used = 0

    # Iterar cada N barras
    for i in range(0, len(timeline), eval_every_n_bars):
        ts = timeline[i]

        # P5: filtrar fuera de sesiones activas Londres+NY (8-16 UTC).
        # No se salta si hay trade abierto (lo simulamos hasta cerrarse).
        skip_for_session = not _is_active_session(ts)

        # Si hay trade abierto, ver si cerró
        if open_trade:
            sym = open_trade["symbol"]
            m15 = multi_frames[sym]["M15"]
            after = m15.loc[(m15.index > open_trade["entry_ts"]) & (m15.index <= ts)]
            for t2, r2 in after.iterrows():
                h, l = float(r2["High"]), float(r2["Low"])
                if open_trade["side"] == "BUY":
                    if l <= open_trade["sl"]:
                        _close_trade(open_trade, open_trade["sl"], t2, "SL", trades, capital_ref=capital)
                        capital += trades[-1]["pnl_usd"]
                        open_trade = None; break
                    if h >= open_trade["tp"]:
                        _close_trade(open_trade, open_trade["tp"], t2, "TP", trades, capital_ref=capital)
                        capital += trades[-1]["pnl_usd"]
                        open_trade = None; break
                else:
                    if h >= open_trade["sl"]:
                        _close_trade(open_trade, open_trade["sl"], t2, "SL", trades, capital_ref=capital)
                        capital += trades[-1]["pnl_usd"]
                        open_trade = None; break
                    if l <= open_trade["tp"]:
                        _close_trade(open_trade, open_trade["tp"], t2, "TP", trades, capital_ref=capital)
                        capital += trades[-1]["pnl_usd"]
                        open_trade = None; break

        if open_trade is not None:
            continue
        if skip_for_session:
            continue
        if capital < 1.0:
            logger.info(f"[AI-WDC] Capital agotado en {ts}.")
            break
        if ai_calls_used >= max_ai_calls:
            logger.info(f"[AI-WDC] max_ai_calls={max_ai_calls} alcanzado en {ts}.")
            break

        # Probar cada par
        for sym in pairs:
            spec = PAIR_SPECS.get(sym)
            if spec is None:
                continue
            frames = multi_frames[sym]
            m15 = _slice_candles_at(frames["M15"], ts, n=200)
            h1 = _slice_candles_at(frames["H1"], ts, n=200)
            h4 = _slice_candles_at(frames["H4"], ts, n=200)
            if len(m15) < 50 or len(h1) < 50 or len(h4) < 50:
                continue

            # Filtro técnico
            sentiment_sim = simulate_sentiment(rng, symbol=sym)
            news = rng.random() < NEWS_PROB
            tech_action, tech_reason, levels = evaluate_confluence(
                m15=m15, h1=h1, h4=h4,
                sentiment=sentiment_sim, av_score=0.0,
                news_block=news,
                trend_threshold=spec["trend_threshold"],
                symbol=sym,
                strict=True,   # P1: H4/H1 deben ser ambos no NEUTRAL
            )

            if tech_action == "HOLD":
                continue

            # Construir contexto para la IA — sin sentimiento externo
            phase_ctx = _build_phase_context(capital, initial_capital, risk_pct)
            cap_status = _build_capital_status(capital, initial_capital, trades_today=len(trades))
            market_ctx = (
                f"TECH_ACTION={tech_action}\n"
                f"TECH_REASON={tech_reason}\n"
                f"TECH_LEVELS={levels}"
            )

            ai_calls_used += 1
            decision = ai_analyst.analyze(
                symbol=sym,
                candles={"M15": m15, "H1": h1, "H4": h4},
                history=[],            # backtest sin Notion live
                open_positions=[],
                pinecone_context="",
                capital_status=cap_status,
                market_context=market_ctx,
                pending_orders=None,
                myfxbook_sentiment=None,   # IA verá "no disponible"
                phase_context=phase_ctx,
            )

            decisions.append({
                "ts": ts, "symbol": sym, "tech_action": tech_action,
                "tech_reason": tech_reason,
                "ai_action": decision.get("action"),
                "ai_reason": decision.get("reason"),
                "ai_lot": decision.get("lot"),
                "ai_sl_pips": decision.get("sl_pips"),
                "ai_tp_pips": decision.get("tp_pips"),
                "capital": capital,
            })

            ai_action = decision.get("action", "HOLD")
            if ai_action not in ("BUY", "SELL"):
                continue

            # P3: SL/TP basados en ATR de M15 (volatilidad del momento).
            # Si la IA dicta sus propios pips, los respetamos; si no, ATR manda.
            atr_pair = _atr_sl_tp_pips(m15, spec["pip_size"])
            if atr_pair is not None:
                atr_sl, atr_tp = atr_pair
            else:
                atr_sl, atr_tp = spec["sl_pips"], spec["tp_pips"]

            ai_sl = decision.get("sl_pips")
            ai_tp = decision.get("tp_pips")
            sl_pips = float(ai_sl) if ai_sl else atr_sl
            tp_pips = float(ai_tp) if ai_tp else atr_tp

            ai_lot = float(decision.get("lot", 0.01))
            risk_lot = (capital * risk_pct) / (sl_pips * spec["pip_value_per_lot"])
            lot = max(0.01, min(5.0, max(ai_lot, risk_lot)))

            entry_price = float(m15.iloc[-1]["Close"])
            if ai_action == "BUY":
                sl_price = entry_price - sl_pips * spec["pip_size"]
                tp_price = entry_price + tp_pips * spec["pip_size"]
            else:
                sl_price = entry_price + sl_pips * spec["pip_size"]
                tp_price = entry_price - tp_pips * spec["pip_size"]

            open_trade = {
                "symbol": sym, "side": ai_action,
                "entry_price": entry_price, "entry_ts": ts,
                "sl": sl_price, "tp": tp_price,
                "sl_pips": sl_pips, "tp_pips": tp_pips,
                "lot": round(lot, 2),
                "pip_size": spec["pip_size"],
                "pip_value_per_lot": spec["pip_value_per_lot"],
                "tech_action": tech_action,
                "ai_action": ai_action,
                "ai_reason": decision.get("reason", ""),
                "capital_start": round(capital, 2),
            }
            break  # un solo par por iteración

    # Cerrar trade al final del histórico si quedó abierto
    if open_trade:
        sym = open_trade["symbol"]
        m15 = multi_frames[sym]["M15"]
        last = m15.iloc[-1]
        close = float(last["Close"])
        _close_trade(open_trade, close, last.name, "EOH", trades, capital_ref=capital)
        capital += trades[-1]["pnl_usd"]

    return pd.DataFrame(trades), decisions


def _close_trade(open_trade: dict, exit_price: float, exit_ts, reason: str, trades: list, capital_ref: float):
    """Calcula PnL y appendea a la lista trades."""
    if open_trade["side"] == "BUY":
        diff = exit_price - open_trade["entry_price"]
    else:
        diff = open_trade["entry_price"] - exit_price
    pips = diff / open_trade["pip_size"]
    pnl = pips * open_trade["lot"] * open_trade["pip_value_per_lot"]
    trades.append({
        **{k: v for k, v in open_trade.items() if k != "spec"},
        "exit_price": exit_price,
        "exit_ts": exit_ts,
        "exit_reason": reason,
        "pnl_usd": round(pnl, 2),
        "won": pnl > 0,
        "capital_end": round(capital_ref + pnl, 2),
        "risk_pct": (open_trade["sl_pips"] * open_trade["pip_value_per_lot"] * open_trade["lot"]) / capital_ref if capital_ref > 0 else 0,
    })


# ────────────────────────── runner CSM + IA ────────────────────────────
def run_ai_csm(
    multi_frames: dict[str, dict[str, pd.DataFrame]],
    initial_capital: float,
    risk_pct: float,
    ai_analyst,
    seed: int = 42,
    max_ai_calls: int = 200,
    excluded_currencies: set[str] | None = None,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Backtest CSM Sniper + IA, sin sentimiento externo.
    Cada lunes selecciona par; busca pullback intra-semana; cuando lo
    encuentra consulta a la IA con `myfxbook_sentiment=None`. Si la IA
    aprueba, simula trade. Una sola operación por semana.
    """
    from .csm_runner import _week_iterator, _simulate_outcome, _pnl_usd

    capital = float(initial_capital)
    trades = []
    decisions = []
    ai_calls_used = 0

    indices = [f["M15"].index for f in multi_frames.values() if not f["M15"].empty]
    if not indices:
        raise ValueError("multi_frames sin datos M15.")
    timeline = indices[0]
    for ix in indices[1:]:
        timeline = timeline.union(ix)
    timeline = timeline.sort_values()

    h4_data = {sym: f["H4"] for sym, f in multi_frames.items() if "H4" in f}
    weeks = _week_iterator(timeline)
    logger.info(f"[AI-CSM] semanas={len(weeks)} | risk={risk_pct*100:.1f}% | max_ai={max_ai_calls}")

    for monday, week_end in weeks:
        if capital < 1.0:
            break
        if ai_calls_used >= max_ai_calls:
            break

        pick = pick_pair_of_week(h4_data, monday, excluded_currencies)
        if pick is None:
            continue
        sym = pick.pair_symbol
        frames = multi_frames.get(sym)
        if not frames or frames.get("M15", pd.DataFrame()).empty:
            continue
        spec = CSM_PAIR_SPECS[sym]
        m15 = frames["M15"]
        h1 = frames.get("H1", pd.DataFrame())
        if h1.empty:
            continue

        # Buscar pullback técnico
        window = m15.loc[(m15.index >= monday) & (m15.index <= week_end)]
        entry_signal = None
        for ts in window.index:
            entry_signal = find_pullback_entry(
                m15_df=m15, h1_df=h1, direction=pick.direction,
                pip_size=spec["pip_size"], pip_value_per_lot=spec["pip_value_per_lot"],
                capital=capital, risk_pct=risk_pct, asof=ts,
            )
            if entry_signal is not None or is_friday_close_time(ts):
                break
        if entry_signal is None:
            continue

        # Llamada a la IA — sin sentimiento externo
        sliced = {
            "M15": _slice_candles_at(m15, entry_signal.timestamp, n=80),
            "H1": _slice_candles_at(h1, entry_signal.timestamp, n=80),
            "H4": _slice_candles_at(frames["H4"], entry_signal.timestamp, n=80),
        }
        ai_calls_used += 1
        market_ctx = (
            f"TECH_ACTION={entry_signal.side}\n"
            f"TECH_REASON=CSM pullback ({pick.strongest} fuerte vs {pick.weakest} debil)"
        )
        decision = ai_analyst.analyze(
            symbol=sym,
            candles=sliced,
            history=[],
            open_positions=[],
            capital_status=_build_capital_status(capital, initial_capital, len(trades)),
            market_context=market_ctx,
            myfxbook_sentiment=None,    # IA verá "no disponible"
            phase_context=_build_phase_context(capital, initial_capital, risk_pct),
        )

        decisions.append({
            "ts": entry_signal.timestamp, "symbol": sym,
            "tech_action": entry_signal.side,
            "ai_action": decision.get("action"),
            "ai_reason": decision.get("reason"),
            "ai_lot": decision.get("lot"),
            "ai_sl_pips": decision.get("sl_pips"),
            "ai_tp_pips": decision.get("tp_pips"),
            "strongest": pick.strongest, "weakest": pick.weakest,
            "capital": capital,
        })

        ai_action = decision.get("action", "HOLD")
        if ai_action not in ("BUY", "SELL") or ai_action != entry_signal.side:
            continue

        # Override IA: respetar lote/SL/TP si los entrega; sino usar técnico
        sl_pips = float(decision.get("sl_pips", entry_signal.sl_pips))
        tp_pips = float(decision.get("tp_pips", entry_signal.tp_pips))
        if ai_action == "BUY":
            sl = entry_signal.entry_price - sl_pips * spec["pip_size"]
            tp = entry_signal.entry_price + tp_pips * spec["pip_size"]
        else:
            sl = entry_signal.entry_price + sl_pips * spec["pip_size"]
            tp = entry_signal.entry_price - tp_pips * spec["pip_size"]
        entry_signal.sl_price = sl
        entry_signal.tp_price = tp
        entry_signal.sl_pips = sl_pips
        entry_signal.tp_pips = tp_pips

        outcome = _simulate_outcome(m15, entry_signal, week_end)
        pnl = _pnl_usd(entry_signal, outcome, spec["pip_size"], spec["pip_value_per_lot"])

        trades.append({
            "week_start": monday, "symbol": sym,
            "side": entry_signal.side,
            "strongest": pick.strongest, "weakest": pick.weakest,
            "entry_ts": entry_signal.timestamp, "exit_ts": outcome["exit_ts"],
            "entry_price": entry_signal.entry_price, "exit_price": outcome["exit_price"],
            "sl_price": sl, "tp_price": tp,
            "sl_pips": sl_pips, "tp_pips": tp_pips,
            "pip_size": spec["pip_size"], "pip_value_per_lot": spec["pip_value_per_lot"],
            "lot": entry_signal.lot,
            "pnl_usd": round(pnl, 2),
            "exit_reason": outcome["reason"],
            "won": outcome["won"],
            "capital_start": round(capital, 2),
            "capital_end": round(capital + pnl, 2),
            "risk_pct": risk_pct,
            "ai_action": decision.get("action"),
            "ai_reason": decision.get("reason"),
        })
        capital = max(0.0, capital + pnl)

    return pd.DataFrame(trades), decisions
