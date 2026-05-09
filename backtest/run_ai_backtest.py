"""
run_ai_backtest.py
CLI para correr backtest AI-driven (la IA toma cada decisión).

Modos:
  --mode wdc  → backtest WDC clásico con IA (signal_engine + AIAnalyst)
  --mode csm  → backtest CSM Sniper con IA confirmando cada pullback

Uso típico:
  # Run CSM con IA, riesgo 5%, 200 calls max
  python -m backtest.run_ai_backtest --mode csm --risk 0.05 --max-ai-calls 200

  # Run WDC con dry-run (sin gastar API, usa MockAIAnalyst)
  python -m backtest.run_ai_backtest --mode wdc --dry-run

  # Run WDC, modelo distinto
  python -m backtest.run_ai_backtest --mode wdc --model gpt-4o-mini

Variables de entorno relevantes:
  - OPENAI_API_KEY  : si falta, se fuerza --dry-run
  - OPENAI_MODEL    : default gpt-4o (override con --model)

Sentimiento: NO se inyecta. La IA recibe "Sentimiento Myfxbook: no disponible"
y debe decidir solo con velas + tendencia + patrón + capital + fase.
"""
import argparse
import logging
import os
import pickle
import sys
from pathlib import Path

from dotenv import load_dotenv

# Cargar .env del proyecto antes de leer OPENAI_API_KEY.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_ai_backtest")


def _load_or_download_data(mode: str, years: int, no_cache: bool):
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode == "wdc":
        cache = out_dir / f"data_cache_EURUSD_GBPUSD_USDJPY.pkl"
        from .data_loader import download_data
        loader = lambda: download_data(years=years, symbols=["EURUSD", "GBPUSD", "USDJPY"])
    else:
        cache = out_dir / "csm_data_cache.pkl"
        from .csm_data_loader import download_csm_data
        loader = lambda: download_csm_data(years=years)

    if cache.exists() and not no_cache:
        logger.info(f"Cargando datos desde cache ({cache.name})…")
        try:
            with open(cache, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"Cache ilegible ({e}); re-descargando.")

    logger.info("Descargando datos…")
    data = loader()
    with open(cache, "wb") as f:
        pickle.dump(data, f)
    return data


def main():
    parser = argparse.ArgumentParser(description="Backtest AI-driven (WDC o CSM)")
    parser.add_argument("--mode", choices=["wdc", "csm"], required=True)
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--capital", type=float, default=50.0)
    parser.add_argument("--risk", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--max-ai-calls", type=int, default=200)
    parser.add_argument("--sims", type=int, default=1000,
                        help="Simulaciones Monte Carlo (default 1000, 0 = saltar)")
    parser.add_argument("--dry-run", action="store_true",
                        help="No consultar OpenAI; usar MockAIAnalyst")
    parser.add_argument("--model", type=str, default=None,
                        help="OpenAI model override (default desde OPENAI_MODEL env)")
    parser.add_argument("--exclude", type=str, default="",
                        help="(CSM) Divisas a excluir, csv (ej. 'XAU,XAG')")
    args = parser.parse_args()

    # Forzar dry-run si no hay clave
    if not args.dry_run and not os.getenv("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY no configurada → forzando --dry-run.")
        args.dry_run = True

    if args.model:
        os.environ["OPENAI_MODEL"] = args.model

    logger.info("=" * 64)
    logger.info(f"  Backtest AI-driven — modo {args.mode.upper()}")
    logger.info("=" * 64)
    logger.info(f"  Capital inicial : ${args.capital:.2f}")
    logger.info(f"  Riesgo / trade  : {args.risk*100:.1f}%")
    logger.info(f"  Histórico       : {args.years} año(s)")
    logger.info(f"  Max AI calls    : {args.max_ai_calls}")
    logger.info(f"  Dry-run         : {args.dry_run}")
    if not args.dry_run:
        logger.info(f"  Modelo OpenAI   : {os.getenv('OPENAI_MODEL', 'gpt-4o')}")
    logger.info("=" * 64)

    # Datos
    multi_frames = _load_or_download_data(args.mode, args.years, args.no_cache)

    # AI analyst (real o mock)
    if args.dry_run:
        from .ai_backtest import MockAIAnalyst
        ai = MockAIAnalyst()
        logger.info("AI: MockAIAnalyst (dry-run, sin API).")
    else:
        from modules.ai_analyst import AIAnalyst
        ai = AIAnalyst()
        logger.info("AI: AIAnalyst (gastará API real).")

    # Run
    from .ai_backtest import run_ai_wdc, run_ai_csm
    if args.mode == "wdc":
        trades_df, decisions = run_ai_wdc(
            multi_frames=multi_frames,
            initial_capital=args.capital,
            risk_pct=args.risk,
            ai_analyst=ai,
            seed=args.seed,
            max_ai_calls=args.max_ai_calls,
        )
    else:
        excluded = {c.strip().upper() for c in args.exclude.split(",") if c.strip()}
        trades_df, decisions = run_ai_csm(
            multi_frames=multi_frames,
            initial_capital=args.capital,
            risk_pct=args.risk,
            ai_analyst=ai,
            seed=args.seed,
            max_ai_calls=args.max_ai_calls,
            excluded_currencies=excluded,
        )

    logger.info(f"Trades: {len(trades_df)} | Decisiones IA: {len(decisions)}")

    if trades_df.empty:
        logger.warning("Backtest sin trades — la IA dijo HOLD a todos los candidatos "
                       "o no hubo candidatos técnicos.")

    # Persistir
    out_dir = Path(__file__).parent / "output"
    suffix = args.mode + ("_dry" if args.dry_run else "_real")
    trades_csv = out_dir / f"ai_trades_{suffix}.csv"
    decisions_csv = out_dir / f"ai_decisions_{suffix}.csv"
    if not trades_df.empty:
        trades_df.to_csv(trades_csv, index=False)
        logger.info(f"Trades CSV: {trades_csv}")
    import pandas as pd
    pd.DataFrame(decisions).to_csv(decisions_csv, index=False)
    logger.info(f"Decisiones IA CSV: {decisions_csv}")

    # Monte Carlo (reusa csm_monte_carlo, lee SL/TP/pip_value de cada trade)
    mc_results = None
    if not trades_df.empty and args.sims > 0:
        # Asegurar columna pip_value_per_lot (WDC viejo no la tiene; la inferimos del par)
        if "pip_value_per_lot" not in trades_df.columns:
            from .pair_config import PAIR_SPECS
            trades_df["pip_value_per_lot"] = trades_df["symbol"].map(
                lambda s: PAIR_SPECS.get(s, {}).get("pip_value_per_lot", 10.0)
            )
        try:
            from .csm_monte_carlo import run_csm_monte_carlo
            logger.info(f"Monte Carlo {args.sims} simulaciones…")
            mc_results = run_csm_monte_carlo(
                trades_df,
                initial_capital=args.capital,
                n_simulations=args.sims,
                seed=args.seed,
                risk_pct=args.risk,
            )
        except Exception as e:
            logger.warning(f"Monte Carlo falló ({e}); reporte sin MC.")

    # Reporte HTML (opcional: requiere plotly)
    if not trades_df.empty:
        try:
            from .ai_report import generate_ai_report
            html_path = generate_ai_report(
                trades_df=trades_df,
                decisions=decisions,
                initial_capital=args.capital,
                risk_pct=args.risk,
                mode=args.mode,
                output_dir=out_dir,
                mc_results=mc_results,
            )
            logger.info(f"Reporte HTML: {html_path}")
        except ImportError as e:
            logger.warning(
                f"Reporte HTML omitido ({e}). "
                "Para activarlo: pip install -r requirements-backtest.txt"
            )

    logger.info("=" * 64)


if __name__ == "__main__":
    main()
