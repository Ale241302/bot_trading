"""
backtest_runner.py
Ejecuta la simulacion vela por vela sobre datos M15 historicos.
Aplica logica de fases de capital (capital_guard) del prompt.md.
"""
import numpy as np
import pandas as pd
from tqdm import tqdm
from .signal_engine import evaluate_confluence, simulate_sentiment, simulate_av_score

# ── Constantes estrategia ────────────────────────────────────────────
SL_PIPS  = 8.0
TP_PIPS  = 16.0
PIP_SIZE = 0.0001       # 1 pip EURUSD
RISK_PCT = {"CRECIMIENTO": 0.02, "CONSOLIDACION": 0.015, "ESCUDO": 0.01}
MAX_TRADES_SIMULTANEOUS = {"CRECIMIENTO": 2, "CONSOLIDACION": 1, "ESCUDO": 1}
DAILY_STOP_PCT = -0.06  # -6% del capital -> HOLD todo el dia

# Probabilidad simulada de noticia HIGH por vela M15 (aprox 2-3 eventos/semana)
NEWS_PROB = 0.008


def _get_phase(capital: float, capital_week_start: float, pnl_day: float) -> str:
    daily_target = capital_week_start * (2 ** (1 / 5) - 1)  # +15% diario aprox
    pct_towards_daily = (capital - capital_week_start) / (capital_week_start * 0.15 + 1e-9)

    if pnl_day / (capital_week_start + 1e-9) <= DAILY_STOP_PCT:
        return "STOP_DIA"
    if pct_towards_daily >= 1.0:
        return "ESCUDO"
    if pct_towards_daily >= 0.5:
        return "CONSOLIDACION"
    return "CRECIMIENTO"


def _lot_size(capital: float, risk_pct: float) -> float:
    lot = (capital * risk_pct) / (SL_PIPS * 10)
    return round(max(lot, 0.01), 2)


def _pip_to_usd(pips: float, lot: float) -> float:
    """Para EURUSD: 1 pip = $10 por lote estandar."""
    return pips * lot * 10.0


def run_backtest(
    frames: dict,
    initial_capital: float = 50.0,
    lookback: int = 60,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Itera vela por vela en M15.
    Para cada vela evalua la confluencia y simula el trade.
    Retorna DataFrame con todas las operaciones ejecutadas.
    """
    rng = np.random.default_rng(seed)
    m15_full = frames["M15"]
    h1_full  = frames["H1"]
    h4_full  = frames["H4"]

    capital           = initial_capital
    capital_week_start = initial_capital
    trades            = []

    pnl_day    = 0.0
    last_date  = None
    last_week  = None

    # Necesitamos al menos `lookback` velas de historia
    start_idx = max(lookback, 60)

    print(f"[Backtest] Iniciando simulacion: {len(m15_full) - start_idx} velas M15")
    print(f"[Backtest] Capital inicial: ${initial_capital:.2f}")

    for i in tqdm(range(start_idx, len(m15_full)), desc="Simulando velas"):
        ts    = m15_full.index[i]
        m15_w = m15_full.iloc[i - lookback: i + 1].copy()

        # Slice H1 y H4 hasta el timestamp actual
        h1_w = h1_full[h1_full.index <= ts].iloc[-lookback:].copy()
        h4_w = h4_full[h4_full.index <= ts].iloc[-lookback:].copy()

        if len(h1_w) < 10 or len(h4_w) < 5:
            continue

        # Reset de contadores de dia / semana
        current_date = ts.date()
        current_week = ts.isocalendar()[:2]  # (year, week)

        if last_date != current_date:
            pnl_day   = 0.0
            last_date = current_date

        if last_week != current_week:
            capital_week_start = capital
            last_week          = current_week

        # Fase de capital
        phase = _get_phase(capital, capital_week_start, pnl_day)
        if phase == "STOP_DIA":
            continue

        # Viernes despues de 17:00 UTC -> HOLD
        if ts.weekday() == 4 and ts.hour >= 17:
            continue

        # Simular inputs externos
        news_block = rng.random() < NEWS_PROB
        sentiment  = simulate_sentiment(rng)
        av_score   = simulate_av_score(rng)

        # Evaluar confluencia
        action, reason, conf_levels = evaluate_confluence(
            m15_w, h1_w, h4_w, sentiment, av_score, news_block
        )

        if action == "HOLD":
            continue

        # Hay senal: simular el trade
        risk_pct = RISK_PCT.get(phase, 0.02)
        lot      = _lot_size(capital, risk_pct)

        entry_price = m15_full.iloc[i]["Close"]
        if action == "BUY":
            sl_price = entry_price - SL_PIPS * PIP_SIZE
            tp_price = entry_price + TP_PIPS * PIP_SIZE
        else:
            sl_price = entry_price + SL_PIPS * PIP_SIZE
            tp_price = entry_price - TP_PIPS * PIP_SIZE

        # Avanzar velas hasta que se toque SL o TP
        outcome   = None
        exit_idx  = i
        exit_price = entry_price
        max_future = min(i + 200, len(m15_full))  # maximo 50 horas

        for j in range(i + 1, max_future):
            future = m15_full.iloc[j]
            if action == "BUY":
                if future["Low"] <= sl_price:
                    outcome    = "SL"
                    exit_price = sl_price
                    exit_idx   = j
                    break
                if future["High"] >= tp_price:
                    outcome    = "TP"
                    exit_price = tp_price
                    exit_idx   = j
                    break
            else:  # SELL
                if future["High"] >= sl_price:
                    outcome    = "SL"
                    exit_price = sl_price
                    exit_idx   = j
                    break
                if future["Low"] <= tp_price:
                    outcome    = "TP"
                    exit_price = tp_price
                    exit_idx   = j
                    break

        if outcome is None:
            # Expiro sin tocar SL/TP -> cerrar al cierre de la ultima vela
            outcome    = "TIMEOUT"
            exit_idx   = max_future - 1
            exit_price = m15_full.iloc[exit_idx]["Close"]

        # Calcular pips y PnL
        if action == "BUY":
            pips_result = (exit_price - entry_price) / PIP_SIZE
        else:
            pips_result = (entry_price - exit_price) / PIP_SIZE

        pnl_usd = _pip_to_usd(pips_result, lot)
        capital += pnl_usd
        pnl_day += pnl_usd

        duration_candles = exit_idx - i

        trades.append({
            "entry_time":        ts,
            "exit_time":         m15_full.index[exit_idx],
            "action":            action,
            "entry_price":       round(entry_price, 5),
            "exit_price":        round(exit_price, 5),
            "lot":               lot,
            "pips":              round(pips_result, 1),
            "pnl_usd":           round(pnl_usd, 4),
            "capital_after":     round(capital, 4),
            "outcome":           outcome,
            "phase":             phase,
            "duration_candles":  duration_candles,
            "duration_hours":    round(duration_candles * 0.25, 2),
            "reason":            reason,
            "sentiment_short":   round(sentiment["short_pct"], 1),
            "av_score":          round(av_score, 3),
            "news_blocked":      news_block,
            "nivel_1":           conf_levels["nivel_1_noticias"],
            "nivel_2":           conf_levels["nivel_2_sentimiento"],
            "nivel_3":           conf_levels["nivel_3_tendencia"],
            "nivel_4":           conf_levels["nivel_4_patron"],
        })

        if capital <= 0:
            print(f"[Backtest] RUINA total en {ts}")
            break

    df_trades = pd.DataFrame(trades)
    print(f"[Backtest] Trades totales: {len(df_trades)}")
    if len(df_trades) > 0:
        wins = (df_trades["pnl_usd"] > 0).sum()
        print(f"[Backtest] Win rate: {wins/len(df_trades)*100:.1f}%")
        print(f"[Backtest] Capital final: ${capital:.2f}")
    return df_trades
