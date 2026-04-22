"""
backtest_runner.py
Ejecuta la simulacion vela por vela sobre datos M15 historicos.
Aplica logica de fases de capital (capital_guard) del prompt.md.

v5 (2026-04-22) — Multi-par capital compartido:
- Soporta EURUSD + GBPUSD + USDJPY con un solo pool de capital.
- Cada par tiene su propio pip_size, SL/TP, pip_value, sentimiento y threshold.
- Loop unificado: combina las velas M15 de todos los pares en una timeline
  unica, ordena por timestamp, y para cada vela evalua confluencia del par.
- Capital compartido: 1 variable `capital` global.
- Circuit breakers (diario + semanal) son GLOBALES (no por par).
- MAX_TRADES_PER_DAY y MAX_TRADES_SIMULTANEOUS son GLOBALES.
"""
import datetime
import numpy as np
import pandas as pd
from tqdm import tqdm
from .signal_engine import evaluate_confluence, simulate_av_score
from .pair_config import PAIR_SPECS, DEFAULT_PAIRS

# ── Constantes globales de gestion ────────────────────────────────────
RISK_PCT = {"CRECIMIENTO": 0.05, "CONSOLIDACION": 0.03, "ESCUDO": 0.01}

MAX_TRADES_SIMULTANEOUS = {"CRECIMIENTO": 1, "CONSOLIDACION": 1, "ESCUDO": 1}
MAX_TRADES_PER_DAY      = 3       # global entre todos los pares
DAILY_STOP_PCT          = -0.06

# Circuit breaker DIARIO — 3 SL consecutivos pausa el dia
MAX_CONSECUTIVE_SL = 3

# Circuit breaker SEMANAL — 5 SL en 7 dias pausa la semana
MAX_SL_WEEK = 5

# Horario Colombia (UTC)
TRADE_HOUR_START_UTC = 7
TRADE_HOUR_END_UTC   = 16

NEWS_PROB = 0.008

# Lookback FIJO para H1/H4
H1_LOOKBACK = 100
H4_LOOKBACK = 100


def _get_phase(capital: float, capital_week_start: float, pnl_day: float) -> str:
    pct_towards_daily = (capital - capital_week_start) / (capital_week_start * 0.15 + 1e-9)
    if pnl_day / (capital_week_start + 1e-9) <= DAILY_STOP_PCT:
        return "STOP_DIA"
    if pct_towards_daily >= 1.0:
        return "ESCUDO"
    if pct_towards_daily >= 0.5:
        return "CONSOLIDACION"
    return "CRECIMIENTO"


def _lot_size(capital: float, risk_pct: float, sl_pips: float, pip_value: float) -> float:
    """Calcula tamaño de lote basado en riesgo, SL y pip_value del par."""
    lot = (capital * risk_pct) / (sl_pips * pip_value)
    return round(max(lot, 0.01), 2)


def _pip_to_usd(pips: float, lot: float, pip_value: float) -> float:
    """Convierte pips a USD usando el pip_value_per_lot del par."""
    return pips * lot * pip_value


def _next_monday(date) -> datetime.date:
    """Devuelve la fecha del lunes siguiente a 'date'."""
    days_ahead = 7 - date.weekday()
    return date + datetime.timedelta(days=days_ahead)


def sentiment_for_backtest(rng: np.random.Generator, base_long_pct: float = 62.0) -> dict:
    """
    Sentimiento realista para backtest.
    Usa el sesgo historico del retail con variacion gaussiana +-10%.
    base_long_pct viene del PAIR_SPECS del par correspondiente.
    """
    noise     = rng.normal(0, 10)
    long_pct  = float(np.clip(base_long_pct + noise, 30, 85))
    short_pct = 100.0 - long_pct
    return {"short_pct": round(short_pct, 1), "long_pct": round(long_pct, 1)}


def run_backtest(
    multi_frames: dict[str, dict[str, pd.DataFrame]],
    initial_capital: float = 50.0,
    lookback: int = 60,
    seed: int = 42,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Backtest multi-par con capital compartido.

    Combina las velas M15 de todos los pares en una timeline unica,
    itera vela por vela en orden temporal, y evalua confluencia para
    el par de cada vela. El capital, circuit breakers y limites de
    trades son GLOBALES.

    Args:
        multi_frames: {"EURUSD": {"M15": df, "H1": df, "H4": df}, ...}
        initial_capital: capital inicial en USD
        lookback: velas de historia M15 para signal_engine
        seed: semilla para RNG
        symbols: lista de pares a operar (default: todos en multi_frames)

    Returns:
        DataFrame con todos los trades de todos los pares.
    """
    if symbols is None:
        symbols = list(multi_frames.keys())

    rng = np.random.default_rng(seed)

    # ── Preparar timeline unificada ──────────────────────────────────
    # Combinar M15 de todos los pares con columna _symbol
    all_m15_parts = []
    per_pair_data = {}  # {"EURUSD": {"m15": df, "h1": df, "h4": df, "spec": dict}, ...}

    for symbol in symbols:
        if symbol not in multi_frames:
            print(f"[Backtest] WARN: {symbol} no tiene datos, saltando.")
            continue
        spec = PAIR_SPECS.get(symbol)
        if spec is None:
            print(f"[Backtest] WARN: {symbol} no tiene PAIR_SPECS, saltando.")
            continue

        frames = multi_frames[symbol]
        m15 = frames["M15"]
        h1  = frames["H1"]
        h4  = frames["H4"]

        if m15.empty:
            print(f"[Backtest] WARN: {symbol} M15 vacio, saltando.")
            continue

        # Crear copia con columna _symbol para la timeline unificada
        m15_tagged = m15.copy()
        m15_tagged["_symbol"] = symbol
        all_m15_parts.append(m15_tagged)

        per_pair_data[symbol] = {
            "m15_full": m15,
            "h1_full": h1,
            "h4_full": h4,
            "spec": spec,
        }

    if not all_m15_parts:
        print("[Backtest] ERROR: No hay datos M15 para ningun par.")
        return pd.DataFrame()

    # Timeline unificada: todas las velas M15 de todos los pares, ordenadas
    unified = pd.concat(all_m15_parts).sort_index()
    active_symbols = list(per_pair_data.keys())

    # ── Estado global ────────────────────────────────────────────────
    capital            = initial_capital
    capital_week_start = initial_capital
    trades             = []

    pnl_day      = 0.0
    trades_today = 0
    last_date    = None
    last_week    = None

    # Circuit breaker DIARIO
    consecutive_sl      = 0
    sl_pause_until_date = None

    # Circuit breaker SEMANAL
    sl_week_dates      = []
    weekly_pause_until = None

    active_exit_times = []
    start_idx = max(lookback, 60)

    # Para acceso por indice posicional en cada par, necesitamos mapear
    # la posicion de cada vela en la timeline unificada a su posicion
    # en el M15 original del par.
    # Pero es mas eficiente iterar con iloc y usar el timestamp para
    # hacer slices del H1/H4.

    total_velas = len(unified)
    if total_velas <= start_idx:
        print("[Backtest] ERROR: Muy pocas velas en timeline unificada.")
        return pd.DataFrame()

    print("[Backtest] " + "=" * 58)
    print(f"[Backtest] Multi-par capital compartido: {', '.join(active_symbols)}")
    print(f"[Backtest] Timeline unificada: {total_velas - start_idx} velas M15")
    print(f"[Backtest] Capital inicial: ${initial_capital:.2f}")
    print(f"[Backtest] Limites: max {MAX_TRADES_PER_DAY} trades/dia (global), "
          f"max {MAX_TRADES_SIMULTANEOUS['CRECIMIENTO']} simultaneo")
    print(f"[Backtest] Horario: {TRADE_HOUR_START_UTC}:00 - {TRADE_HOUR_END_UTC}:00 UTC")
    print(f"[Backtest] CB diario: {MAX_CONSECUTIVE_SL} SL consecutivos | "
          f"CB semanal: {MAX_SL_WEEK} SL en 7 dias")
    for sym in active_symbols:
        s = per_pair_data[sym]["spec"]
        print(f"[Backtest]   {sym}: SL={s['sl_pips']}p TP={s['tp_pips']}p "
              f"pip={s['pip_size']} threshold={s['trend_threshold']}")
    print("[Backtest] " + "=" * 58)

    for i in tqdm(range(start_idx, total_velas), desc="Simulando velas"):
        row = unified.iloc[i]
        ts  = unified.index[i]
        symbol = row["_symbol"]

        pair_data = per_pair_data[symbol]
        spec      = pair_data["spec"]
        m15_full  = pair_data["m15_full"]
        h1_full   = pair_data["h1_full"]
        h4_full   = pair_data["h4_full"]

        # Encontrar la posicion de este timestamp en el M15 original del par
        # para poder hacer slices correctos
        try:
            pair_idx = m15_full.index.get_loc(ts)
        except KeyError:
            continue

        if isinstance(pair_idx, slice):
            pair_idx = pair_idx.stop - 1
        if not isinstance(pair_idx, (int, np.integer)):
            continue

        if pair_idx < lookback:
            continue

        m15_w = m15_full.iloc[pair_idx - lookback: pair_idx + 1].copy()
        h1_w  = h1_full[h1_full.index <= ts].iloc[-H1_LOOKBACK:].copy()
        h4_w  = h4_full[h4_full.index <= ts].iloc[-H4_LOOKBACK:].copy()

        if len(h1_w) < 10 or len(h4_w) < 5:
            continue

        current_date = ts.date()
        current_week = ts.isocalendar()[:2]

        # ── Reset diario ────────────────────────────────────────────
        if last_date != current_date:
            pnl_day             = 0.0
            trades_today        = 0
            last_date           = current_date
            consecutive_sl      = 0
            sl_pause_until_date = None

        # ── Reset semanal ────────────────────────────────────────────
        if last_week != current_week:
            capital_week_start = capital
            last_week          = current_week
            weekly_pause_until = None

        # ── Horario Colombia ─────────────────────────────────────────
        if not (TRADE_HOUR_START_UTC <= ts.hour < TRADE_HOUR_END_UTC):
            continue

        # ── Fase de capital ──────────────────────────────────────────
        phase = _get_phase(capital, capital_week_start, pnl_day)
        if phase == "STOP_DIA":
            continue

        # Viernes despues de 17:00 UTC
        if ts.weekday() == 4 and ts.hour >= 17:
            continue

        # Limite trades por dia (GLOBAL)
        if trades_today >= MAX_TRADES_PER_DAY:
            continue

        # ── CB diario ────────────────────────────────────────────────
        if sl_pause_until_date is not None and current_date <= sl_pause_until_date:
            continue

        # ── CB semanal ───────────────────────────────────────────────
        cutoff = current_date - datetime.timedelta(days=7)
        sl_week_dates = [d for d in sl_week_dates if d > cutoff]

        if weekly_pause_until is not None and current_date < weekly_pause_until:
            continue

        # Limite trades simultaneos (GLOBAL)
        active_exit_times = [t for t in active_exit_times if t > ts]
        max_simultaneous  = MAX_TRADES_SIMULTANEOUS.get(phase, 1)
        if len(active_exit_times) >= max_simultaneous:
            continue

        # ── Inputs externos ──────────────────────────────────────────
        news_block = rng.random() < NEWS_PROB
        sentiment  = sentiment_for_backtest(rng, base_long_pct=spec["sentiment_base_long"])
        av_score   = simulate_av_score(rng)

        # ── Evaluar confluencia (con threshold del par) ──────────────
        action, reason, conf_levels = evaluate_confluence(
            m15_w, h1_w, h4_w, sentiment, av_score, news_block,
            trend_threshold=spec["trend_threshold"],
        )

        if action == "HOLD":
            continue

        # ── Ejecutar trade ───────────────────────────────────────────
        sl_pips     = spec["sl_pips"]
        tp_pips     = spec["tp_pips"]
        pip_size    = spec["pip_size"]
        pip_value   = spec["pip_value_per_lot"]

        risk_pct    = RISK_PCT.get(phase, 0.02)
        lot         = _lot_size(capital, risk_pct, sl_pips, pip_value)
        entry_price = m15_full.iloc[pair_idx]["Close"]

        if action == "BUY":
            sl_price = entry_price - sl_pips * pip_size
            tp_price = entry_price + tp_pips * pip_size
        else:
            sl_price = entry_price + sl_pips * pip_size
            tp_price = entry_price - tp_pips * pip_size

        # Avanzar velas hasta SL o TP (en el M15 del par)
        outcome    = None
        exit_idx   = pair_idx
        exit_price = entry_price
        max_future = min(pair_idx + 200, len(m15_full))

        for j in range(pair_idx + 1, max_future):
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

        # ── PnL ──────────────────────────────────────────────────────
        if action == "BUY":
            pips_result = (exit_price - entry_price) / pip_size
        else:
            pips_result = (entry_price - exit_price) / pip_size

        pnl_usd  = _pip_to_usd(pips_result, lot, pip_value)
        capital  += pnl_usd
        pnl_day  += pnl_usd

        # ── Actualizar circuit breakers (GLOBALES) ───────────────────
        if outcome == "SL":
            consecutive_sl += 1
            if consecutive_sl >= MAX_CONSECUTIVE_SL:
                sl_pause_until_date = current_date
                print(f"[CB-Diario]  {ts.date()} {symbol} — {consecutive_sl} SL consecutivos -> pausa hoy")

            sl_week_dates.append(current_date)
            if len(sl_week_dates) >= MAX_SL_WEEK and weekly_pause_until is None:
                weekly_pause_until = _next_monday(current_date)
                print(f"[CB-Semanal] {ts.date()} {symbol} — {len(sl_week_dates)} SL en 7 dias "
                      f"-> pausa hasta {weekly_pause_until}")
        else:
            consecutive_sl = 0

        exit_timestamp = m15_full.index[exit_idx]
        active_exit_times.append(exit_timestamp)
        trades_today += 1

        duration_candles = exit_idx - pair_idx

        trades.append({
            "symbol":           symbol,
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
            print(f"[Backtest] RUINA total en {ts} ({symbol})")
            break

    df_trades = pd.DataFrame(trades)
    print("[Backtest] " + "=" * 58)
    print(f"[Backtest] Trades totales: {len(df_trades)}")
    if len(df_trades) > 0:
        wins = (df_trades["pnl_usd"] > 0).sum()
        print(f"[Backtest] Win rate: {wins/len(df_trades)*100:.1f}%")
        print(f"[Backtest] Capital final: ${capital:.2f}")
        # Desglose por par
        for sym in active_symbols:
            sym_df = df_trades[df_trades["symbol"] == sym]
            if len(sym_df) > 0:
                sym_wins = (sym_df["pnl_usd"] > 0).sum()
                sym_pnl  = sym_df["pnl_usd"].sum()
                print(f"[Backtest]   {sym}: {len(sym_df)} trades | "
                      f"WR={sym_wins/len(sym_df)*100:.1f}% | PnL=${sym_pnl:.2f}")
    print("[Backtest] " + "=" * 58)
    return df_trades
