"""
report.py
Genera reporte completo en HTML con graficas Plotly interactivas.
Archivos generados en: backtest/output/
  - backtest_report.html  <- reporte principal con todas las secciones
  - trades.csv            <- historial de trades para analisis externo

v3 (2026-04-16):
  - Tabla de trades DETALLADA: tipo operacion, 4 criterios de decision,
    pips, lote, PnL, duracion, fase.
  - Proyecciones temporales: dia / semana / mes / ano con P5/P50/P95.
  - Grafica PnL semanal adicional.
  - Seccion de criterios expandible (accordion HTML nativo).
  - Semaforo WDC corregido con criterios reales.
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
# METRICAS GENERALES
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

    # Conteos por tipo de senial
    buy_count  = (df["action"] == "BUY").sum()  if "action"  in df.columns else 0
    sell_count = (df["action"] == "SELL").sum() if "action"  in df.columns else 0

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
        "buy_count":       int(buy_count),
        "sell_count":      int(sell_count),
    }


# ─────────────────────────────────────────────────────────────
# PROYECCIONES TEMPORALES (DIA / SEMANA / MES / ANO)
# ─────────────────────────────────────────────────────────────

def compute_time_projections(trades_df: pd.DataFrame, initial_capital: float, mc_results: dict) -> dict:
    """
    Calcula proyecciones de capital para diferentes horizontes temporales
    usando estadisticas del Monte Carlo y la frecuencia real de trades del backtest.
    """
    if trades_df.empty:
        return {}

    df = trades_df.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["date"]  = df["entry_time"].dt.date
    df["week"]  = df["entry_time"].dt.isocalendar().week.astype(int)
    df["month"] = df["entry_time"].dt.to_period("M").astype(str)

    # Frecuencia de trades por periodo
    total_dias_activos = df["date"].nunique()
    total_semanas      = max(df["entry_time"].dt.isocalendar()["week"].nunique(), 1)
    total_meses        = max(df["month"].nunique(), 1)
    total_trades       = len(df)

    trades_por_dia    = round(total_trades / max(total_dias_activos, 1), 2)
    trades_por_semana = round(total_trades / total_semanas, 2)
    trades_por_mes    = round(total_trades / total_meses, 2)
    trades_por_ano    = round(trades_por_mes * 12, 1)

    # PnL promedio por trade (historico)
    avg_pnl_per_trade = df["pnl_usd"].mean()
    std_pnl_per_trade = df["pnl_usd"].std()

    # Estadisticas por periodo
    def _stats_for_n_trades(n: int, cap: float = initial_capital) -> dict:
        """Proyeccion simple con distribucion normal via CLT."""
        if n <= 0:
            return {"p5": cap, "p50": cap, "p95": cap, "esperado": cap}
        mu     = avg_pnl_per_trade * n
        sigma  = std_pnl_per_trade * np.sqrt(n)
        return {
            "p5":      round(cap + mu - 1.645 * sigma, 2),
            "p50":     round(cap + mu, 2),
            "p95":     round(cap + mu + 1.645 * sigma, 2),
            "esperado":round(cap + mu, 2),
        }

    stats = mc_results.get("stats", {})
    n_trades_mc = stats.get("n_trades", total_trades)

    # Escalar percentiles del MC a distintos horizontes
    mc_curves = mc_results.get("equity_curves", None)

    projections = {
        "trades_por_dia":    trades_por_dia,
        "trades_por_semana": trades_por_semana,
        "trades_por_mes":    trades_por_mes,
        "trades_por_ano":    trades_por_ano,
        "dia":   _stats_for_n_trades(max(1, int(trades_por_dia))),
        "semana":_stats_for_n_trades(max(1, int(trades_por_semana))),
        "mes":   _stats_for_n_trades(max(1, int(trades_por_mes))),
        "ano":   _stats_for_n_trades(max(1, int(trades_por_ano))),
    }
    return projections


# ─────────────────────────────────────────────────────────────
# LOGICA DE VEREDICTO WDC
# ─────────────────────────────────────────────────────────────

def _compute_wdc_verdict(metrics: dict, stats: dict) -> tuple[str, str, str]:
    pf       = metrics.get("profit_factor", 0)
    wr       = metrics.get("win_rate", 0)
    max_dd   = metrics.get("max_drawdown_pct", 100)
    ruin_pct = stats.get("ruin_pct", 100)

    if pf >= 1.50 and wr >= 40 and ruin_pct < 15 and max_dd < 25:
        emoji = "✅ VIABLE"
        color = "#22c55e"
        desc  = (
            f"Estrategia solida: PF={pf:.2f}, WR={wr:.1f}%, DD={max_dd:.1f}%, Ruina MC={ruin_pct:.1f}%. "
            f"Matematicamente apta para WDC. Ajusta el riesgo en capital_guard.py para acelerar el compounding."
        )
    elif pf >= 1.20 and wr >= 34 and ruin_pct < 30 and max_dd < 40:
        emoji = "⚠️ MARGINAL"
        color = "#f59e0b"
        desc  = (
            f"Estrategia rentable: PF={pf:.2f}, WR={wr:.1f}%, DD={max_dd:.1f}%, Ruina MC={ruin_pct:.1f}%. "
            f"Funciona con lotes conservadores. Aumenta el riesgo con precaucion."
        )
    else:
        emoji = "❌ NO VIABLE"
        color = "#ef4444"
        reasons = []
        if pf < 1.20:      reasons.append(f"Profit Factor bajo ({pf:.2f} < 1.20)")
        if wr < 34:        reasons.append(f"Win Rate insuficiente ({wr:.1f}% < 34%)")
        if max_dd >= 40:   reasons.append(f"Drawdown excesivo ({max_dd:.1f}% >= 40%)")
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


def _fig_weekly_pnl(trades_df: pd.DataFrame) -> go.Figure:
    df = trades_df.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["semana"] = df["entry_time"].dt.isocalendar()["year"].astype(str) + "-W" + \
                   df["entry_time"].dt.isocalendar()["week"].astype(str).str.zfill(2)
    weekly = df.groupby("semana")["pnl_usd"].sum().reset_index()
    colors = ["#00d4aa" if v >= 0 else "#ef4444" for v in weekly["pnl_usd"]]
    fig = go.Figure(go.Bar(
        x=weekly["semana"],
        y=weekly["pnl_usd"],
        marker_color=colors,
        hovertemplate="Semana %{x}<br>PnL: $%{y:.2f}<extra></extra>",
    ))
    fig.update_layout(
        title="PnL Semanal (USD)",
        xaxis_title="Semana",
        yaxis_title="PnL USD",
        template="plotly_dark",
        height=380,
    )
    return fig


def _fig_action_donut(trades_df: pd.DataFrame) -> go.Figure:
    if "action" not in trades_df.columns:
        return go.Figure()
    actions = trades_df["action"].value_counts()
    color_map = {"BUY": "#22c55e", "SELL": "#ef4444"}
    colors = [color_map.get(a, "#6b7280") for a in actions.index]
    fig = go.Figure(go.Pie(
        labels=actions.index,
        values=actions.values,
        hole=0.5,
        marker_colors=colors,
        hovertemplate="%{label}: %{value} trades (%{percent})<extra></extra>",
    ))
    fig.update_layout(
        title="Tipo de Operacion (BUY / SELL)",
        template="plotly_dark",
        height=340,
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
# TABLA DE TRADES DETALLADA (HTML)
# ─────────────────────────────────────────────────────────────

def _build_trades_table(trades_df: pd.DataFrame) -> str:
    """
    Genera tabla HTML detallada con:
    - #, Fecha/Hora, Tipo (BUY/SELL), Fase, Lote, Entrada, Pips, PnL, Resultado, Duracion
    - Criterios de decision (4 niveles de confluencia) en fila expandible
    """
    if trades_df.empty:
        return "<p style='color:#94a3b8'>No hay trades registrados.</p>"

    rows = []
    for i, (_, row) in enumerate(trades_df.iterrows()):
        idx       = i + 1
        outcome   = row.get("outcome", "?")
        pnl       = row.get("pnl_usd", 0.0)
        action    = row.get("action", "?")
        phase     = row.get("phase", "?")
        pips      = row.get("pips", 0.0)
        lot       = row.get("lot", 0.01)
        duration  = row.get("duration_hours", 0.0)
        entry_t   = row.get("entry_time", "")
        entry_p   = row.get("entry_price", 0.0)
        exit_p    = row.get("exit_price", 0.0)
        cap_after = row.get("capital_after", 0.0)

        # Criterios de confluencia (4 niveles)
        n1 = row.get("nivel_1", False)  # Noticias
        n2 = row.get("nivel_2", False)  # Sentimiento
        n3 = row.get("nivel_3", False)  # Tendencia H4/H1
        n4 = row.get("nivel_4", False)  # Patron M15
        reason = row.get("reason", "—")
        sentiment = row.get("sentiment_short", 0.0)
        av_score  = row.get("av_score", 0.0)

        def chk(val):
            return "✅" if val else "❌"

        # Colores
        outcome_color = {"TP": "#22c55e", "SL": "#ef4444", "TIMEOUT": "#f59e0b"}.get(outcome, "#94a3b8")
        action_color  = "#22c55e" if action == "BUY" else "#ef4444"
        pnl_color     = "#22c55e" if pnl > 0 else "#ef4444"
        phase_color   = {"CRECIMIENTO": "#3b82f6", "CONSOLIDACION": "#f59e0b", "ESCUDO": "#8b5cf6"}.get(phase, "#94a3b8")

        entry_str = str(entry_t)[:16] if entry_t else "—"

        detail_id = f"detail_{idx}"

        rows.append(f"""
        <tr onclick="toggleDetail('{detail_id}')" style="cursor:pointer" class="trade-row">
          <td style="color:#94a3b8;font-size:0.8rem">{idx}</td>
          <td style="font-size:0.82rem;color:#e2e8f0">{entry_str}</td>
          <td><span class="badge" style="background:{action_color}20;color:{action_color};border:1px solid {action_color}40">{action}</span></td>
          <td><span class="badge" style="background:{phase_color}20;color:{phase_color};border:1px solid {phase_color}40;font-size:0.72rem">{phase}</span></td>
          <td style="text-align:right;font-variant-numeric:tabular-nums">{lot:.2f}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums;font-size:0.82rem">{entry_p:.5f}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums;color:{'#22c55e' if pips>0 else '#ef4444'}">{pips:+.1f}p</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums;font-weight:700;color:{pnl_color}">${pnl:+.4f}</td>
          <td><span class="badge" style="background:{outcome_color}20;color:{outcome_color};border:1px solid {outcome_color}40">{outcome}</span></td>
          <td style="text-align:right;color:#94a3b8;font-size:0.82rem">{duration:.1f}h</td>
          <td style="text-align:right;font-size:0.82rem">${cap_after:.2f}</td>
          <td style="color:#94a3b8;font-size:0.8rem">▼</td>
        </tr>
        <tr id="{detail_id}" class="detail-row" style="display:none">
          <td colspan="12">
            <div class="detail-box">
              <div class="detail-grid">
                <div class="detail-section">
                  <div class="detail-title">🔍 Criterios de Decision (Confluencia)</div>
                  <div class="criteria-row"><span class="crit-label">Nivel 1 — Noticias bloqueadas:</span> <span>{chk(not row.get("news_blocked", True))} {"Sin noticias HIGH" if not row.get("news_blocked", True) else "Noticias detectadas"}</span></div>
                  <div class="criteria-row"><span class="crit-label">Nivel 2 — Sentimiento:</span> <span>{chk(n2)} Short: {sentiment:.1f}% | Alpha Vantage score: {av_score:.3f}</span></div>
                  <div class="criteria-row"><span class="crit-label">Nivel 3 — Tendencia H4/H1:</span> <span>{chk(n3)} Alineacion estructural</span></div>
                  <div class="criteria-row"><span class="crit-label">Nivel 4 — Patron M15:</span> <span>{chk(n4)} Señal tecnica de entrada</span></div>
                  <div class="criteria-row" style="margin-top:8px;font-style:italic;color:#94a3b8"><span class="crit-label">Razon final:</span> <span>{reason}</span></div>
                </div>
                <div class="detail-section">
                  <div class="detail-title">📊 Ejecucion del Trade</div>
                  <div class="criteria-row"><span class="crit-label">Direccion:</span> <span style="color:{action_color};font-weight:600">{action} EURUSD</span></div>
                  <div class="criteria-row"><span class="crit-label">Entrada:</span> <span>{entry_p:.5f}</span></div>
                  <div class="criteria-row"><span class="crit-label">Salida:</span> <span>{exit_p:.5f}</span></div>
                  <div class="criteria-row"><span class="crit-label">Resultado:</span> <span style="color:{outcome_color};font-weight:600">{outcome} ({pips:+.1f} pips)</span></div>
                  <div class="criteria-row"><span class="crit-label">Lote:</span> <span>{lot:.2f} (riesgo calculado)</span></div>
                  <div class="criteria-row"><span class="crit-label">PnL:</span> <span style="color:{pnl_color};font-weight:700">${pnl:+.4f}</span></div>
                  <div class="criteria-row"><span class="crit-label">Duracion:</span> <span>{duration:.2f} horas</span></div>
                  <div class="criteria-row"><span class="crit-label">Capital tras trade:</span> <span>${cap_after:.2f}</span></div>
                </div>
              </div>
            </div>
          </td>
        </tr>
        """)

    html = f"""
    <div class="table-wrapper">
      <table class="trades-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Fecha/Hora</th>
            <th>Tipo</th>
            <th>Fase</th>
            <th style="text-align:right">Lote</th>
            <th style="text-align:right">Entrada</th>
            <th style="text-align:right">Pips</th>
            <th style="text-align:right">PnL</th>
            <th>Resultado</th>
            <th style="text-align:right">Duracion</th>
            <th style="text-align:right">Capital</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {"".join(rows)}
        </tbody>
      </table>
    </div>
    """
    return html


# ─────────────────────────────────────────────────────────────
# TABLA DE PROYECCIONES TEMPORALES
# ─────────────────────────────────────────────────────────────

def _build_projections_table(proj: dict, initial_capital: float) -> str:
    if not proj:
        return ""

    def row(periodo, n_trades, p5, p50, p95):
        p5_color  = "#ef4444" if p5 < initial_capital else "#22c55e"
        p50_color = "#22c55e" if p50 > initial_capital else "#ef4444"
        p95_color = "#22c55e"
        ret_p50   = (p50 - initial_capital) / initial_capital * 100
        return f"""
        <tr>
          <td style="font-weight:600;color:#e2e8f0">{periodo}</td>
          <td style="text-align:center;color:#94a3b8">{n_trades:.1f}</td>
          <td style="text-align:right;color:{p5_color};font-weight:600">${p5:.2f}</td>
          <td style="text-align:right;color:{p50_color};font-weight:700;font-size:1.05em">${p50:.2f}</td>
          <td style="text-align:right;color:{p95_color};font-weight:600">${p95:.2f}</td>
          <td style="text-align:right;color:{p50_color}">{ret_p50:+.1f}%</td>
        </tr>"""

    d  = proj.get("dia",    {})
    s  = proj.get("semana", {})
    m  = proj.get("mes",    {})
    a  = proj.get("ano",    {})

    return f"""
    <table class="metrics-table">
      <thead>
        <tr>
          <th>Horizonte</th>
          <th style="text-align:center">Trades estimados</th>
          <th style="text-align:right">Pesimista (P5)</th>
          <th style="text-align:right">Realista (P50)</th>
          <th style="text-align:right">Optimista (P95)</th>
          <th style="text-align:right">Retorno esperado</th>
        </tr>
      </thead>
      <tbody>
        {row("📅 1 Día",    proj.get("trades_por_dia",    1), d.get("p5",initial_capital), d.get("p50",initial_capital), d.get("p95",initial_capital))}
        {row("📅 1 Semana", proj.get("trades_por_semana", 5), s.get("p5",initial_capital), s.get("p50",initial_capital), s.get("p95",initial_capital))}
        {row("📅 1 Mes",    proj.get("trades_por_mes",   20), m.get("p5",initial_capital), m.get("p50",initial_capital), m.get("p95",initial_capital))}
        {row("📅 1 Año",    proj.get("trades_por_ano",  240), a.get("p5",initial_capital), a.get("p50",initial_capital), a.get("p95",initial_capital))}
      </tbody>
    </table>
    <p style="font-size:0.78rem;color:#94a3b8;margin-top:8px">
      * Proyeccion estadistica basada en la distribucion de PnL del backtest (CLT). El escenario real depende del
      numero de sesiones operadas y la volatilidad del mercado. Capital inicial: <strong>${initial_capital:.2f}</strong>.
    </p>
    """


# ─────────────────────────────────────────────────────────────
# GENERACION DEL REPORTE HTML COMPLETO
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
    proj    = compute_time_projections(trades_df, initial_capital, mc_results)

    # --- Guardar CSV de trades ---
    csv_path = OUTPUT_DIR / "trades.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"[Report] Trades guardados en {csv_path}")

    # --- Generar graficas ---
    fig_equity    = _fig_equity_curve(trades_df, initial_capital)
    fig_hist      = _fig_pnl_histogram(trades_df)
    fig_donut     = _fig_outcome_donut(trades_df)
    fig_action    = _fig_action_donut(trades_df)
    fig_monthly   = _fig_monthly_pnl(trades_df)
    fig_weekly    = _fig_weekly_pnl(trades_df)
    fig_phase     = _fig_phase_distribution(trades_df)
    fig_mc        = _fig_monte_carlo(mc_results)
    fig_mc_final  = _fig_mc_final_distribution(mc_results)
    fig_mc_dd     = _fig_mc_drawdown_distribution(mc_results)

    def to_div(fig):
        return fig.to_html(full_html=False, include_plotlyjs=False)

    # --- Veredicto WDC ---
    wdc_verdict = _compute_wdc_verdict(metrics, stats)

    # --- Tabla metricas backtest ---
    def metric_row(label, value):
        return f"""<tr><td>{label}</td><td><strong>{value}</strong></td></tr>"""

    backtest_table = f"""
    <table class="metrics-table">
      <tr><th>Metrica</th><th>Valor</th></tr>
      {metric_row('Total Trades', metrics.get('total_trades', 0))}
      {metric_row('Wins / Losses', f"{metrics.get('wins',0)} / {metrics.get('losses',0)}")}
      {metric_row('BUY / SELL', f"{metrics.get('buy_count',0)} / {metrics.get('sell_count',0)}")}
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

    trades_table_html = _build_trades_table(trades_df)
    projections_html  = _build_projections_table(proj, initial_capital)

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
      --surface2: #263348;
      --border:   #334155;
      --text:     #e2e8f0;
      --muted:    #94a3b8;
      --green:    #22c55e;
      --red:      #ef4444;
      --yellow:   #f59e0b;
      --blue:     #3b82f6;
      --purple:   #8b5cf6;
      --cyan:     #00d4aa;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; padding: 24px; max-width: 1400px; margin: 0 auto; }}
    h1   {{ font-size: 1.9rem; font-weight: 800; margin-bottom: 4px; background: linear-gradient(135deg,#00d4aa,#3b82f6); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    h2   {{ font-size: 1.15rem; font-weight: 700; color: var(--text); margin: 36px 0 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
    .subtitle {{ color: var(--muted); font-size: 0.88rem; margin-bottom: 32px; }}
    .grid-2  {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
    .grid-3  {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
    .grid-4  {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
    .card    {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 28px; }}
    .kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; text-align: center; transition: border-color 0.2s; }}
    .kpi:hover {{ border-color: var(--blue); }}
    .kpi-label {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
    .kpi-value {{ font-size: 1.5rem; font-weight: 800; }}
    .verdict-box {{ border-radius: 12px; padding: 20px 24px; margin-bottom: 32px; border: 2px solid; }}
    .verdict-title {{ font-size: 1.4rem; font-weight: 700; margin-bottom: 6px; }}
    .verdict-desc  {{ font-size: 0.92rem; color: var(--muted); line-height: 1.6; }}
    .metrics-table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    .metrics-table th {{ text-align: left; padding: 9px 12px; background: var(--bg); color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }}
    .metrics-table td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); }}
    .metrics-table tr:last-child td {{ border-bottom: none; }}
    .metrics-table tbody tr:hover {{ background: var(--surface2); }}

    /* === TABLA DE TRADES === */
    .table-wrapper {{ overflow-x: auto; border-radius: 10px; border: 1px solid var(--border); }}
    .trades-table {{ width: 100%; border-collapse: collapse; font-size: 0.86rem; min-width: 900px; }}
    .trades-table thead tr {{ background: #0f172a; }}
    .trades-table th {{ padding: 10px 12px; text-align: left; color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); white-space: nowrap; }}
    .trade-row {{ background: var(--surface); transition: background 0.15s; }}
    .trade-row:hover {{ background: var(--surface2); }}
    .trade-row td {{ padding: 9px 12px; border-bottom: 1px solid var(--border)40; white-space: nowrap; }}
    .detail-row td {{ padding: 0; background: #0d1a2e; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.03em; }}

    /* === DETALLE DE TRADE === */
    .detail-box {{ padding: 16px 20px; border-top: 1px solid var(--border); animation: slideDown 0.2s ease; }}
    @keyframes slideDown {{ from {{ opacity:0; transform:translateY(-6px); }} to {{ opacity:1; transform:translateY(0); }} }}
    .detail-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
    .detail-section {{ }}
    .detail-title {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); margin-bottom: 10px; font-weight: 700; }}
    .criteria-row {{ display: flex; gap: 8px; align-items: flex-start; padding: 4px 0; font-size: 0.84rem; border-bottom: 1px solid var(--border)30; }}
    .crit-label {{ color: var(--muted); min-width: 220px; flex-shrink: 0; font-size: 0.82rem; }}

    /* === PROYECCIONES === */
    .proj-note {{ font-size: 0.78rem; color: var(--muted); margin-top: 8px; }}

    .full-width {{ grid-column: 1 / -1; }}
    @media (max-width: 900px) {{
      .grid-2, .grid-3, .grid-4 {{ grid-template-columns: 1fr; }}
      .detail-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <h1>&#x1F4CA; Backtest + Monte Carlo</h1>
  <p class="subtitle">WDC Confluence Strategy &middot; EURUSD M15/H1/H4 &middot; Generado: {ts}</p>

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
      <div class="kpi-value" style="color:{'var(--green)' if metrics.get('expectancy_usd',0) > 0 else 'var(--red)'}">\\${metrics.get('expectancy_usd',0):.3f}</div>
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
      <div class="kpi-value">\\${metrics.get('final_capital',0):.2f}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">BUY / SELL</div>
      <div class="kpi-value" style="font-size:1.2rem"><span style="color:var(--green)">{metrics.get('buy_count',0)}</span> / <span style="color:var(--red)">{metrics.get('sell_count',0)}</span></div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Total Trades</div>
      <div class="kpi-value">{metrics.get('total_trades',0)}</div>
    </div>
  </div>

  <!-- EQUITY CURVE -->
  <h2>&#x1F4C8; Curva de Equity &mdash; Backtest Real</h2>
  <div class="card">{to_div(fig_equity)}</div>

  <!-- PROYECCIONES TEMPORALES -->
  <h2>&#x23F1; Proyecciones Temporales (Dia / Semana / Mes / Ano)</h2>
  <div class="card">
    <p style="font-size:0.88rem;color:#94a3b8;margin-bottom:16px">
      Basado en la distribucion estadistica real de los {metrics.get('total_trades',0)} trades del backtest.
      El escenario <strong style="color:#22c55e">Optimista (P95)</strong> ocurre en 1 de cada 20 meses.
      El <strong style="color:#f59e0b">Realista (P50)</strong> es la mediana — lo que sucede la mitad del tiempo.
      El <strong style="color:#ef4444">Pesimista (P5)</strong> solo en los peores meses del ano.
    </p>
    {projections_html}
  </div>

  <!-- ANALISIS DE TRADES -->
  <h2>&#x1F4CB; Analisis de Trades</h2>
  <div class="grid-4">
    <div class="card">{to_div(fig_donut)}</div>
    <div class="card">{to_div(fig_action)}</div>
    <div class="card">{to_div(fig_hist)}</div>
    <div class="card">{to_div(fig_phase)}</div>
  </div>

  <!-- PnL TEMPORAL -->
  <h2>&#x1F4C5; PnL por Periodo</h2>
  <div class="grid-2">
    <div class="card">{to_div(fig_weekly)}</div>
    <div class="card">{to_div(fig_monthly)}</div>
  </div>

  <!-- MONTE CARLO -->
  <h2>&#x1F3B2; Monte Carlo &mdash; {stats['n_simulations']} Simulaciones</h2>
  <div class="card" style="margin-bottom:20px">{to_div(fig_mc)}</div>
  <div class="grid-2">
    <div class="card">{to_div(fig_mc_final)}</div>
    <div class="card">{to_div(fig_mc_dd)}</div>
  </div>

  <!-- METRICAS DETALLADAS -->
  <h2>&#x1F4CA; Metricas Detalladas</h2>
  <div class="grid-2">
    <div class="card">
      <h3 style="margin-bottom:12px;font-size:1rem;color:#e2e8f0">Backtest</h3>
      {backtest_table}
    </div>
    <div class="card">
      <h3 style="margin-bottom:12px;font-size:1rem;color:#e2e8f0">Monte Carlo</h3>
      {mc_table}
    </div>
  </div>

  <!-- HISTORIAL DE TRADES DETALLADO -->
  <h2>&#x1F4DD; Historial de Trades &mdash; Criterios y Decision</h2>
  <p style="color:#94a3b8;font-size:0.85rem;margin-bottom:12px">
    Haz click en cualquier fila para ver el <strong>tipo de operacion, los 4 criterios de confluencia y la razon exacta</strong> de cada trade.
  </p>
  <div class="card" style="padding: 0; overflow:hidden;">
    {trades_table_html}
  </div>

  <p style="color:var(--muted);font-size:0.78rem;margin-top:32px;text-align:center">
    bot_trading &middot; WDC Confluence Strategy &middot; EURUSD M15/H1/H4 &middot; {ts}
  </p>

  <script>
    function toggleDetail(id) {{
      const row = document.getElementById(id);
      if (!row) return;
      const isVisible = row.style.display !== 'none';
      row.style.display = isVisible ? 'none' : 'table-row';
      // flip arrow on trigger row
      const triggerRow = row.previousElementSibling;
      if (triggerRow) {{
        const arrow = triggerRow.querySelector('td:last-child');
        if (arrow) arrow.textContent = isVisible ? '▼' : '▲';
      }}
    }}
  </script>
</body>
</html>"""

    html_path = OUTPUT_DIR / "backtest_report.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[Report] Reporte HTML v3 generado: {html_path}")
    return html_path
