"""
monte_carlo.py
Toma el historial de trades del backtest y corre N simulaciones
aleatorizando el orden de los trades (bootstrap sin reemplazo).
Aplica el compounding WDC: $50 -> $100 -> $200 ...
"""
import numpy as np
import pandas as pd
from tqdm import tqdm

WEEKLY_TARGET_MULT  = 2.0   # duplicar cada semana
TRADES_PER_WEEK_EST = 25    # estimado de trades por semana para segmentar
RUIN_THRESHOLD      = 0.50  # drawdown > 50% del capital inicial de semana = ruina


def run_monte_carlo(
    trades_df: pd.DataFrame,
    initial_capital: float = 50.0,
    n_simulations: int = 1000,
    seed: int = 0,
) -> dict:
    """
    Corre `n_simulations` simulaciones sobre el historial de trades.
    En cada simulacion se baraja aleatoriamente el orden de los trades.

    Retorna dict con:
      - equity_curves: array (n_sims, n_trades+1) con curvas de capital
      - final_capitals: array (n_sims,)
      - max_drawdowns: array (n_sims,)
      - ruin_count: int
      - double_count: int
      - stats: dict con percentiles y medias
    """
    if trades_df.empty:
        raise ValueError("No hay trades para simular. Ejecuta primero el backtest.")

    rng          = np.random.default_rng(seed)
    pnl_array    = trades_df["pnl_usd"].values.copy()
    n_trades     = len(pnl_array)
    final_target = initial_capital * WEEKLY_TARGET_MULT

    equity_curves  = np.zeros((n_simulations, n_trades + 1))
    final_capitals = np.zeros(n_simulations)
    max_drawdowns  = np.zeros(n_simulations)
    ruin_count     = 0
    double_count   = 0

    print(f"[Monte Carlo] {n_simulations} simulaciones | {n_trades} trades cada una")

    for sim in tqdm(range(n_simulations), desc="Monte Carlo"):
        shuffled_pnl = rng.permutation(pnl_array)
        capital      = initial_capital
        equity       = [capital]
        peak_capital = capital
        max_dd       = 0.0
        ruined       = False

        for pnl in shuffled_pnl:
            capital += pnl
            equity.append(capital)

            # Drawdown corriente
            if capital > peak_capital:
                peak_capital = capital
            dd = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0.0
            max_dd = max(max_dd, dd)

            # Condicion de ruina
            if dd >= RUIN_THRESHOLD or capital <= 0:
                ruined = True
                # Rellenar el resto de la curva con el ultimo valor
                equity.extend([capital] * (n_trades - len(equity) + 1))
                break

        # Asegurar longitud correcta
        while len(equity) < n_trades + 1:
            equity.append(equity[-1])

        equity_curves[sim] = equity[:n_trades + 1]
        final_capitals[sim] = capital
        max_drawdowns[sim]  = max_dd

        if ruined:
            ruin_count += 1
        if capital >= final_target:
            double_count += 1

    stats = {
        "mean_final":         float(np.mean(final_capitals)),
        "median_final":       float(np.median(final_capitals)),
        "p5_final":           float(np.percentile(final_capitals, 5)),
        "p95_final":          float(np.percentile(final_capitals, 95)),
        "mean_max_drawdown":  float(np.mean(max_drawdowns)),
        "p95_max_drawdown":   float(np.percentile(max_drawdowns, 95)),
        "ruin_pct":           ruin_count   / n_simulations * 100,
        "double_pct":         double_count / n_simulations * 100,
        "n_simulations":      n_simulations,
        "n_trades":           n_trades,
        "initial_capital":    initial_capital,
        "target_capital":     final_target,
    }

    print(f"[Monte Carlo] Ruinas: {ruin_count}/{n_simulations} ({stats['ruin_pct']:.1f}%)")
    print(f"[Monte Carlo] Duplican: {double_count}/{n_simulations} ({stats['double_pct']:.1f}%)")
    print(f"[Monte Carlo] DD medio: {stats['mean_max_drawdown']*100:.1f}% | P95: {stats['p95_max_drawdown']*100:.1f}%")

    return {
        "equity_curves":  equity_curves,
        "final_capitals": final_capitals,
        "max_drawdowns":  max_drawdowns,
        "ruin_count":     ruin_count,
        "double_count":   double_count,
        "stats":          stats,
    }
