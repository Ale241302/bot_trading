"""
monte_carlo.py
Simula N escenarios bootstrap sobre el historial de trades.

v5 — Multi-par:
  Ahora los trades pueden venir de pares con diferentes SL/TP.
  En modo compound, se recalcula el lote por trade usando el SL real
  de cada trade (campo 'pips' del outcome).

  En modo historico, se usa el PnL directo del backtest.

  Modos:
    risk_pct=None  → usa PnL tal como llegan del backtest (modo historico)
    risk_pct=0.05  → reescala al 5% de riesgo real (compounding dinamico)
"""
import time
import numpy as np
import pandas as pd
from tqdm import tqdm

from .pair_config import PAIR_SPECS

WEEKLY_TARGET_MULT = 2.0    # objetivo: duplicar la cuenta
RUIN_THRESHOLD     = 0.50   # drawdown >= 50% sobre capital pico = ruina


def run_monte_carlo(
    trades_df: pd.DataFrame,
    initial_capital: float = 50.0,
    n_simulations: int = 1000,
    seed: int | None = None,
    risk_pct: float | None = None,
) -> dict:
    """
    Corre `n_simulations` simulaciones bootstrap CON reemplazo.

    Multi-par: cada trade tiene un SL y pip_value potencialmente diferente.
    En modo compound, se recalcula el lote dinamicamente por trade.

    Retorna dict con equity_curves, final_capitals, max_drawdowns, stats.
    """
    if trades_df.empty:
        raise ValueError("No hay trades. Ejecuta primero el backtest.")

    effective_seed = seed if seed is not None else int(time.time() * 1000) % (2**31)
    rng = np.random.default_rng(effective_seed)

    # Preparar arrays de datos por trade
    pnl_raw    = trades_df["pnl_usd"].values.copy()
    outcomes   = np.sign(pnl_raw).astype(int)  # +1 o -1
    n_trades   = len(pnl_raw)
    final_target = initial_capital * WEEKLY_TARGET_MULT

    # Para modo compound multi-par: necesitamos SL y pip_value por trade
    # Extraer del campo 'symbol' si existe
    has_symbol = "symbol" in trades_df.columns

    if has_symbol:
        symbols_arr = trades_df["symbol"].values
        sl_pips_arr = np.array([
            PAIR_SPECS.get(s, {}).get("sl_pips", 8.0) for s in symbols_arr
        ])
        tp_pips_arr = np.array([
            PAIR_SPECS.get(s, {}).get("tp_pips", 16.0) for s in symbols_arr
        ])
        pip_value_arr = np.array([
            PAIR_SPECS.get(s, {}).get("pip_value_per_lot", 10.0) for s in symbols_arr
        ])
    else:
        # Retrocompatible: asumir EURUSD
        sl_pips_arr   = np.full(n_trades, 8.0)
        tp_pips_arr   = np.full(n_trades, 16.0)
        pip_value_arr = np.full(n_trades, 10.0)

    use_compound = risk_pct is not None
    if use_compound:
        riesgo_modo = risk_pct
        print(f"[Monte Carlo] Modo CAMINO A — riesgo {risk_pct*100:.0f}% por trade (compounding dinamico)")
    else:
        riesgo_modo = None
        print(f"[Monte Carlo] Modo HISTORICO — PnL del backtest sin reescalar")

    print(f"[Monte Carlo] {n_simulations} sims | {n_trades} trades | seed={effective_seed} | capital=${initial_capital}")
    if has_symbol:
        unique_syms = trades_df["symbol"].unique()
        print(f"[Monte Carlo] Pares: {', '.join(unique_syms)}")

    equity_curves  = np.zeros((n_simulations, n_trades + 1))
    final_capitals = np.zeros(n_simulations)
    max_drawdowns  = np.zeros(n_simulations)
    ruin_count     = 0
    double_count   = 0

    for sim in tqdm(range(n_simulations), desc="Monte Carlo"):
        # Bootstrap: indices aleatorios con reemplazo
        sampled_indices = rng.integers(0, n_trades, size=n_trades)

        capital      = initial_capital
        equity       = [capital]
        peak_capital = capital
        max_dd       = 0.0
        ruined       = False

        for idx in sampled_indices:
            outcome   = outcomes[idx]
            sl_pips   = sl_pips_arr[idx]
            tp_pips   = tp_pips_arr[idx]
            pip_value = pip_value_arr[idx]

            if use_compound:
                # Lote dinamico: crece con el capital actual
                lote_actual = (capital * riesgo_modo) / (sl_pips * pip_value)
                lote_actual = max(0.01, lote_actual)
                if outcome > 0:   # TP
                    pnl = lote_actual * tp_pips * pip_value
                else:             # SL
                    pnl = -lote_actual * sl_pips * pip_value
            else:
                # Modo historico: PnL fijo del backtest
                pnl = pnl_raw[idx]

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
