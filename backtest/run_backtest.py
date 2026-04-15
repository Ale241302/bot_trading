"""
run_backtest.py — Script de entrada del modulo backtest.

Uso:
  python -m backtest.run_backtest
  python -m backtest.run_backtest --years 2 --sims 1000 --capital 50
"""
import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_backtest")


def main():
    parser = argparse.ArgumentParser(
        description="Backtest + Monte Carlo — WDC Confluence Strategy EURUSD"
    )
    parser.add_argument("--years",   type=int,   default=2,    help="Anios de datos historicos (default: 2)")
    parser.add_argument("--sims",    type=int,   default=1000, help="Numero de simulaciones Monte Carlo (default: 1000)")
    parser.add_argument("--capital", type=float, default=50.0, help="Capital inicial USD (default: 50)")
    parser.add_argument("--seed",    type=int,   default=42,   help="Semilla aleatoria para reproducibilidad")
    parser.add_argument("--no-cache",action="store_true",      help="Forzar re-descarga de datos (ignorar cache)")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("  WDC Confluence Strategy — Backtest + Monte Carlo")
    logger.info("=" * 60)
    logger.info(f"  Capital inicial : ${args.capital}")
    logger.info(f"  Periodo         : {args.years} anio(s)")
    logger.info(f"  Simulaciones MC : {args.sims}")
    logger.info(f"  Semilla         : {args.seed}")
    logger.info("=" * 60)

    # ── 1. Cargar / descargar datos ────────────────────────────────────
    from .data_loader import download_data
    import pickle
    from pathlib import Path

    cache_path = Path(__file__).parent / "output" / "data_cache.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists() and not args.no_cache:
        logger.info("Cargando datos desde cache local (usa --no-cache para re-descargar)")
        with open(cache_path, "rb") as f:
            frames = pickle.load(f)
        logger.info(f"  M15: {len(frames['M15'])} velas | H1: {len(frames['H1'])} | H4: {len(frames['H4'])}")
    else:
        logger.info(f"Descargando datos EURUSD ({args.years} anio(s))...")
        frames = download_data(years=args.years)
        with open(cache_path, "wb") as f:
            pickle.dump(frames, f)
        logger.info("Datos guardados en cache")

    if frames["M15"].empty:
        logger.error("No se pudieron descargar datos M15. Verifica tu conexion a internet.")
        sys.exit(1)

    # ── 2. Ejecutar Backtest ───────────────────────────────────────────
    logger.info("Ejecutando backtest vela por vela...")
    from .backtest_runner import run_backtest
    trades_df = run_backtest(frames, initial_capital=args.capital, seed=args.seed)

    if trades_df.empty:
        logger.warning("El backtest no genero ningun trade. Revisa los datos o la logica del signal_engine.")
        sys.exit(1)

    logger.info(f"Backtest completado: {len(trades_df)} trades simulados")

    # ── 3. Monte Carlo ─────────────────────────────────────────────────
    logger.info(f"Ejecutando Monte Carlo ({args.sims} simulaciones)...")
    from .monte_carlo import run_monte_carlo
    mc_results = run_monte_carlo(
        trades_df,
        initial_capital=args.capital,
        n_simulations=args.sims,
        seed=args.seed,
    )

    # ── 4. Reporte HTML ────────────────────────────────────────────────
    logger.info("Generando reporte HTML...")
    from .report import generate_report
    html_path = generate_report(trades_df, mc_results, initial_capital=args.capital)

    logger.info("=" * 60)
    logger.info(f"  ✅ Reporte listo: {html_path}")
    logger.info(f"  📄 Trades CSV  : {html_path.parent / 'trades.csv'}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
