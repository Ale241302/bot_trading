"""
ai_report.py
Reporte HTML para backtests AI-driven. Incluye sección dedicada a las
decisiones de la IA: distribución BUY/SELL/HOLD, % de confirmación con
el técnico, motivos por los que la IA dijo HOLD pese a señal técnica.

Reusa parte de la estructura visual de csm_report.py y añade:
  - KPIs de comportamiento IA (calls, BUY/SELL/HOLD %, override rate)
  - Tabla expandible de decisiones (incluso las HOLD)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

logger = logging.getLogger(__name__)


def _kpis(trades_df: pd.DataFrame, decisions: list[dict], initial_capital: float) -> dict:
    if trades_df.empty:
        return {
            "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "capital_final": initial_capital, "drawdown": 0.0,
            "n_decisions": len(decisions),
        }

    wins = (trades_df["pnl_usd"] > 0).sum()
    n = len(trades_df)
    sum_win = trades_df.loc[trades_df["pnl_usd"] > 0, "pnl_usd"].sum()
    sum_loss = -trades_df.loc[trades_df["pnl_usd"] <= 0, "pnl_usd"].sum()
    pf = sum_win / sum_loss if sum_loss > 0 else float("inf")
    final = float(trades_df["capital_end"].iloc[-1])

    eq = np.concatenate([[initial_capital], trades_df["capital_end"].values])
    peak = np.maximum.accumulate(eq)
    dd = float(((peak - eq) / peak).max())

    return {
        "n_trades": n,
        "win_rate": wins / n * 100,
        "profit_factor": pf,
        "capital_final": final,
        "drawdown": dd * 100,
        "n_decisions": len(decisions),
    }


def _ai_behavior(decisions: list[dict]) -> dict:
    """Estadísticas sobre el comportamiento de la IA."""
    if not decisions:
        return {}
    df = pd.DataFrame(decisions)
    by_action = df["ai_action"].value_counts(dropna=False).to_dict()
    n = len(df)
    confirmed = ((df["ai_action"].isin(["BUY", "SELL"])) &
                 (df["ai_action"] == df["tech_action"])).sum()
    overridden_to_hold = (df["ai_action"] == "HOLD").sum()
    overridden_to_other = ((df["ai_action"].isin(["BUY", "SELL"])) &
                           (df["ai_action"] != df["tech_action"])).sum()

    return {
        "ai_calls": n,
        "by_action": by_action,
        "confirmation_rate": confirmed / n * 100 if n else 0,
        "hold_rate": overridden_to_hold / n * 100 if n else 0,
        "override_rate": overridden_to_other / n * 100 if n else 0,
    }


def _render_verdict(kpis: dict, mc_stats: dict, risk_pct: float) -> str:
    """Devuelve un bloque HTML con veredicto cualitativo basado en métricas."""
    if not kpis or kpis.get("n_trades", 0) == 0:
        return ""
    pf = kpis.get("profit_factor", 0)
    wr = kpis.get("win_rate", 0)
    dd = kpis.get("drawdown", 0)
    ruin = mc_stats.get("ruin_pct", None) if mc_stats else None
    double = mc_stats.get("double_pct", None) if mc_stats else None

    # Clasificación
    if pf >= 1.5 and wr >= 48 and (ruin is None or ruin <= 15):
        cls = "ok"
        title = "🟢 ESTRATEGIA VIABLE"
        msg = (
            f"PF {pf:.2f} · WR {wr:.1f}% · DD {dd:.1f}%."
            + (f" Ruina MC {ruin:.1f}% · Duplican {double:.1f}%." if ruin is not None else "")
            + " Cumple los umbrales mínimos. Se puede testear en demo varias semanas antes de live."
        )
    elif pf >= 1.2 and wr >= 42 and (ruin is None or ruin <= 25):
        cls = "warn"
        title = "🟡 MARGINAL"
        msg = (
            f"PF {pf:.2f} · WR {wr:.1f}% · DD {dd:.1f}%."
            + (f" Ruina MC {ruin:.1f}%." if ruin is not None else "")
            + " Edge positivo pero filo. Iterar prompt/filtros antes de demo."
        )
    else:
        cls = "bad"
        title = "🔴 NO APTO"
        msg = (
            f"PF {pf:.2f} · WR {wr:.1f}% · DD {dd:.1f}%."
            + (f" Ruina MC {ruin:.1f}%." if ruin is not None else "")
            + " Métricas insuficientes. NO operar — endurece filtros y vuelve a correr."
        )
    return f'<div class="verdict {cls}"><strong>{title}</strong><br>{msg}</div>'


def _render_mc_section(mc_stats: dict, initial_capital: float) -> str:
    """Sección Monte Carlo: ruina, duplican, mediana, percentiles."""
    if not mc_stats:
        return (
            '<div class="warning"><strong>Monte Carlo no disponible</strong>: '
            'corre con <code>--sims 1000</code> para añadir esta sección.</div>'
        )
    ruin = mc_stats.get("ruin_pct", 0)
    double = mc_stats.get("double_pct", 0)
    median = mc_stats.get("median_final", initial_capital)
    p5 = mc_stats.get("p5_final", initial_capital)
    p95 = mc_stats.get("p95_final", initial_capital)
    dd_p95 = mc_stats.get("p95_max_drawdown", 0) * 100
    n_sims = mc_stats.get("n_simulations", 0)
    return f"""
<h2>Monte Carlo ({n_sims} simulaciones)</h2>
<div class="kpi-grid">
  <div class="kpi mc"><div class="label">Ruina</div>
       <div class="value">{ruin:.1f}%</div></div>
  <div class="kpi mc"><div class="label">Duplican capital</div>
       <div class="value">{double:.1f}%</div></div>
  <div class="kpi mc"><div class="label">Mediana final</div>
       <div class="value">${median:.2f}</div></div>
  <div class="kpi mc"><div class="label">P5 (pesimista)</div>
       <div class="value">${p5:.2f}</div></div>
  <div class="kpi mc"><div class="label">P95 (optimista)</div>
       <div class="value">${p95:.2f}</div></div>
  <div class="kpi mc"><div class="label">DD máx P95</div>
       <div class="value">{dd_p95:.1f}%</div></div>
</div>"""


def generate_ai_report(
    trades_df: pd.DataFrame,
    decisions: list[dict],
    initial_capital: float,
    risk_pct: float,
    mode: str,                # "wdc" | "csm"
    output_dir: Path | None = None,
    mc_results: dict | None = None,
) -> Path:
    out = output_dir or (Path(__file__).parent / "output")
    out.mkdir(parents=True, exist_ok=True)

    kpis = _kpis(trades_df, decisions, initial_capital)
    ai = _ai_behavior(decisions)
    mc_stats = (mc_results or {}).get("stats", {})

    # ── Equity curve ──
    fig_eq = go.Figure()
    if not trades_df.empty:
        fig_eq.add_trace(go.Scatter(
            x=trades_df["exit_ts"], y=trades_df["capital_end"],
            mode="lines+markers",
            line=dict(color="#2E86AB", width=2),
            marker=dict(size=6),
            name="Capital",
        ))
        fig_eq.add_hline(y=initial_capital, line_dash="dash",
                         line_color="gray", annotation_text="Capital inicial")
    fig_eq.update_layout(
        title=f"Equity curve — modo {mode.upper()} con IA",
        xaxis_title="Fecha cierre", yaxis_title="USD",
        template="plotly_white", height=400,
    )

    # ── Distribución decisiones IA ──
    fig_ai = go.Figure()
    if ai.get("by_action"):
        labels = list(ai["by_action"].keys())
        values = list(ai["by_action"].values())
        colors = ["#27ae60" if l in ("BUY","SELL") else
                  "#e67e22" if l == "HOLD" else "#95a5a6" for l in labels]
        fig_ai.add_trace(go.Bar(
            x=[str(l) for l in labels], y=values,
            marker_color=colors, text=values, textposition="auto",
        ))
    fig_ai.update_layout(
        title="Distribución de decisiones IA",
        xaxis_title="Acción", yaxis_title="# llamadas",
        template="plotly_white", height=350,
    )

    # ── Tablas ──
    trades_html = "<p>Sin trades.</p>"
    if not trades_df.empty:
        cols = [c for c in [
            "entry_ts", "exit_ts", "symbol", "side",
            "ai_action", "ai_reason",
            "lot", "sl_pips", "tp_pips",
            "pnl_usd", "exit_reason",
            "capital_start", "capital_end",
        ] if c in trades_df.columns]
        df_disp = trades_df[cols].copy()
        for c in ("entry_ts", "exit_ts"):
            if c in df_disp.columns:
                df_disp[c] = pd.to_datetime(df_disp[c]).dt.strftime("%Y-%m-%d %H:%M")
        trades_html = df_disp.to_html(
            index=False, classes="trades-table",
            float_format=lambda x: f"{x:.2f}",
        )

    decisions_html = "<p>Sin decisiones IA.</p>"
    if decisions:
        ddf = pd.DataFrame(decisions)
        if "ts" in ddf.columns:
            ddf["ts"] = pd.to_datetime(ddf["ts"]).dt.strftime("%Y-%m-%d %H:%M")
        ddf = ddf.head(80)  # primeras 80 para no inflar
        decisions_html = ddf.to_html(
            index=False, classes="trades-table",
            float_format=lambda x: f"{x:.2f}",
        ) + f"<p style='font-size:0.85em;color:#666'>Mostrando primeras 80 de {len(decisions)} decisiones.</p>"

    # ── HTML ──
    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>AI Backtest — modo {mode.upper()}</title>
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
.kpi.ai {{ border-left-color: #8e44ad; }}
.kpi.mc {{ border-left-color: #27ae60; }}
.verdict {{ padding: 16px; border-radius: 6px; margin: 1.5rem 0; }}
.verdict.ok {{ background: #eafaf1; border-left: 6px solid #27ae60; }}
.verdict.warn {{ background: #fef5e7; border-left: 6px solid #e67e22; }}
.verdict.bad {{ background: #fdedec; border-left: 6px solid #c0392b; }}
.kpi .label {{ font-size: 0.8em; color: #666; text-transform: uppercase; }}
.kpi .value {{ font-size: 1.5em; font-weight: 600; margin-top: 4px; }}
table.trades-table {{ width: 100%; border-collapse: collapse; font-size: 0.82em; }}
table.trades-table th, table.trades-table td {{
    border: 1px solid #ddd; padding: 5px 7px; text-align: right; }}
table.trades-table th {{ background: #2E86AB; color: white; text-align: center; }}
table.trades-table td:nth-child(-n+5) {{ text-align: left; }}
table.trades-table tr:nth-child(even) {{ background: #f9f9f9; }}
.warning {{ background: #fdf2e8; border-left: 4px solid #d35400; padding: 12px;
             border-radius: 4px; margin: 1.5rem 0; }}
details summary {{ cursor: pointer; font-weight: 600; padding: 6px 0; }}
</style>
</head>
<body>
<h1>AI Backtest — modo {mode.upper()}</h1>
<p>Capital inicial <strong>${initial_capital:.2f}</strong> ·
   Riesgo/trade <strong>{risk_pct*100:.1f}%</strong> ·
   Trades <strong>{kpis['n_trades']}</strong> ·
   Decisiones IA <strong>{kpis['n_decisions']}</strong></p>

{_render_verdict(kpis, mc_stats, risk_pct)}

<h2>KPIs de trading</h2>
<div class="kpi-grid">
  <div class="kpi"><div class="label">Win rate</div>
       <div class="value">{kpis['win_rate']:.1f}%</div></div>
  <div class="kpi"><div class="label">Profit factor</div>
       <div class="value">{kpis['profit_factor']:.2f}</div></div>
  <div class="kpi"><div class="label">Capital final</div>
       <div class="value">${kpis['capital_final']:.2f}</div></div>
  <div class="kpi"><div class="label">Drawdown máx</div>
       <div class="value">{kpis['drawdown']:.1f}%</div></div>
</div>

<h2>Comportamiento de la IA</h2>
<div class="kpi-grid">
  <div class="kpi ai"><div class="label">Calls IA</div>
       <div class="value">{ai.get('ai_calls', 0)}</div></div>
  <div class="kpi ai"><div class="label">Confirma técnico</div>
       <div class="value">{ai.get('confirmation_rate', 0):.1f}%</div></div>
  <div class="kpi ai"><div class="label">Override → HOLD</div>
       <div class="value">{ai.get('hold_rate', 0):.1f}%</div></div>
  <div class="kpi ai"><div class="label">Override → otro</div>
       <div class="value">{ai.get('override_rate', 0):.1f}%</div></div>
</div>

<div class="warning">
  <strong>Nota:</strong> Este backtest no inyecta sentimiento externo
  (Myfxbook ni OANDA). La IA recibe <code>"Sentimiento Myfxbook: no disponible"</code>
  y decide solo con velas + tendencia + patrón + capital + fase. Esto es
  más conservador y reproducible que un sentimiento simulado.
</div>

{_render_mc_section(mc_stats, initial_capital)}

<h2>Equity curve</h2>
<div id="eq"></div>

<h2>Distribución de decisiones IA</h2>
<div id="ai"></div>

<details>
  <summary>Ver tabla de trades ejecutados ({kpis['n_trades']})</summary>
  {trades_html}
</details>

<details>
  <summary>Ver decisiones IA ({kpis['n_decisions']} en total)</summary>
  {decisions_html}
</details>

<script>
Plotly.newPlot('eq', {fig_eq.to_json()}, {{}}, {{responsive:true}});
Plotly.newPlot('ai', {fig_ai.to_json()}, {{}}, {{responsive:true}});
</script>
</body>
</html>
"""
    out_path = out / f"ai_report_{mode}.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info(f"[AI] Reporte: {out_path}")
    return out_path
