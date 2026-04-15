"""
monte_carlo.py
Simula N escenarios bootstrap sobre el historial de trades.

v3 — Rescalado de PnL al riesgo real del Camino A:
  El backtest historico opera con lote 0.01 (~0.4% riesgo sobre $50).
  En produccion el Camino A usa 25% de riesgo → factor de escala = 62.5x.
  El MC recibe `risk_pct` y reescala cada PnL antes de simular, manteniendo
  los resultados TP/SL del backtest pero con el tamano de lote real.

  Modos:
    risk_pct=None  → usa PnL tal como llegan del backtest (modo historico)
    risk_pct=0.25  → reescala al 25% de riesgo real (Camino A)
    risk_pct=0.10  → reescala al 10% (modo anterior)
"""
import time
import numpy as np
import pandas as pd
from tqdm import tqdm

WEEKLY_TARGET_MULT = 2.0    # objetivo: duplicar la cuenta
RUIN_THRESHOLD     = 0.50   # drawdown >= 50% sobre capital pico = ruina

# Parametros del backtest historico (lote fijo con el que se genero trades.csv)
_BACKTEST_SL_PIPS   = 8.0
_BACKTEST_LOT       = 0.01
_PIP_VALUE_PER_LOT  = 10.0   # $10 por pip por lote estandar en EURUSD


def _rescale_pnl(
    pnl_array: np.ndarray,
    initial_capital: float,
    risk_pct: float,
    sl_pips: float = _BACKTEST_SL_PIPS,
    backtest_lot: float = _BACKTEST_LOT,
    pip_value: float = _PIP_VALUE_PER_LOT,
) -> np.ndarray:
    """
    Reescala los PnL del backtest (lote fijo) al riesgo % real del Camino A.

    Formula:
      riesgo_usd_real  = capital * risk_pct
      lote_real        = riesgo_usd_real / (sl_pips * pip_value)
      factor           = lote_real / backtest_lot
      pnl_reescalado   = pnl_original * factor

    Nota: el factor se recalcula trade a trade usando el capital COMPUESTO,
    lo que modela fielmente el compounding del Camino A.
    Sin embargo para el array estatico usamos capital inicial como proxy
    (el compounding real ocurre dentro del loop de simulacion).
    """
    lote_real = (initial_capital * risk_pct) / (sl_pips * pip_value)
    factor    = lote_real / backtest_lot
    return pnl_array * factor


def run_monte_carlo(
    trades_df: pd.DataFrame,
    initial_capital: float = 50.0,
    n_simulations: int = 1000,
    seed: int | None = None,
    risk_pct: float | None = None,   # None = historico | 0.25 = Camino A
) -> dict:
    """
    Corre `n_simulations` simulaciones bootstrap CON reemplazo.

    Si `risk_pct` se especifica, los PnL se reescalan dinamicamente
    en cada paso del compounding (lote crece con el capital).

    Retorna dict con equity_curves, final_capitals, max_drawdowns, stats.
    """
    if trades_df.empty:
        raise ValueError("No hay trades. Ejecuta primero el backtest.")

    effective_seed = seed if seed is not None else int(time.time() * 1000) % (2**31)
    rng = np.random.default_rng(effective_seed)

    # Array de resultados binarios: +1 (TP) o -1 (SL) segun signo del PnL
    # Guardamos el ratio TP/SL para reescalar dinamicamente con compounding
    outcomes   = np.sign(trades_df["pnl_usd"].values).astype(int)  # +1 o -1
    pnl_raw    = trades_df["pnl_usd"].values.copy()
    n_trades   = len(pnl_raw)
    final_target = initial_capital * WEEKLY_TARGET_MULT

    # Modo: historico (pnl fijo) vs reescalado (compounding real)
    use_compound = risk_pct is not None
    if use_compound:
        riesgo_modo = risk_pct
        print(f"[Monte Carlo] Modo CAMINO A — riesgo {risk_pct*100:.0f}% por trade (compounding dinamico)")
    else:
        riesgo_modo = None
        print(f"[Monte Carlo] Modo HISTORICO — PnL del backtest sin reescalar")

    print(f"[Monte Carlo] {n_simulations} sims | {n_trades} trades | seed={effective_seed} | capital=${initial_capital}")

    equity_curves  = np.zeros((n_simulations, n_trades + 1))
    final_capitals = np.zeros(n_simulations)
    max_drawdowns  = np.zeros(n_simulations)
    ruin_count     = 0
    double_count   = 0

    for sim in tqdm(range(n_simulations), desc="Monte Carlo"):
        # Bootstrap con reemplazo sobre los outcomes (+1/-1)
        sampled_outcomes = rng.choice(outcomes, size=n_trades, replace=True)

        capital      = initial_capital
        equity       = [capital]
        peak_capital = capital
        max_dd       = 0.0
        ruined       = False

        for outcome in sampled_outcomes:
            if use_compound:
                # Lote dinamico: crece con el capital actual (compounding real)
                lote_actual = (capital * riesgo_modo) / (_BACKTEST_SL_PIPS * _PIP_VALUE_PER_LOT)
                lote_actual = max(0.01, lote_actual)   # minimo 0.01
                if outcome > 0:   # TP
                    pnl = lote_actual * _BACKTEST_SL_PIPS * _PIP_VALUE_PER_LOT * 2  # RR 1:2
                else:             # SL
                    pnl = -lote_actual * _BACKTEST_SL_PIPS * _PIP_VALUE_PER_LOT
            else:
                # Modo historico: PnL fijo del backtest (promedio TP/SL)
                avg_tp = pnl_raw[pnl_raw > 0].mean() if (pnl_raw > 0).any() else 1.6
                avg_sl = pnl_raw[pnl_raw < 0].mean() if (pnl_raw < 0).any() else -0.8
                pnl = avg_tp if outcome > 0 else avg_sl

            capital += pnl
            equity.append(capital)

            if capital > peak_capital:
                peak_capital = capital

            dd = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0.0
            max_dd = max(max_dd, dd)

            if dd >= RUIN_THRESHOLD or capital <= 0:
                ruined = True
                equity.extend([max(capital, 0.0)] * (n_trades - len(equity) + 1))
                break

        while len(equity) < n_trades + 1:
            equity.append(equity[-1])

        equity_curves[sim]  = equity[:n_trades + 1]
        final_capitals[sim] = max(capital, 0.0)
        max_drawdowns[sim]  = max_dd

        if ruined:
            ruin_count += 1
        if capital >= final_target:
            double_count += 1

    stats = {
        "mean_final":          float(np.mean(final_capitals)),
        "median_final":        float(np.median(final_capitals)),
        "p5_final":            float(np.percentile(final_capitals, 5)),
        "p95_final":           float(np.percentile(final_capitals, 95)),
        "mean_max_drawdown":   float(np.mean(max_drawdowns)),
        "p95_max_drawdown":    float(np.percentile(max_drawdowns, 95)),
        "ruin_pct":            ruin_count   / n_simulations * 100,
        "double_pct":          double_count / n_simulations * 100,
        "n_simulations":       n_simulations,
        "n_trades":            n_trades,
        "initial_capital":     initial_capital,
        "target_capital":      final_target,
        "seed_used":           effective_seed,
        "risk_mode":           f"{risk_pct*100:.0f}%" if risk_pct else "historico",
    }

    print(f"[Monte Carlo] Ruinas   : {ruin_count}/{n_simulations} ({stats['ruin_pct']:.1f}%)")
    print(f"[Monte Carlo] Duplican : {double_count}/{n_simulations} ({stats['double_pct']:.1f}%)")
    print(f"[Monte Carlo] Mediana  : ${stats['median_final']:.2f} | P5: ${stats['p5_final']:.2f} | P95: ${stats['p95_final']:.2f}")
    print(f"[Monte Carlo] DD medio : {stats['mean_max_drawdown']*100:.1f}% | P95: {stats['p95_max_drawdown']*100:.1f}%")

    return {
        "equity_curves":  equity_curves,
        "final_capitals": final_capitals,
        "max_drawdowns":  max_drawdowns,
        "ruin_count":     ruin_count,
        "double_count":   double_count,
        "stats":          stats,
    }
