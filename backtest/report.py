"""
report.py
Genera reporte completo en HTML con graficas Plotly interactivas.
Archivos generados en: backtest/output/
  - backtest_report.html  <- reporte principal con todas las secciones
  - trades.csv            <- historial de trades para analisis externo

FIX v2:
  - Logica de veredicto WDC corregida: criterios reales de viabilidad.
  - Semaforo basado en profit_factor + ruin_pct + win_rate (no solo double_pct).
"""
import os
import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

OUTPUT_DIR = Path(__file__).parent / "output"


def _ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# METRICAS
# ─────────────────────────────────────────────────────────────

def compute_metrics(trades_df: pd.DataFrame, initial_capital: float = 50.0) -> dict:
    if trades_df.empty:
        return {}

    df = trades_df.copy()
    wins  = df[df["pnl_usd"] > 0]
    loses = df[df["pnl_usd"] < 0]

    win_rate       = len(wins) / len(df) * 100
    gross_profit   = wins["pnl_usd"].sum() if len(wins) else 0.0
    gross_loss     = abs(loses["pnl_usd"].sum()) if len(loses) else 1e-9
    profit_factor  = gross_profit / gross_loss
    expectancy     = df["pnl_usd"].mean()
    total_pnl      = df["pnl_usd"].sum()
    final_capital  = initial_capital + total_pnl
    total_return   = (final_capital - initial_capital) / initial_capital * 100

    # Sharpe (diario)
    df["date"] = pd.to_datetime(df["entry_time"]).dt.date
    daily_pnl  = df.groupby("date")["pnl_usd"].sum()
    sharpe     = (daily_pnl.mean() / (daily_pnl.std() + 1e-9)) * np.sqrt(252)

    # Max Drawdown
    equity = initial_capital + df["pnl_usd"].cumsum()
    peak   = equity.cummax()
    dd     = (peak - equity) / peak
    max_dd = dd.max() * 100

    # Avg duracion
    avg_duration = df["duration_hours"].mean()

    return {
        "total_trades":    len(df),
        "wins":            len(wins),
        "losses":          len(loses),
        "win_rate":        round(win_rate, 2),
        "profit_factor":   round(profit_factor, 3),
        "sharpe_ratio":    round(float(sharpe), 3),
        "max_drawdown_pct":round(float(max_dd), 2),
        "expectancy_usd":  round(float(expectancy), 4),
        "total_pnl_usd":   round(float(total_pnl), 2),
        "final_capital":   round(float(final_capital), 2),
        "total_return_pct":round(float(total_return), 2),
        "avg_duration_h":  round(float(avg_duration), 2),
        "initial_capital": initial_capital,
    }


# ─────────────────────────────────────────────────────────────
# LOGICA DE VEREDICTO WDC (CORREGIDA)
# ─────────────────────────────────────────────────────────────

def _compute_wdc_verdict(metrics: dict, stats: dict) -> tuple[str, str, str]:
    """
    Evalua si la estrategia es viable para WDC basandose en:
      1. Profit Factor >= 1.20  (rentabilidad estructural)
      2. Win Rate >= 34%        (minimo matematico con RR 1:2)
      3. Riesgo de ruina MC < 25% (seguridad aceptable)
      4. Max Drawdown backtest < 40%

    El veredicto NO depende de double_pct del MC con lotes fijos,
    porque ese % cambia drasticamente con el riesgo por operacion.
    """
    pf       = metrics.get("profit_factor", 0)
    wr       = metrics.get("win_rate", 0)
    max_dd   = metrics.get("max_drawdown_pct", 100)
    ruin_pct = stats.get("ruin_pct", 100)
    double_pct = stats.get("double_pct", 0)

    # --- VIABLE: estrategia solida ---
    if pf >= 1.50 and wr >= 40 and ruin_pct < 15 and max_dd < 25:
        emoji = "✅ VIABLE"
        color = "#22c55e"
        desc  = (
            f"Estrategia solida: PF={pf:.2f}, WR={wr:.1f}%, DD={max_dd:.1f}%, Ruina MC={ruin_pct:.1f}%. "
            f"Matematicamente apta para WDC. Ajusta el riesgo por trade en capital_guard.py para acelerar el compounding."
        )
    # --- MARGINAL: funciona pero con limitaciones ---
    elif pf >= 1.20 and wr >= 34 and ruin_pct < 30 and max_dd < 40:
        emoji = "⚠️ MARGINAL"
        color = "#f59e0b"
        desc  = (
            f"Estrategia rentable: PF={pf:.2f}, WR={wr:.1f}%, DD={max_dd:.1f}%, Ruina MC={ruin_pct:.1f}%. "
            f"Funciona con lotes conservadores. Aumenta el riesgo por trade en capital_guard.py con precaucion."
        )
    # --- NO VIABLE: problemas estructurales ---
    else:
        emoji = "\u274c NO VIABLE"
        color = "#ef4444"
        reasons = []
        if pf < 1.20:  reasons.append(f"Profit Factor bajo ({pf:.2f} < 1.20)")
        if wr < 34:    reasons.append(f"Win Rate insuficiente ({wr:.1f}% < 34%)")
        if max_dd >= 40: reasons.append(f"Drawdown excesivo ({max_dd:.1f}% >= 40%)")
        if ruin_pct >= 30: reasons.append(f"Riesgo de ruina alto ({ruin_pct:.1f}% >= 30%)")
        desc = "Problemas estructurales: " + " | ".join(reasons) + ". Revisar logica de senales o SL/TP."

    return emoji, color, desc


# ─────────────────────────────────────────────────────────────
# GRAFICAS PLOTLY
# ─────────────────────────────────────────────────────────────

def _fig_equity_curve(trades_df: pd.DataFrame, initial_capital: float) -> go.Figure:
    equity = initial_capital + trades_df["pnl_usd"].cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(range(len(equity))),
        y=equity.values,
        mode="lines",
        line=dict(color="#00d4aa", width=2),
        name="Capital",
        hovertemplate="Trade #%{x}<br>Capital: $%{y:.2f}<extra></extra>",
    ))
    fig.add_hline(y=initial_capital, line_dash="dash", line_color="gray",
                  annotation_text=f"Inicio ${initial_capital}")
    fig.update_layout(
        title="Curva de Equity — Backtest EURUSD M15",
        xaxis_title="# Trade",
        yaxis_title="Capital (USD)",
        template="plotly_dark",
        height=420,
    )
    return fig


def _fig_pnl_histogram(trades_df: pd.DataFrame) -> go.Figure:
    fig = px.histogram(
        trades_df,
        x="pnl_usd",
        nbins=50,
        color_discrete_sequence=["#636efa"],
        title="Distribucion de PnL por Trade (USD)",
        labels={"pnl_usd": "PnL USD"},
        template="plotly_dark",
    )
    fig.add_vline(x=0, line_color="red", line_dash="dash")
    fig.update_layout(height=380)
    return fig


def _fig_outcome_donut(trades_df: pd.DataFrame) -> go.Figure:
    outcomes = trades_df["outcome"].value_counts()
    color_map = {"TP": "#00d4aa", "SL": "#ef4444", "TIMEOUT": "#f59e0b"}
    colors = [color_map.get(o, "#6b7280") for o in outcomes.index]
    fig = go.Figure(go.Pie(
        labels=outcomes.index,
        values=outcomes.values,
        hole=0.5,
        marker_colors=colors,
        hovertemplate="%{label}: %{value} trades (%{percent})<extra></extra>",
    ))
    fig.update_layout(
        title="Resultado de Trades (TP / SL / Timeout)",
        template="plotly_dark",
        height=380,
    )
    return fig


def _fig_monthly_pnl(trades_df: pd.DataFrame) -> go.Figure:
    df = trades_df.copy()
    df["month"] = pd.to_datetime(df["entry_time"]).dt.to_period("M").astype(str)
    monthly = df.groupby("month")["pnl_usd"].sum().reset_index()
    colors  = ["#00d4aa" if v >= 0 else "#ef4444" for v in monthly["pnl_usd"]]
    fig = go.Figure(go.Bar(
        x=monthly["month"],
        y=monthly["pnl_usd"],
        marker_color=colors,
        hovertemplate="%{x}<br>PnL: $%{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="PnL Mensual (USD)",
        xaxis_title="Mes",
        yaxis_title="PnL USD",
        template="plotly_dark",
        height=380,
    )
    return fig


def _fig_phase_distribution(trades_df: pd.DataFrame) -> go.Figure:
    phase_pnl = trades_df.groupby("phase")["pnl_usd"].agg(["sum", "count"]).reset_index()
    color_map = {"CRECIMIENTO": "#3b82f6", "CONSOLIDACION": "#f59e0b", "ESCUDO": "#8b5cf6"}
    colors = [color_map.get(p, "#6b7280") for p in phase_pnl["phase"]]
    fig = go.Figure(go.Bar(
        x=phase_pnl["phase"],
        y=phase_pnl["sum"],
        text=phase_pnl["count"].apply(lambda x: f"{x} trades"),
        textposition="auto",
        marker_color=colors,
        hovertemplate="Fase: %{x}<br>PnL total: $%{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="PnL por Fase de Capital",
        xaxis_title="Fase",
        yaxis_title="PnL Total USD",
        template="plotly_dark",
        height=380,
    )
    return fig


def _fig_monte_carlo(mc_results: dict) -> go.Figure:
    curves     = mc_results["equity_curves"]
    stats      = mc_results["stats"]
    n_trades   = stats["n_trades"]
    initial    = stats["initial_capital"]
    target     = stats["target_capital"]
    x_axis     = list(range(n_trades + 1))
    n_sims     = len(curves)

    fig = go.Figure()

    sample_n = min(n_sims, 500)
    rng_sample = np.random.default_rng(99)
    indices = rng_sample.choice(n_sims, size=sample_n, replace=False)

    for idx in indices:
        curve     = curves[idx]
        is_ruin   = curve[-1] < initial * 0.5
        fig.add_trace(go.Scatter(
            x=x_axis,
            y=curve,
            mode="lines",
            line=dict(
                color="rgba(239,68,68,0.08)" if is_ruin else "rgba(99,102,241,0.05)",
                width=1,
            ),
            showlegend=False,
            hoverinfo="skip",
        ))

    p5  = np.percentile(curves, 5,  axis=0)
    p50 = np.percentile(curves, 50, axis=0)
    p95 = np.percentile(curves, 95, axis=0)

    fig.add_trace(go.Scatter(x=x_axis, y=p95, mode="lines",
        line=dict(color="#00d4aa", width=2, dash="dot"), name="P95 (optimista)"))
    fig.add_trace(go.Scatter(x=x_axis, y=p50, mode="lines",
        line=dict(color="#f59e0b", width=2.5), name="Mediana (P50)"))
    fig.add_trace(go.Scatter(x=x_axis, y=p5, mode="lines",
        line=dict(color="#ef4444", width=2, dash="dot"), name="P5 (pesimista)"))

    fig.add_hline(y=target, line_dash="dash", line_color="#22c55e",
                  annotation_text=f"Objetivo WDC: ${target:.0f}")
    fig.add_hline(y=initial, line_dash="dash", line_color="gray",
                  annotation_text=f"Capital inicial: ${initial:.0f}")

    fig.update_layout(
        title=f"Monte Carlo — {n_sims} simulaciones | Ruinas: {stats['ruin_pct']:.1f}% | Duplican: {stats['double_pct']:.1f}%",
        xaxis_title="# Trade",
        yaxis_title="Capital (USD)",
        template="plotly_dark",
        height=520,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _fig_mc_final_distribution(mc_results: dict) -> go.Figure:
    finals  = mc_results["final_capitals"]
    stats   = mc_results["stats"]
    initial = stats["initial_capital"]
    target  = stats["target_capital"]

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=finals,
        nbinsx=60,
        marker_color="#636efa",
        opacity=0.8,
        name="Capital final",
    ))
    fig.add_vline(x=target,  line_color="#22c55e", line_dash="dash",
                  annotation_text=f"Objetivo ${target:.0f}")
    fig.add_vline(x=initial, line_color="gray",    line_dash="dash",
                  annotation_text=f"Inicial ${initial:.0f}")
    fig.add_vline(x=np.median(finals), line_color="#f59e0b", line_dash="solid",
                  annotation_text=f"Mediana ${np.median(finals):.1f}")

    fig.update_layout(
        title="Distribucion del Capital Final — Monte Carlo",
        xaxis_title="Capital Final (USD)",
        yaxis_title="Frecuencia",
        template="plotly_dark",
        height=400,
    )
    return fig


def _fig_mc_drawdown_distribution(mc_results: dict) -> go.Figure:
    dds = mc_results["max_drawdowns"] * 100
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=dds,
        nbinsx=50,
        marker_color="#ef4444",
        opacity=0.8,
        name="Max Drawdown",
    ))
    fig.add_vline(x=50, line_color="white", line_dash="dash",
                  annotation_text="Umbral ruina 50%")
    fig.add_vline(x=np.median(dds), line_color="#f59e0b", line_dash="solid",
                  annotation_text=f"Mediana {np.median(dds):.1f}%")

    fig.update_layout(
        title="Distribucion del Max Drawdown — Monte Carlo",
        xaxis_title="Max Drawdown (%)",
        yaxis_title="Frecuencia",
        template="plotly_dark",
        height=400,
    )
    return fig


# ─────────────────────────────────────────────────────────────
# GENERACION DEL REPORTE HTML
# ─────────────────────────────────────────────────────────────

def generate_report(
    trades_df:      pd.DataFrame,
    mc_results:     dict,
    initial_capital: float = 50.0,
):
    _ensure_output_dir()

    metrics = compute_metrics(trades_df, initial_capital)
    stats   = mc_results["stats"]
    ts      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- Guardar CSV de trades ---
    csv_path = OUTPUT_DIR / "trades.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"[Report] Trades guardados en {csv_path}")

    # --- Generar graficas ---
    fig_equity   = _fig_equity_curve(trades_df, initial_capital)
    fig_hist     = _fig_pnl_histogram(trades_df)
    fig_donut    = _fig_outcome_donut(trades_df)
    fig_monthly  = _fig_monthly_pnl(trades_df)
    fig_phase    = _fig_phase_distribution(trades_df)
    fig_mc       = _fig_monte_carlo(mc_results)
    fig_mc_final = _fig_mc_final_distribution(mc_results)
    fig_mc_dd    = _fig_mc_drawdown_distribution(mc_results)

    def to_div(fig):
        return fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Semaforo de viabilidad WDC (CORREGIDO) ---
    wdc_verdict = _compute_wdc_verdict(metrics, stats)

    # --- Tabla de metricas backtest ---
    def metric_row(label, value, fmt=""):
        return f"""<tr><td>{label}</td><td><strong>{value:{fmt}}</strong></td></tr>"""

    backtest_table = f"""
    <table class="metrics-table">
      <tr><th>Metrica</th><th>Valor</th></tr>
      {metric_row('Total Trades', metrics.get('total_trades', 0))}
      {metric_row('Wins / Losses', f"{metrics.get('wins',0)} / {metrics.get('losses',0)}")}
      {metric_row('Win Rate', f"{metrics.get('win_rate',0):.2f}%")}
      {metric_row('Profit Factor', f"{metrics.get('profit_factor',0):.3f}")}
      {metric_row('Sharpe Ratio', f"{metrics.get('sharpe_ratio',0):.3f}")}
      {metric_row('Max Drawdown', f"{metrics.get('max_drawdown_pct',0):.2f}%")}
      {metric_row('Expectancy USD', f"${metrics.get('expectancy_usd',0):.4f}")}
      {metric_row('PnL Total', f"${metrics.get('total_pnl_usd',0):.2f}")}
      {metric_row('Capital Final', f"${metrics.get('final_capital',0):.2f}")}
      {metric_row('Retorno Total', f"{metrics.get('total_return_pct',0):.2f}%")}
      {metric_row('Duracion Media', f"{metrics.get('avg_duration_h',0):.2f}h")}
    </table>
    """

    mc_table = f"""
    <table class="metrics-table">
      <tr><th>Metrica Monte Carlo</th><th>Valor ({stats['n_simulations']} sims)</th></tr>
      <tr><td>% Simulaciones con Ruina</td><td><strong style="color:#ef4444">{stats['ruin_pct']:.1f}%</strong></td></tr>
      <tr><td>% Simulaciones que Duplican</td><td><strong style="color:#22c55e">{stats['double_pct']:.1f}%</strong></td></tr>
      <tr><td>Capital Final Medio</td><td><strong>${stats['mean_final']:.2f}</strong></td></tr>
      <tr><td>Capital Final Mediana</td><td><strong>${stats['median_final']:.2f}</strong></td></tr>
      <tr><td>Capital Final P5 (pesimista)</td><td><strong>${stats['p5_final']:.2f}</strong></td></tr>
      <tr><td>Capital Final P95 (optimista)</td><td><strong>${stats['p95_final']:.2f}</strong></td></tr>
      <tr><td>Max Drawdown Medio</td><td><strong>{stats['mean_max_drawdown']*100:.1f}%</strong></td></tr>
      <tr><td>Max Drawdown P95</td><td><strong>{stats['p95_max_drawdown']*100:.1f}%</strong></td></tr>
    </table>
    """

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Backtest + Monte Carlo — WDC Bot EURUSD</title>
  <script src="https://cdn.plot.ly/plotly-2.29.1.min.js"></script>
  <style>
    :root {{
      --bg:       #0f172a;
      --surface:  #1e293b;
      --border:   #334155;
      --text:     #e2e8f0;
      --muted:    #94a3b8;
      --green:    #22c55e;
      --red:      #ef4444;
      --yellow:   #f59e0b;
      --blue:     #3b82f6;
      --purple:   #8b5cf6;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 24px; }}
    h1   {{ font-size: 1.8rem; font-weight: 700; margin-bottom: 4px; }}
    h2   {{ font-size: 1.2rem; font-weight: 600; color: var(--muted); margin: 32px 0 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
    .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 32px; }}
    .grid-2  {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
    .grid-3  {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
    .card    {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 28px; }}
    .kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; text-align: center; }}
    .kpi-label {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }}
    .kpi-value {{ font-size: 1.5rem; font-weight: 700; }}
    .verdict-box {{ border-radius: 12px; padding: 20px 24px; margin-bottom: 32px; border: 2px solid; }}
    .verdict-title {{ font-size: 1.4rem; font-weight: 700; margin-bottom: 6px; }}
    .verdict-desc  {{ font-size: 0.95rem; color: var(--muted); }}
    .metrics-table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    .metrics-table th {{ text-align: left; padding: 8px 12px; background: var(--bg); color: var(--muted); font-size: 0.75rem; text-transform: uppercase; }}
    .metrics-table td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); }}
    .metrics-table tr:last-child td {{ border-bottom: none; }}
    .full-width {{ grid-column: 1 / -1; }}
    @media (max-width: 768px) {{ .grid-2, .grid-3 {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <h1>&#x1F4CA; Backtest + Monte Carlo</h1>
  <p class="subtitle">WDC Confluence Strategy &middot; EURUSD M15 &middot; Generado: {ts}</p>

  <!-- VEREDICTO WDC -->
  <div class="verdict-box" style="border-color:{wdc_verdict[1]}; background:{wdc_verdict[1]}18">
    <div class="verdict-title" style="color:{wdc_verdict[1]}">{wdc_verdict[0]} &mdash; Estrategia WDC</div>
    <div class="verdict-desc">{wdc_verdict[2]}</div>
  </div>

  <!-- KPIs PRINCIPALES -->
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Win Rate</div>
      <div class="kpi-value" style="color:{'var(--green)' if metrics.get('win_rate',0) >= 50 else 'var(--red)'}">{metrics.get('win_rate',0):.1f}%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Profit Factor</div>
      <div class="kpi-value" style="color:{'var(--green)' if metrics.get('profit_factor',1) >= 1.5 else 'var(--yellow)'}">{metrics.get('profit_factor',0):.2f}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Sharpe Ratio</div>
      <div class="kpi-value" style="color:{'var(--green)' if metrics.get('sharpe_ratio',0) >= 1 else 'var(--yellow)'}">{metrics.get('sharpe_ratio',0):.2f}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Max Drawdown</div>
      <div class="kpi-value" style="color:{'var(--red)' if metrics.get('max_drawdown_pct',0) >= 30 else 'var(--yellow)'}">{metrics.get('max_drawdown_pct',0):.1f}%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Expectancy</div>
      <div class="kpi-value" style="color:{'var(--green)' if metrics.get('expectancy_usd',0) > 0 else 'var(--red)'}">\${metrics.get('expectancy_usd',0):.3f}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">% Duplican (MC)</div>
      <div class="kpi-value" style="color:{'var(--green)' if stats.get('double_pct',0) >= 40 else 'var(--yellow)'}">{stats.get('double_pct',0):.1f}%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">% Ruina (MC)</div>
      <div class="kpi-value" style="color:{'var(--red)' if stats.get('ruin_pct',0) >= 25 else 'var(--green)'}">{stats.get('ruin_pct',0):.1f}%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Capital Final</div>
      <div class="kpi-value">\${metrics.get('final_capital',0):.2f}</div>
    </div>
  </div>

  <!-- EQUITY CURVE -->
  <h2>&#x1F4C8; Curva de Equity &mdash; Backtest Real</h2>
  <div class="card">{to_div(fig_equity)}</div>

  <!-- GRAFICAS BACKTEST -->
  <h2>&#x1F4CB; Analisis de Trades</h2>
  <div class="grid-3">
    <div class="card">{to_div(fig_donut)}</div>
    <div class="card">{to_div(fig_hist)}</div>
    <div class="card">{to_div(fig_phase)}</div>
  </div>
  <div class="card" style="margin-bottom:24px">{to_div(fig_monthly)}</div>

  <!-- MONTE CARLO -->
  <h2>&#x1F3B2; Monte Carlo &mdash; {stats['n_simulations']} Simulaciones</h2>
  <div class="card" style="margin-bottom:20px">{to_div(fig_mc)}</div>
  <div class="grid-2">
    <div class="card">{to_div(fig_mc_final)}</div>
    <div class="card">{to_div(fig_mc_dd)}</div>
  </div>

  <!-- TABLAS DE METRICAS -->
  <h2>&#x1F4CA; Metricas Detalladas</h2>
  <div class="grid-2">
    <div class="card">
      <h3 style="margin-bottom:12px;font-size:1rem">Backtest</h3>
      {backtest_table}
    </div>
    <div class="card">
      <h3 style="margin-bottom:12px;font-size:1rem">Monte Carlo</h3>
      {mc_table}
    </div>
  </div>

  <p style="color:var(--muted);font-size:0.8rem;margin-top:32px;text-align:center">
    bot_trading &middot; WDC Confluence Strategy &middot; EURUSD M15/H1/H4 &middot; {ts}
  </p>
</body>
</html>"""

    html_path = OUTPUT_DIR / "backtest_report.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[Report] Reporte HTML generado: {html_path}")
    return html_path
