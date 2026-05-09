"""
compare_risk.py
Compara el comportamiento Monte Carlo de la MISMA tanda de trades reales
bajo distintos niveles de riesgo por trade.

Idea:
  - Reusa los outcomes (TP/SL) ya generados por la IA.
  - Solo cambia el sizing (lote dinámico).
  - Bootstrap 1000 sims por cada riesgo.
  - Muestra tabla y reporte HTML lado a lado.

Uso:
  python -m backtest.compare_risk
  python -m backtest.compare_risk --trades backtest/output/ai_trades_wdc_real.csv
  python -m backtest.compare_risk --risks 0.03,0.05,0.10,0.20 --sims 1000

No gasta API ni descarga datos. Lee el CSV y corre MC en segundos.
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("compare_risk")


def _verdict_text(stats: dict) -> str:
    """Veredicto cualitativo de un nivel de riesgo."""
    ruin = stats["ruin_pct"]
    double = stats["double_pct"]
    median_pct = (stats["median_final"] / stats["initial_capital"] - 1) * 100
    p5_pct = (stats["p5_final"] / stats["initial_capital"] - 1) * 100

    if ruin > 50:
        return "🔴 SUICIDA"
    if ruin > 25:
        return "🔴 NO APTO"
    if ruin > 15:
        return "🟠 ALTO RIESGO"
    if ruin > 5:
        return "🟡 AGRESIVO"
    return "🟢 SOSTENIBLE"


def _weeks_to_double(stats: dict, n_trades_per_week: float) -> float | None:
    """Aproxima cuántas semanas para doblar usando la mediana."""
    if stats["median_final"] <= stats["initial_capital"]:
        return None
    total_trades = stats["n_trades"]
    growth = stats["median_final"] / stats["initial_capital"]
    # Número de "tandas" de n_trades para doblar
    if growth <= 1:
        return None
    import math
    tandas = math.log(2) / math.log(growth)
    return tandas * (total_trades / n_trades_per_week)


def main():
    parser = argparse.ArgumentParser(description="Comparador de niveles de riesgo (MC sobre trades existentes)")
    parser.add_argument("--trades", type=str,
                        default="backtest/output/ai_trades_wdc_real.csv",
                        help="CSV de trades (default: ai_trades_wdc_real.csv)")
    parser.add_argument("--capital", type=float, default=50.0)
    parser.add_argument("--sims", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--risks", type=str, default="0.03,0.05,0.10,0.20,0.30",
                        help="Niveles de riesgo separados por coma (default: 0.03,0.05,0.10,0.20,0.30)")
    args = parser.parse_args()

    trades_path = Path(args.trades)
    if not trades_path.is_absolute():
        trades_path = Path(__file__).parent.parent / trades_path
    if not trades_path.exists():
        logger.error(f"No encuentro {trades_path}. Corre primero run_ai_backtest.")
        sys.exit(1)

    trades_df = pd.read_csv(trades_path)
    if trades_df.empty:
        logger.error("CSV vacío.")
        sys.exit(1)

    # Asegurar columnas necesarias para el MC
    needed = {"pnl_usd", "symbol", "sl_pips", "tp_pips"}
    missing = needed - set(trades_df.columns)
    if missing:
        logger.error(f"Faltan columnas: {missing}")
        sys.exit(1)

    # pip_value_per_lot: si no está, lo derivamos de pair_config
    if "pip_value_per_lot" not in trades_df.columns:
        from .pair_config import PAIR_SPECS
        trades_df["pip_value_per_lot"] = trades_df["symbol"].map(
            lambda s: PAIR_SPECS.get(s, {}).get("pip_value_per_lot", 10.0)
        )

    risks = [float(r.strip()) for r in args.risks.split(",") if r.strip()]
    risks = sorted(risks)

    logger.info("=" * 72)
    logger.info(f"  Comparador de riesgo — {len(trades_df)} trades · ${args.capital} inicial · {args.sims} sims")
    logger.info(f"  Trades CSV: {trades_path.name}")
    logger.info("=" * 72)

    # Estimar trades por semana del histórico (si hay timestamps)
    trades_per_week = len(trades_df) / 8.0  # asumimos ~8 semanas
    if "exit_ts" in trades_df.columns:
        try:
            ts = pd.to_datetime(trades_df["exit_ts"])
            span_weeks = max(1.0, (ts.max() - ts.min()).total_seconds() / (7 * 86400))
            trades_per_week = len(trades_df) / span_weeks
        except Exception:
            pass

    # Correr MC para cada riesgo
    from .csm_monte_carlo import run_csm_monte_carlo
    results = []
    for r in risks:
        logger.info(f"\n--- Riesgo {r*100:.1f}% ---")
        mc = run_csm_monte_carlo(
            trades_df,
            initial_capital=args.capital,
            n_simulations=args.sims,
            seed=args.seed,
            risk_pct=r,
        )
        results.append((r, mc))

    # Tabla comparativa
    print("\n" + "=" * 100)
    print(f"  COMPARATIVA — {len(trades_df)} trades reales reescalados a distintos niveles de riesgo")
    print(f"  {trades_per_week:.1f} trades/semana en el histórico")
    print("=" * 100)
    print(f"{'Riesgo':>8} {'Veredicto':<16} {'Ruina%':>8} {'Duplican%':>10} "
          f"{'Mediana $':>10} {'P5 $':>8} {'P95 $':>8} {'DD P95%':>9} {'Sem→2x':>8}")
    print("-" * 100)
    for risk, mc in results:
        s = mc["stats"]
        verdict = _verdict_text(s)
        wks = _weeks_to_double(s, trades_per_week)
        wks_str = f"{wks:.1f}" if wks else "  ∞"
        print(f"{risk*100:>7.1f}% {verdict:<16} "
              f"{s['ruin_pct']:>7.1f}% {s['double_pct']:>9.1f}% "
              f"${s['median_final']:>9.2f} ${s['p5_final']:>7.2f} ${s['p95_final']:>7.2f} "
              f"{s['p95_max_drawdown']*100:>8.1f}% {wks_str:>8}")
    print("=" * 100)

    # Recomendación automática
    print("\nRECOMENDACIÓN AUTOMÁTICA:")
    sostenibles = [(r, mc) for r, mc in results if mc["stats"]["ruin_pct"] <= 5]
    if sostenibles:
        best_r, best_mc = max(sostenibles, key=lambda x: x[1]["stats"]["median_final"])
        print(f"  → Mejor riesgo sostenible (ruina ≤ 5%): {best_r*100:.0f}%")
        print(f"     Mediana esperada: ${best_mc['stats']['median_final']:.2f}")
    else:
        print("  → Ningún nivel de riesgo es sostenible (ruina > 5% en todos).")
        print("  → Considera operar el WDC clásico (be7601d) o cambiar la estrategia.")

    # CSV con los resultados
    summary_path = trades_path.parent / "risk_comparison.csv"
    summary = pd.DataFrame([
        {
            "risk_pct": r,
            "verdict": _verdict_text(mc["stats"]),
            "ruin_pct": mc["stats"]["ruin_pct"],
            "double_pct": mc["stats"]["double_pct"],
            "median_final": mc["stats"]["median_final"],
            "p5_final": mc["stats"]["p5_final"],
            "p95_final": mc["stats"]["p95_final"],
            "p95_dd": mc["stats"]["p95_max_drawdown"] * 100,
            "weeks_to_double": _weeks_to_double(mc["stats"], trades_per_week),
        }
        for r, mc in results
    ])
    summary.to_csv(summary_path, index=False)
    print(f"\nResumen CSV: {summary_path}")

    # Reporte HTML comparativo
    try:
        html_path = _generate_compare_html(results, trades_per_week, args.capital, trades_path.parent)
        print(f"Reporte HTML: {html_path}")
    except Exception as e:
        logger.warning(f"No se generó HTML comparativo: {e}")

    print()


def _generate_compare_html(results: list, trades_per_week: float, initial_capital: float, out_dir: Path) -> Path:
    """HTML comparativo con tabla y curvas de equity superpuestas."""
    import plotly.graph_objects as go

    fig = go.Figure()
    colors = ["#27ae60", "#2E86AB", "#e67e22", "#c0392b", "#8e44ad"]
    for (risk, mc), color in zip(results, colors):
        # Mediana de las equity curves
        median_curve = np.median(mc["equity_curves"], axis=0)
        fig.add_trace(go.Scatter(
            y=median_curve, mode="lines",
            line=dict(color=color, width=2),
            name=f"Riesgo {risk*100:.0f}% (mediana)",
        ))
    fig.add_hline(y=initial_capital, line_dash="dash", line_color="gray",
                  annotation_text="Capital inicial")
    fig.add_hline(y=initial_capital * 2, line_dash="dot", line_color="green",
                  annotation_text="Doble (objetivo)")
    fig.update_layout(
        title="Equity curve mediana por nivel de riesgo (Monte Carlo)",
        xaxis_title="# trade", yaxis_title="Capital (USD)",
        template="plotly_white", height=450,
    )

    # Tabla
    rows_html = ""
    for risk, mc in results:
        s = mc["stats"]
        v = _verdict_text(s)
        cls = "ok" if "🟢" in v else "warn" if "🟡" in v else "bad"
        wks = _weeks_to_double(s, trades_per_week)
        wks_str = f"{wks:.1f} sem" if wks else "—"
        rows_html += f"""
<tr class="row-{cls}">
  <td><strong>{risk*100:.0f}%</strong></td>
  <td>{v}</td>
  <td>{s['ruin_pct']:.1f}%</td>
  <td>{s['double_pct']:.1f}%</td>
  <td>${s['median_final']:.2f}</td>
  <td>${s['p5_final']:.2f}</td>
  <td>${s['p95_final']:.2f}</td>
  <td>{s['p95_max_drawdown']*100:.1f}%</td>
  <td>{wks_str}</td>
</tr>"""

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Comparativa de niveles de riesgo</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 1100px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
h1 {{ color: #2E86AB; }}
table {{ width: 100%; border-collapse: collapse; margin: 1.5rem 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: right; }}
th {{ background: #2E86AB; color: white; }}
td:nth-child(1), td:nth-child(2) {{ text-align: left; }}
.row-ok {{ background: #eafaf1; }}
.row-warn {{ background: #fef5e7; }}
.row-bad {{ background: #fdedec; }}
.note {{ background: #fef9e7; border-left: 4px solid #f1c40f; padding: 12px;
         border-radius: 4px; margin: 1rem 0; font-size: 0.95em; }}
</style>
</head>
<body>
<h1>Comparativa — Mismos {len(results)} niveles de riesgo sobre los mismos trades reales</h1>
<p>Capital inicial <strong>${initial_capital:.2f}</strong> ·
   {trades_per_week:.1f} trades/semana en el histórico ·
   1000 simulaciones Monte Carlo por nivel</p>

<div class="note">
  Esta comparativa <strong>reusa los mismos outcomes (TP/SL) que la IA ya decidió</strong>
  y solo varía el tamaño de la posición. Es matemáticamente equivalente a haber corrido
  el backtest 5 veces, pero más rápido y sin gastar API. La columna <strong>"Sem→2x"</strong>
  estima cuántas semanas tomaría doblar la cuenta si el ritmo histórico se mantiene.
</div>

<table>
<thead>
<tr><th>Riesgo</th><th>Veredicto</th><th>Ruina MC</th><th>Duplican MC</th>
    <th>Mediana</th><th>P5 (peor)</th><th>P95 (mejor)</th><th>DD máx P95</th><th>Sem→2x</th></tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>

<h2>Equity curves medianas (todas superpuestas)</h2>
<div id="eq"></div>

<script>
Plotly.newPlot('eq', {fig.to_json()}, {{}}, {{responsive: true}});
</script>
</body>
</html>
"""
    out_path = out_dir / "risk_comparison.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    main()
