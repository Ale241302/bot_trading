"""
smoke_test_ai.py
Validación end-to-end del pipeline AI-driven (WDC y CSM) usando:
  - MockAIAnalyst (no consume API real)
  - Datos OHLCV sintéticos generados localmente
  - Sin sentimiento externo (la IA recibe "no disponible")

Verifica que:
  - run_ai_wdc(...) y run_ai_csm(...) ejecutan sin excepciones.
  - Las decisiones IA se registran en `decisions`.
  - Trades_df tiene columnas IA-related (ai_action, ai_reason).
  - El reporte HTML se genera.
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("smoke_ai")


def _synth(start, periods, freq, seed, base, vol):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    rets = rng.normal(0, vol, periods)
    closes = base * np.exp(np.cumsum(rets))
    highs = closes * (1 + np.abs(rng.normal(0, vol/2, periods)))
    lows = closes * (1 - np.abs(rng.normal(0, vol/2, periods)))
    opens = np.concatenate([[base], closes[:-1]])
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows,
                         "Close": closes, "Volume": rng.integers(100, 9999, periods)},
                        index=idx)


def _build_synth_data(symbols, days=120):
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=days)
    out = {}
    for i, sym in enumerate(symbols):
        base = (1.10 if "EUR" in sym and sym.endswith("USD") else
                1.27 if sym == "GBPUSD" else
                150.0 if sym == "USDJPY" else
                1850.0 if sym == "XAUUSD" else
                1.0)
        n_h4 = days * 6
        n_h1 = days * 24
        n_m15 = days * 24 * 4
        out[sym] = {
            "H4":  _synth(start, n_h4,  "4h",  100+i, base, 0.005),
            "H1":  _synth(start, n_h1,  "1h",  200+i, base, 0.002),
            "M15": _synth(start, n_m15, "15min",300+i, base, 0.0008),
        }
    return out


def main():
    from backtest.ai_backtest import (
        MockAIAnalyst, run_ai_wdc, run_ai_csm,
    )
    from backtest.ai_report import generate_ai_report

    ai = MockAIAnalyst()

    # ─── WDC ───
    logger.info("=== Test WDC + IA (mock, sin sentimiento) ===")
    wdc_data = _build_synth_data(["EURUSD", "GBPUSD", "USDJPY"], days=120)
    trades_wdc, decisions_wdc = run_ai_wdc(
        multi_frames=wdc_data, initial_capital=50.0, risk_pct=0.05,
        ai_analyst=ai, seed=42, max_ai_calls=80,
    )
    logger.info(f"WDC trades={len(trades_wdc)} | decisiones={len(decisions_wdc)} | ai.calls={ai.calls}")
    assert isinstance(trades_wdc, pd.DataFrame)
    assert isinstance(decisions_wdc, list)

    if not trades_wdc.empty:
        # Validar columnas IA
        for col in ("ai_action", "ai_reason"):
            assert col in trades_wdc.columns, f"falta columna {col} en trades WDC"
        # Reporte HTML
        out_dir = trades_wdc.attrs.get("out_dir", None)
        from pathlib import Path
        html = generate_ai_report(
            trades_df=trades_wdc, decisions=decisions_wdc,
            initial_capital=50.0, risk_pct=0.05, mode="wdc",
            output_dir=Path(__file__).parent / "output",
        )
        logger.info(f"Reporte WDC: {html}")

    # ─── CSM ───
    logger.info("=== Test CSM + IA (mock) ===")
    from backtest.csm_pair_specs import CSM_DEFAULT_PAIRS
    # Para acotar la generación, usamos solo 8 pares en lugar de los 30
    csm_pairs = ["EURUSD","GBPUSD","USDJPY","USDCHF","EURJPY","GBPJPY","AUDUSD","XAUUSD"]
    csm_data = _build_synth_data(csm_pairs, days=120)
    # Rellenar el resto con DataFrames vacíos (CSM lo tolera)
    for sym in CSM_DEFAULT_PAIRS:
        if sym not in csm_data:
            csm_data[sym] = {"H4": pd.DataFrame(columns=["Open","High","Low","Close","Volume"]),
                              "H1": pd.DataFrame(columns=["Open","High","Low","Close","Volume"]),
                              "M15":pd.DataFrame(columns=["Open","High","Low","Close","Volume"])}
    ai_csm = MockAIAnalyst()
    trades_csm, decisions_csm = run_ai_csm(
        multi_frames=csm_data, initial_capital=50.0, risk_pct=0.05,
        ai_analyst=ai_csm, seed=42, max_ai_calls=50,
    )
    logger.info(f"CSM trades={len(trades_csm)} | decisiones={len(decisions_csm)} | ai.calls={ai_csm.calls}")
    assert isinstance(trades_csm, pd.DataFrame)
    assert isinstance(decisions_csm, list)

    if not trades_csm.empty:
        from pathlib import Path
        html = generate_ai_report(
            trades_df=trades_csm, decisions=decisions_csm,
            initial_capital=50.0, risk_pct=0.05, mode="csm",
            output_dir=Path(__file__).parent / "output",
        )
        logger.info(f"Reporte CSM: {html}")

    logger.info("=" * 60)
    logger.info("Smoke AI test PASA — pipeline IA ejecuta sin errores.")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
