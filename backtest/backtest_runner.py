"""
backtest_runner.py
Ejecuta la simulacion vela por vela sobre datos M15 historicos.
Aplica logica de fases de capital (capital_guard) del prompt.md.

Cambios v4 (2026-04-22):
- FIX #1: simulate_sentiment() reemplazado por sentiment_for_backtest().
  El sentimiento ya NO es completamente aleatorio. Se usa una distribucion
  basada en el sesgo historico real del EURUSD retail (60-65% long),
  con variacion ±10% por vela para simular cambios intraday.
- FIX #4: Filtro de horario Colombia aplicado al backtest (7:00-16:00 UTC).
  El bot real no opera fuera de ese rango; el backtest tampoco debe.
- FIX #5: MAX_CONSECUTIVE_SL=3 — circuit breaker igual al CapitalGuard real.
  Si hay 3 SL consecutivos, pausa operaciones hasta el dia siguiente.
  Elimina rachas destructoras (ej: 5-6 SL seguidos de Apr 14-21).
  El contador se resetea al inicio de cada dia o cuando entra un TP.
"""
import numpy as np
import pandas as pd
from tqdm import tqdm
from .signal_engine import evaluate_confluence, simulate_av_score

# ── Constantes estrategia ────────────────────────────────────────────
SL_PIPS  = 8.0
TP_PIPS  = 16.0
PIP_SIZE = 0.0001
RISK_PCT = {"CRECIMIENTO": 0.05, "CONSOLIDACION": 0.03, "ESCUDO": 0.01}

MAX_TRADES_SIMULTANEOUS = {"CRECIMIENTO": 1, "CONSOLIDACION": 1, "ESCUDO": 1}
MAX_TRADES_PER_DAY      = 3
DAILY_STOP_PCT          = -0.06

# FIX #5: Circuit breaker — igual al CapitalGuard del bot real
MAX_CONSECUTIVE_SL = 3   # Pausa el dia si hay 3 SL seguidos

# FIX #4: Horario Colombia — igual al CapitalGuard del bot real
TRADE_HOUR_START_UTC = 7    # 02:00 AM Colombia (UTC-5)
TRADE_HOUR_END_UTC   = 16   # 11:00 AM Colombia (UTC-5)

NEWS_PROB = 0.008


def _get_phase(capital: float, capital_week_start: float, pnl_day: float) -> str:
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
    return pips * lot * 10.0


def sentiment_for_backtest(rng: np.random.Generator, base_long_pct: float = 62.0) -> dict:
    """
    FIX #1: Sentimiento mas realista para backtest EURUSD.

    En lugar de generar valores completamente aleatorios, usa como base
    el sesgo historico real del retail en EURUSD (~60-65% long) con una
    variacion gaussiana ±10% por vela para simular fluctuaciones intraday.

    base_long_pct: Porcentaje base de retail long (default 62% = promedio historico EURUSD).
    """
    noise    = rng.normal(0, 10)
    long_pct = float(np.clip(base_long_pct + noise, 30, 85))
    short_pct = 100.0 - long_pct
    return {"short_pct": round(short_pct, 1), "long_pct": round(long_pct, 1)}


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

    v4:
    - Sentimiento basado en sesgo historico real EURUSD (FIX #1).
    - Horario Colombia 7:00-16:00 UTC aplicado (FIX #4).
    - Circuit breaker MAX_CONSECUTIVE_SL=3 (FIX #5).
    """
    rng = np.random.default_rng(seed)
    m15_full = frames["M15"]
    h1_full  = frames["H1"]
    h4_full  = frames["H4"]

    capital            = initial_capital
    capital_week_start = initial_capital
    trades             = []

    pnl_day           = 0.0
    trades_today      = 0
    last_date         = None
    last_week         = None

    # FIX #5: contador de SL consecutivos
    consecutive_sl        = 0
    sl_pause_until_date   = None   # fecha hasta la que se pausa (None = sin pausa)

    active_exit_times = []

    start_idx = max(lookback, 60)

    print(f"[Backtest] Iniciando simulacion: {len(m15_full) - start_idx} velas M15")
    print(f"[Backtest] Capital inicial: ${initial_capital:.2f}")
    print(f"[Backtest] Limites v4: max {MAX_TRADES_PER_DAY} trades/dia, "
          f"max {MAX_TRADES_SIMULTANEOUS['CRECIMIENTO']} simultaneo")
    print(f"[Backtest] Horario Colombia: {TRADE_HOUR_START_UTC}:00 - {TRADE_HOUR_END_UTC}:00 UTC")
    print(f"[Backtest] Circuit breaker: pausa tras {MAX_CONSECUTIVE_SL} SL consecutivos")

    for i in tqdm(range(start_idx, len(m15_full)), desc="Simulando velas"):
        ts    = m15_full.index[i]
        m15_w = m15_full.iloc[i - lookback: i + 1].copy()

        h1_w = h1_full[h1_full.index <= ts].iloc[-lookback:].copy()
        h4_w = h4_full[h4_full.index <= ts].iloc[-lookback:].copy()

        if len(h1_w) < 10 or len(h4_w) < 5:
            continue

        # Reset contadores de dia / semana
        current_date = ts.date()
        current_week = ts.isocalendar()[:2]

        if last_date != current_date:
            pnl_day           = 0.0
            trades_today      = 0
            last_date         = current_date
            # FIX #5: el circuit breaker se resetea al inicio de cada nuevo dia
            consecutive_sl    = 0
            sl_pause_until_date = None

        if last_week != current_week:
            capital_week_start = capital
            last_week          = current_week

        # FIX #4: Filtro horario Colombia (7:00 - 16:00 UTC)
        if not (TRADE_HOUR_START_UTC <= ts.hour < TRADE_HOUR_END_UTC):
            continue

        # Fase de capital
        phase = _get_phase(capital, capital_week_start, pnl_day)
        if phase == "STOP_DIA":
            continue

        # Viernes despues de 17:00 UTC -> HOLD
        if ts.weekday() == 4 and ts.hour >= 17:
            continue

        # Limite trades por dia
        if trades_today >= MAX_TRADES_PER_DAY:
            continue

        # FIX #5: Circuit breaker activo — saltamos el resto del dia
        if sl_pause_until_date is not None and current_date <= sl_pause_until_date:
            continue

        # Limite trades simultaneos
        active_exit_times = [t for t in active_exit_times if t > ts]
        max_simultaneous  = MAX_TRADES_SIMULTANEOUS.get(phase, 1)
        if len(active_exit_times) >= max_simultaneous:
            continue

        # Inputs externos
        news_block = rng.random() < NEWS_PROB
        sentiment  = sentiment_for_backtest(rng)   # FIX #1
        av_score   = simulate_av_score(rng)

        # Evaluar confluencia
        action, reason, conf_levels = evaluate_confluence(
            m15_w, h1_w, h4_w, sentiment, av_score, news_block
        )

        if action == "HOLD":
            continue

        # Ejecutar trade
        risk_pct = RISK_PCT.get(phase, 0.02)
        lot      = _lot_size(capital, risk_pct)

        entry_price = m15_full.iloc[i]["Close"]
        if action == "BUY":
            sl_price = entry_price - SL_PIPS * PIP_SIZE
            tp_price = entry_price + TP_PIPS * PIP_SIZE
        else:
            sl_price = entry_price + SL_PIPS * PIP_SIZE
            tp_price = entry_price - TP_PIPS * PIP_SIZE

        # Avanzar velas hasta SL o TP
        outcome    = None
        exit_idx   = i
        exit_price = entry_price
        max_future = min(i + 200, len(m15_full))

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
            else:
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
            outcome    = "TIMEOUT"
            exit_idx   = max_future - 1
            exit_price = m15_full.iloc[exit_idx]["Close"]

        # Calcular PnL
        if action == "BUY":
            pips_result = (exit_price - entry_price) / PIP_SIZE
        else:
            pips_result = (entry_price - exit_price) / PIP_SIZE

        pnl_usd = _pip_to_usd(pips_result, lot)
        capital += pnl_usd
        pnl_day += pnl_usd

        # FIX #5: actualizar contador de SL consecutivos
        if outcome == "SL":
            consecutive_sl += 1
            if consecutive_sl >= MAX_CONSECUTIVE_SL:
                # Pausa el resto del dia actual
                sl_pause_until_date = current_date
                print(f"[Backtest] Circuit breaker activado en {ts.date()} "
                      f"tras {consecutive_sl} SL consecutivos — pausando dia")
        else:
            # TP o TIMEOUT resetea el contador
            consecutive_sl = 0

        exit_timestamp = m15_full.index[exit_idx]
        active_exit_times.append(exit_timestamp)
        trades_today += 1

        duration_candles = exit_idx - i

        trades.append({
            "entry_time":       ts,
            "exit_time":        exit_timestamp,
            "action":           action,
            "entry_price":      round(entry_price, 5),
            "exit_price":       round(exit_price, 5),
            "lot":              lot,
            "pips":             round(pips_result, 1),
            "pnl_usd":          round(pnl_usd, 4),
            "capital_after":    round(capital, 4),
            "outcome":          outcome,
            "phase":            phase,
            "duration_candles": duration_candles,
            "duration_hours":   round(duration_candles * 0.25, 2),
            "reason":           reason,
            "sentiment_short":  round(sentiment["short_pct"], 1),
            "av_score":         round(av_score, 3),
            "news_blocked":     news_block,
            "nivel_1":          conf_levels["nivel_1_noticias"],
            "nivel_2":          conf_levels["nivel_2_sentimiento"],
            "nivel_3":          conf_levels["nivel_3_tendencia"],
            "nivel_4":          conf_levels["nivel_4_patron"],
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
