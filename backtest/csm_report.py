"""
csm_report.py
Reporte HTML interactivo (Plotly) para el backtest CSM.

Secciones:
  - KPIs: WR, PF, capital final, ruina%, duplican%, DD máximo
  - Equity curve histórica (1 sola corrida bootstrap-free)
  - Sample de N curvas Monte Carlo
  - Histograma de capital final
  - Distribución de drawdowns
  - Tabla de trades con razón de cierre
  - Tabla de picks semanales (strongest vs weakest)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).parent / "output"


def _kpis(trades_df: pd.DataFrame, mc_stats: dict, initial_capital: float) -> dict:
    if trades_df.empty:
        return {}
    wins = (trades_df["pnl_usd"] > 0).sum()
    losses = (trades_df["pnl_usd"] <= 0).sum()
    n = len(trades_df)
    wr = wins / n * 100
    sum_win = trades_df.loc[trades_df["pnl_usd"] > 0, "pnl_usd"].sum()
    sum_loss = -trades_df.loc[trades_df["pnl_usd"] <= 0, "pnl_usd"].sum()
    pf = sum_win / sum_loss if sum_loss > 0 else float("inf")
    final_hist = float(trades_df["capital_end"].iloc[-1]) if not trades_df.empty else initial_capital

    # Drawdown histórico
    eq = trades_df["capital_end"].values
    peak = np.maximum.accumulate(np.concatenate([[initial_capital], eq]))
    dd = (peak - np.concatenate([[initial_capital], eq])) / peak
    max_dd = float(dd.max())

    return {
        "n_trades": n,
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": wr,
        "profit_factor": pf,
        "capital_final_hist": final_hist,
        "drawdown_hist": max_dd * 100,
        "ruin_pct_mc": mc_stats.get("ruin_pct", float("nan")),
        "double_pct_mc": mc_stats.get("double_pct", float("nan")),
        "median_final_mc": mc_stats.get("median_final", float("nan")),
        "p5_final_mc": mc_stats.get("p5_final", float("nan")),
        "p95_final_mc": mc_stats.get("p95_final", float("nan")),
        "p95_dd_mc": mc_stats.get("p95_max_drawdown", float("nan")) * 100,
    }


def _verdict(kpis: dict, risk_pct: float) -> tuple[str, str]:
    """Veredicto cualitativo basado en métricas."""
    if not kpis:
        return ("Sin datos", "Backtest sin trades — revisa filtros de selección.")

    ruin = kpis["ruin_pct_mc"]
    pf = kpis["profit_factor"]
    wr = kpis["win_rate"]

    if ruin > 30 or pf < 1.0:
        return (
            "🔴 NO APTO",
            f"Ruina {ruin:.1f}% / PF {pf:.2f} / WR {wr:.1f}%. "
            f"Con riesgo {risk_pct*100:.0f}% por trade, esta estrategia destruye la cuenta "
            "en un porcentaje inaceptable de escenarios. NO operar en demo ni real."
        )
    if ruin > 15 or pf < 1.3:
        return (
            "🟠 ALTO RIESGO",
            f"Ruina {ruin:.1f}% / PF {pf:.2f}. La estrategia tiene edge positivo pero "
            f"el riesgo {risk_pct*100:.0f}% es agresivo. Probar en demo varias semanas antes de live."
        )
    if ruin <= 15 and pf >= 1.5 and wr >= 45:
        return (
            "🟢 ACEPTABLE",
            f"Ruina {ruin:.1f}% / PF {pf:.2f} / WR {wr:.1f}%. Métricas sostenibles "
            f"al riesgo {risk_pct*100:.0f}%. Considera demo de 4+ semanas antes de live."
        )
    return (
        "🟡 MARGINAL",
        f"Ruina {ruin:.1f}% / PF {pf:.2f} / WR {wr:.1f}%. Edge positivo pero "
        "métricas justas. Iterar con risk menor (5-10%) o filtros adicionales."
    )


def generate_csm_report(
    trades_df: pd.DataFrame,
    mc_results: dict,
    week_picks: list,
    initial_capital: float,
    risk_pct: float,
    output_dir: Path | None = None,
) -> Path:
    """
    Genera el HTML interactivo y devuelve la ruta.
    """
    out = output_dir or OUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    kpis = _kpis(trades_df, mc_results.get("stats", {}), initial_capital)
    verdict_tag, verdict_text = _verdict(kpis, risk_pct)

    # ─────────────────── Plotly figures ───────────────────
    fig_eq = go.Figure()
    if not trades_df.empty:
        eq_x = trades_df["exit_ts"]
        eq_y = trades_df["capital_end"]
        fig_eq.add_trace(go.Scatter(
            x=eq_x, y=eq_y, mode="lines+markers",
            name="Capital histórico",
            line=dict(color="#2E86AB", width=2),
            marker=dict(size=5),
        ))
        fig_eq.add_hline(y=initial_capital, line_dash="dash",
                         line_color="gray", annotation_text="Capital inicial")
    fig_eq.update_layout(
        title="Equity curve histórica (sin Monte Carlo)",
        xaxis_title="Fecha de cierre",
        yaxis_title="Capital (USD)",
        template="plotly_white",
        height=400,
    )

    # Sample de curvas MC
    fig_mc = go.Figure()
    eq_curves = mc_results.get("equity_curves")
    if eq_curves is not None and len(eq_curves) > 0:
        n_plot = min(100, len(eq_curves))
        rng = np.random.default_rng(0)
        sample = rng.choice(len(eq_curves), n_plot, replace=False)
        for s in sample:
            fig_mc.add_trace(go.Scatter(
                y=eq_curves[s], mode="lines",
                line=dict(width=0.7, color="rgba(46, 134, 171, 0.15)"),
                showlegend=False, hoverinfo="skip",
            ))
        # Mediana
        median_curve = np.median(eq_curves, axis=0)
        fig_mc.add_trace(go.Scatter(
            y=median_curve, mode="lines",
            name="Mediana MC",
            line=dict(color="#A23B72", width=3),
        ))
        fig_mc.add_hline(y=initial_capital, line_dash="dash", line_color="gray")
    fig_mc.update_layout(
        title=f"Monte Carlo — {n_plot if eq_curves is not None else 0} curvas (mediana en magenta)",
        xaxis_title="# trade",
        yaxis_title="Capital (USD)",
        template="plotly_white",
        height=400,
    )

    # Histograma de capital final
    final_caps = mc_results.get("final_capitals", np.array([]))
    fig_hist = go.Figure()
    if len(final_caps) > 0:
        fig_hist.add_trace(go.Histogram(
            x=final_caps, nbinsx=50,
            marker_color="#2E86AB",
        ))
        fig_hist.add_vline(x=initial_capital, line_dash="dash",
                           line_color="black", annotation_text="Capital inicial")
        fig_hist.add_vline(x=initial_capital * 2, line_dash="dot",
                           line_color="green", annotation_text="2× (objetivo)")
    fig_hist.update_layout(
        title="Distribución del capital final (Monte Carlo)",
        xaxis_title="Capital final (USD)",
        yaxis_title="Frecuencia",
        template="plotly_white",
        height=350,
    )

    # Drawdown histogram
    dds = mc_results.get("max_drawdowns", np.array([]))
    fig_dd = go.Figure()
    if len(dds) > 0:
        fig_dd.add_trace(go.Histogram(
            x=dds * 100, nbinsx=50,
            marker_color="#E63946",
        ))
        fig_dd.add_vline(x=50, line_dash="dash", line_color="black",
                         annotation_text="Umbral ruina (50%)")
    fig_dd.update_layout(
        title="Distribución del drawdown máximo (Monte Carlo)",
        xaxis_title="Drawdown máximo (%)",
        yaxis_title="Frecuencia",
        template="plotly_white",
        height=350,
    )

    # ─────────────────── HTML render ───────────────────
    trades_html = (
        trades_df.assign(
            week_start=trades_df["week_start"].dt.strftime("%Y-%m-%d") if not trades_df.empty else "",
            entry_ts=trades_df["entry_ts"].dt.strftime("%Y-%m-%d %H:%M") if not trades_df.empty else "",
            exit_ts=trades_df["exit_ts"].dt.strftime("%Y-%m-%d %H:%M") if not trades_df.empty else "",
        )[[
            "week_start", "symbol", "side", "strongest", "weakest",
            "entry_ts", "exit_ts", "lot", "sl_pips", "tp_pips",
            "pnl_usd", "exit_reason", "capital_start", "capital_end",
        ]].to_html(index=False, classes="trades-table", float_format=lambda x: f"{x:.2f}")
        if not trades_df.empty else "<p>No se generaron trades.</p>"
    )

    picks_html = "<p>No hubo selecciones semanales.</p>"
    if week_picks:
        rows = []
        for p in week_picks[:50]:  # primeras 50 picks
            rows.append(
                f"<tr><td>{p.week_start.date()}</td>"
                f"<td>{p.strongest}</td><td>{p.weakest}</td>"
                f"<td>{p.pair_symbol}</td>"
                f"<td>{'LONG' if p.direction > 0 else 'SHORT'}</td></tr>"
            )
        picks_html = (
            "<table class='trades-table'>"
            "<thead><tr><th>Lunes</th><th>Más fuerte</th>"
            "<th>Más débil</th><th>Par</th><th>Dirección</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
            f"<p style='font-size:0.85em;color:#666'>"
            f"Mostrando primeras 50 de {len(week_picks)} selecciones.</p>"
        )

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>CSM Sniper — Backtest Report</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       max-width: 1200px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
h1 {{ color: #2E86AB; }}
h2 {{ border-bottom: 2px solid #ddd; padding-bottom: 4px; margin-top: 2rem; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
             gap: 12px; margin: 1.2rem 0; }}
.kpi {{ background: #f5f7fa; border-left: 4px solid #2E86AB; padding: 12px;
        border-radius: 4px; }}
.kpi .label {{ font-size: 0.8em; color: #666; text-transform: uppercase; }}
.kpi .value {{ font-size: 1.6em; font-weight: 600; margin-top: 4px; }}
.verdict {{ padding: 16px; border-radius: 6px; margin: 1.5rem 0;
            background: #fef9e7; border-left: 6px solid #f1c40f; }}
.verdict.NO_APTO {{ background: #fdedec; border-color: #c0392b; }}
.verdict.ALTO_RIESGO {{ background: #fef5e7; border-color: #e67e22; }}
.verdict.ACEPTABLE {{ background: #eafaf1; border-color: #27ae60; }}
.verdict h3 {{ margin: 0 0 6px 0; }}
table.trades-table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
table.trades-table th, table.trades-table td {{
    border: 1px solid #ddd; padding: 6px 8px; text-align: right; }}
table.trades-table th {{ background: #2E86AB; color: white; text-align: center; }}
table.trades-table td:nth-child(1), table.trades-table td:nth-child(2),
table.trades-table td:nth-child(3) {{ text-align: left; }}
table.trades-table tr:nth-child(even) {{ background: #f9f9f9; }}
.warning {{ background: #fdf2e8; border-left: 4px solid #d35400; padding: 12px;
             border-radius: 4px; margin: 1.5rem 0; }}
</style>
</head>
<body>
<h1>CSM Sniper — Backtest + Monte Carlo</h1>
<p>Estrategia "Francotirador de Fuerza Relativa con Compounding Agresivo".
Capital inicial: <strong>${initial_capital:.2f}</strong> ·
Riesgo por trade: <strong>{risk_pct*100:.0f}%</strong> ·
Trades históricos: <strong>{kpis.get('n_trades', 0)}</strong> ·
Simulaciones MC: <strong>{mc_results.get('stats', {}).get('n_simulations', 0)}</strong></p>

<div class="verdict {verdict_tag.split(' ', 1)[1].replace(' ', '_') if ' ' in verdict_tag else 'MARGINAL'}">
  <h3>Veredicto: {verdict_tag}</h3>
  <p>{verdict_text}</p>
</div>

<h2>KPIs</h2>
<div class="kpi-grid">
  <div class="kpi"><div class="label">Win rate</div>
       <div class="value">{kpis.get('win_rate', 0):.1f}%</div></div>
  <div class="kpi"><div class="label">Profit factor</div>
       <div class="value">{kpis.get('profit_factor', 0):.2f}</div></div>
  <div class="kpi"><div class="label">Capital final histórico</div>
       <div class="value">${kpis.get('capital_final_hist', 0):.2f}</div></div>
  <div class="kpi"><div class="label">Drawdown histórico</div>
       <div class="value">{kpis.get('drawdown_hist', 0):.1f}%</div></div>
  <div class="kpi"><div class="label">Ruina (MC)</div>
       <div class="value">{kpis.get('ruin_pct_mc', 0):.1f}%</div></div>
  <div class="kpi"><div class="label">Duplican (MC)</div>
       <div class="value">{kpis.get('double_pct_mc', 0):.1f}%</div></div>
  <div class="kpi"><div class="label">Mediana final (MC)</div>
       <div class="value">${kpis.get('median_final_mc', 0):.2f}</div></div>
  <div class="kpi"><div class="label">P5 final (MC)</div>
       <div class="value">${kpis.get('p5_final_mc', 0):.2f}</div></div>
  <div class="kpi"><div class="label">P95 DD (MC)</div>
       <div class="value">{kpis.get('p95_dd_mc', 0):.1f}%</div></div>
</div>

<div class="warning">
  <strong>Aviso:</strong> El backtest opera con datos históricos y la simulación
  intra-vela asume worst-case (SL primero si la vela contiene SL y TP). El sentimiento
  retail real (Myfxbook) NO se incluye — esta estrategia es 100% técnica.
  El riesgo {risk_pct*100:.0f}% por trade es <strong>sobre-Kelly</strong> incluso con WR≈55%
  y RR=1:3, lo que matemáticamente implica ruina &gt;40% en horizonte largo.
</div>

<h2>Equity curve histórica</h2>
<div id="eq"></div>

<h2>Monte Carlo (sample)</h2>
<div id="mc"></div>

<h2>Distribución de resultados</h2>
<div id="hist"></div>
<div id="dd"></div>

<h2>Selecciones semanales (lunes)</h2>
{picks_html}

<h2>Trades históricos</h2>
{trades_html}

<script>
Plotly.newPlot('eq', {fig_eq.to_json()}, {{}}, {{responsive:true}});
Plotly.newPlot('mc', {fig_mc.to_json()}, {{}}, {{responsive:true}});
Plotly.newPlot('hist', {fig_hist.to_json()}, {{}}, {{responsive:true}});
Plotly.newPlot('dd', {fig_dd.to_json()}, {{}}, {{responsive:true}});
</script>
</body>
</html>
"""

    out_path = out / "csm_report.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"[CSM] Reporte: {out_path}")
    return out_path
