"""
run_csm_backtest.py
CLI para correr el backtest de la estrategia CSM Sniper.

Uso:
  python -m backtest.run_csm_backtest
  python -m backtest.run_csm_backtest --years 2 --sims 1000 --capital 50 --risk 0.30
  python -m backtest.run_csm_backtest --no-cache --exclude XAU,XAG

Args:
  --years     int    : años de histórico yfinance (default 2, max 2)
  --capital   float  : capital inicial USD (default 50)
  --risk      float  : fracción del capital por trade (default 0.30)
  --sims      int    : simulaciones Monte Carlo (default 1000)
  --seed      int    : semilla determinista (default 42)
  --no-cache         : forzar re-descarga de datos
  --exclude   str    : divisas a excluir del CSM, csv (ej. "XAU,XAG")
"""
import argparse
import logging
import pickle
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_csm_backtest")

CACHE_NAME = "csm_data_cache.pkl"


def main():
    parser = argparse.ArgumentParser(
        description="CSM Sniper — Backtest + Monte Carlo (estrategia paralela a WDC)"
    )
    parser.add_argument("--years",   type=int,   default=2)
    parser.add_argument("--capital", type=float, default=50.0)
    parser.add_argument("--risk",    type=float, default=0.05,
                        help="Fracción de capital arriesgada por trade (default 0.05 = 5%%, profesional)")
    parser.add_argument("--sims",    type=int,   default=1000)
    parser.add_argument("--seed",    type=int,   default=42)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--exclude", type=str, default="",
                        help="Divisas a excluir del CSM scoring, csv (ej. 'XAU,XAG')")
    args = parser.parse_args()

    if not (0 < args.risk < 1):
        logger.error(f"--risk debe estar en (0,1). Recibido: {args.risk}")
        sys.exit(2)

    excluded = {c.strip().upper() for c in args.exclude.split(",") if c.strip()}

    logger.info("=" * 60)
    logger.info("  CSM Sniper — Backtest + Monte Carlo")
    logger.info("=" * 60)
    logger.info(f"  Capital inicial : ${args.capital:.2f}")
    logger.info(f"  Riesgo / trade  : {args.risk*100:.0f}%")
    logger.info(f"  Histórico       : {args.years} año(s)")
    logger.info(f"  Sims Monte Carlo: {args.sims}")
    logger.info(f"  Seed            : {args.seed}")
    logger.info(f"  Excluidos       : {','.join(sorted(excluded)) if excluded else '(ninguno)'}")
    logger.info("=" * 60)

    # 1. Datos
    cache_path = Path(__file__).parent / "output" / CACHE_NAME
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    multi_frames = None
    if cache_path.exists() and not args.no_cache:
        logger.info(f"Cargando datos desde cache ({cache_path.name})…")
        try:
            with open(cache_path, "rb") as f:
                multi_frames = pickle.load(f)
        except Exception as e:
            logger.warning(f"Cache ilegible ({e}), re-descargando.")
            multi_frames = None

    if multi_frames is None:
        from .csm_data_loader import download_csm_data
        logger.info(f"Descargando datos para 30 instrumentos ({args.years} año(s))…")
        multi_frames = download_csm_data(years=args.years)
        with open(cache_path, "wb") as f:
            pickle.dump(multi_frames, f)
        logger.info("Cache guardado.")

    n_with_data = sum(1 for f in multi_frames.values() if not f.get("M15", []).empty)
    logger.info(f"Pares con datos M15: {n_with_data}/{len(multi_frames)}")

    # 2. Backtest
    from .csm_runner import run_csm_backtest
    logger.info("Ejecutando backtest semanal…")
    trades_df, picks = run_csm_backtest(
        multi_frames,
        initial_capital=args.capital,
        risk_pct=args.risk,
        seed=args.seed,
        excluded_currencies=excluded,
    )
    logger.info(f"Trades simulados: {len(trades_df)} | Picks semanales: {len(picks)}")

    if trades_df.empty:
        logger.error("Backtest vacío — el CSM no encontró señales válidas en este histórico.")
        sys.exit(1)

    # Guardar CSV
    out_dir = Path(__file__).parent / "output"
    csv_path = out_dir / "csm_trades.csv"
    trades_df.to_csv(csv_path, index=False)
    logger.info(f"Trades CSV: {csv_path}")

    # 3. Monte Carlo
    from .csm_monte_carlo import run_csm_monte_carlo
    logger.info(f"Monte Carlo {args.sims} simulaciones…")
    mc_results = run_csm_monte_carlo(
        trades_df,
        initial_capital=args.capital,
        n_simulations=args.sims,
        seed=args.seed,
        risk_pct=args.risk,
    )

    # 4. Reporte (opcional: requiere plotly)
    html_path = None
    try:
        from .csm_report import generate_csm_report
        html_path = generate_csm_report(
            trades_df=trades_df,
            mc_results=mc_results,
            week_picks=picks,
            initial_capital=args.capital,
            risk_pct=args.risk,
        )
    except ImportError as e:
        logger.warning(
            f"Reporte HTML omitido ({e}). "
            "Para activarlo: pip install -r requirements-backtest.txt"
        )

    stats = mc_results["stats"]
    logger.info("=" * 60)
    logger.info("  Resumen")
    logger.info("=" * 60)
    logger.info(f"  Capital final histórico : ${trades_df['capital_end'].iloc[-1]:.2f}")
    logger.info(f"  Win rate                : {(trades_df['pnl_usd']>0).mean()*100:.1f}%")
    logger.info(f"  Ruina MC                : {stats['ruin_pct']:.1f}%")
    logger.info(f"  Duplican MC             : {stats['double_pct']:.1f}%")
    logger.info(f"  Mediana final MC        : ${stats['median_final']:.2f}")
    if html_path:
        logger.info(f"  Reporte HTML            : {html_path}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
