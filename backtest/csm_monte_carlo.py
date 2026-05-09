"""
csm_monte_carlo.py
Monte Carlo bootstrap independiente para la estrategia CSM.

A diferencia de monte_carlo.py (WDC), este lee `sl_pips`, `tp_pips`
y `pip_value_per_lot` directamente del trades_df (porque el ATR
hace que cada trade tenga SL distinto). No depende de PAIR_SPECS.

Modos:
  - risk_pct=None  → bootstrap del PnL histórico (sin reescalar)
  - risk_pct=0.30  → recalcula lote dinámico cada trade (compounding)

Definición de ruina: capital ≤ 0 OR drawdown ≥ 50% sobre pico.
"""
import logging
import time

import numpy as np
import pandas as pd
from tqdm import tqdm

logger = logging.getLogger(__name__)

WEEKLY_TARGET_MULT = 2.0   # objetivo "duplicar"
RUIN_THRESHOLD = 0.50


def run_csm_monte_carlo(
    trades_df: pd.DataFrame,
    initial_capital: float = 50.0,
    n_simulations: int = 1000,
    seed: int = 42,
    risk_pct: float | None = 0.30,
) -> dict:
    if trades_df.empty:
        raise ValueError("trades_df vacío. Corre primero el backtest CSM.")

    rng = np.random.default_rng(seed if seed is not None else int(time.time()))
    n_trades = len(trades_df)
    target = initial_capital * WEEKLY_TARGET_MULT

    pnl_raw = trades_df["pnl_usd"].values.astype(float)
    outcomes = np.sign(pnl_raw).astype(int)
    sl_pips_arr = trades_df["sl_pips"].values.astype(float)
    tp_pips_arr = trades_df["tp_pips"].values.astype(float)
    pip_value_arr = trades_df["pip_value_per_lot"].values.astype(float)

    use_compound = risk_pct is not None
    mode_label = f"compound {risk_pct*100:.0f}%" if use_compound else "histórico"
    logger.info(
        f"[MC-CSM] {n_simulations} sims | {n_trades} trades | "
        f"seed={seed} | modo={mode_label} | capital=${initial_capital}"
    )

    equity_curves = np.zeros((n_simulations, n_trades + 1))
    final_capitals = np.zeros(n_simulations)
    max_drawdowns = np.zeros(n_simulations)
    ruin_count = 0
    double_count = 0

    for sim in tqdm(range(n_simulations), desc="CSM Monte Carlo"):
        sample_idx = rng.integers(0, n_trades, size=n_trades)

        capital = initial_capital
        peak = capital
        max_dd = 0.0
        equity = [capital]
        ruined = False

        for i in sample_idx:
            outcome = outcomes[i]
            sl_pips = sl_pips_arr[i]
            tp_pips = tp_pips_arr[i]
            pip_val = pip_value_arr[i]

            if use_compound:
                lote = (capital * risk_pct) / (sl_pips * pip_val)
                lote = max(0.01, min(5.0, lote))
                pnl = lote * tp_pips * pip_val if outcome > 0 else -lote * sl_pips * pip_val
            else:
                pnl = pnl_raw[i]

            capital += pnl
            equity.append(capital)

            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

            if capital <= 0 or dd >= RUIN_THRESHOLD:
                ruined = True
                pad_value = max(capital, 0.0)
                equity.extend([pad_value] * (n_trades - len(equity) + 1))
                break

        while len(equity) < n_trades + 1:
            equity.append(equity[-1])

        equity_curves[sim] = equity[: n_trades + 1]
        final_capitals[sim] = max(capital, 0.0)
        max_drawdowns[sim] = max_dd
        if ruined:
            ruin_count += 1
        if capital >= target:
            double_count += 1

    stats = {
        "mean_final": float(np.mean(final_capitals)),
        "median_final": float(np.median(final_capitals)),
        "p5_final": float(np.percentile(final_capitals, 5)),
        "p95_final": float(np.percentile(final_capitals, 95)),
        "mean_max_drawdown": float(np.mean(max_drawdowns)),
        "p95_max_drawdown": float(np.percentile(max_drawdowns, 95)),
        "ruin_pct": ruin_count / n_simulations * 100,
        "double_pct": double_count / n_simulations * 100,
        "n_simulations": n_simulations,
        "n_trades": n_trades,
        "initial_capital": initial_capital,
        "target_capital": target,
        "seed_used": seed,
        "risk_mode": mode_label,
    }

    logger.info(
        f"[MC-CSM] ruina={stats['ruin_pct']:.1f}% | "
        f"duplican={stats['double_pct']:.1f}% | "
        f"mediana=${stats['median_final']:.2f} | "
        f"DD p95={stats['p95_max_drawdown']*100:.1f}%"
    )

    return {
        "equity_curves": equity_curves,
        "final_capitals": final_capitals,
        "max_drawdowns": max_drawdowns,
        "ruin_count": ruin_count,
        "double_count": double_count,
        "stats": stats,
    }
