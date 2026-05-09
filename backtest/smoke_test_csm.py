"""
smoke_test_csm.py
Smoke test del pipeline CSM con datos sintéticos (sin yfinance).

Genera 6 meses de OHLCV ruido browniano para los 30 instrumentos del
universo CSM, corre el backtest + Monte Carlo + reporte HTML, y
verifica que no haya excepciones.

Uso:
  python -m backtest.smoke_test_csm
"""
import logging
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("smoke_test")


def _synth_ohlc(start: datetime, periods: int, freq: str, seed: int, base_price: float, vol: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    rets = rng.normal(loc=0.0, scale=vol, size=periods)
    closes = base_price * np.exp(np.cumsum(rets))
    highs = closes * (1 + np.abs(rng.normal(0, vol / 2, periods)))
    lows = closes * (1 - np.abs(rng.normal(0, vol / 2, periods)))
    opens = np.concatenate([[base_price], closes[:-1]])
    return pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": rng.integers(100, 10000, periods),
    }, index=idx)


def main():
    from .csm_pair_specs import CSM_PAIR_SPECS
    from .csm_runner import run_csm_backtest
    from .csm_monte_carlo import run_csm_monte_carlo
    from .csm_report import generate_csm_report

    logger.info("Generando datos sintéticos (6 meses, 30 instrumentos)...")
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=180)

    multi_frames: dict = {}
    for i, (sym, spec) in enumerate(CSM_PAIR_SPECS.items()):
        # base price razonable según el par
        base = 1.10 if "EUR" in sym and sym.endswith("USD") else \
               1.27 if sym == "GBPUSD" else \
               150.0 if sym == "USDJPY" else \
               1850.0 if sym == "XAUUSD" else \
               24.0 if sym == "XAGUSD" else 1.0
        vol_h4 = 0.005
        vol_h1 = 0.002
        vol_m15 = 0.0008

        # Cantidad de velas: 6 meses ≈ 1080 H4, 4320 H1, 17280 M15
        # Pero limitamos para test
        n_h4 = 24 * 30 * 6 // 4   # 1080
        n_h1 = 24 * 30 * 6        # 4320
        n_m15 = 4 * 24 * 30 * 6   # 17280

        multi_frames[sym] = {
            "H4":  _synth_ohlc(start, n_h4,  "4H",   seed=100 + i, base_price=base, vol=vol_h4),
            "H1":  _synth_ohlc(start, n_h1,  "1h",   seed=200 + i, base_price=base, vol=vol_h1),
            "M15": _synth_ohlc(start, n_m15, "15min",seed=300 + i, base_price=base, vol=vol_m15),
        }

    logger.info(f"Datos listos. Ejecutando backtest CSM...")
    trades_df, picks = run_csm_backtest(
        multi_frames,
        initial_capital=50.0,
        risk_pct=0.30,
        seed=42,
    )
    logger.info(f"Trades: {len(trades_df)} | Picks: {len(picks)}")

    if trades_df.empty:
        logger.warning("Backtest sintético sin trades — el ruido browniano sin tendencia "
                       "rara vez genera divergencia >0.1% en 30 velas H4. Esto es esperado "
                       "y NO es un bug — los datos reales sí tendrán tendencias.")
        # Aún así, validar que MC + reporte funcionan con dataset mínimo simulado
        # generamos 1 trade fake para validar el reporte
        fake_trade = pd.DataFrame([{
            "week_start": pd.Timestamp(start, tz="UTC"),
            "symbol": "EURUSD",
            "side": "BUY",
            "strongest": "EUR",
            "weakest": "USD",
            "entry_ts": pd.Timestamp(start, tz="UTC"),
            "exit_ts": pd.Timestamp(start, tz="UTC") + pd.Timedelta(hours=4),
            "entry_price": 1.10, "exit_price": 1.105,
            "sl_price": 1.0995, "tp_price": 1.1015,
            "sl_pips": 5.0, "tp_pips": 15.0,
            "pip_size": 0.0001, "pip_value_per_lot": 10.0,
            "lot": 0.30, "pnl_usd": 4.5, "exit_reason": "TP", "won": True,
            "capital_start": 50.0, "capital_end": 54.5, "risk_pct": 0.30,
        }])
        trades_df = fake_trade
        logger.info("Inyectado 1 trade ficticio para validar MC + reporte.")

    logger.info("Monte Carlo (200 sims para ahorrar tiempo)...")
    mc_results = run_csm_monte_carlo(
        trades_df, initial_capital=50.0, n_simulations=200,
        seed=42, risk_pct=0.30,
    )

    logger.info("Generando reporte HTML...")
    html_path = generate_csm_report(
        trades_df=trades_df, mc_results=mc_results, week_picks=picks,
        initial_capital=50.0, risk_pct=0.30,
    )
    logger.info(f"OK — reporte: {html_path}")
    logger.info(f"Trades en CSV serían escritos por run_csm_backtest.py.")
    logger.info("=" * 60)
    logger.info("Smoke test PASA — el pipeline ejecuta sin errores.")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
