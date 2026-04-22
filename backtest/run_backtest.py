"""
run_backtest.py — Script de entrada del modulo backtest.

Uso:
  python -m backtest.run_backtest
  python -m backtest.run_backtest --years 2 --sims 1000 --capital 50
  python -m backtest.run_backtest --risk-mode 0.05
  python -m backtest.run_backtest --risk-mode hist
  python -m backtest.run_backtest --pairs EURUSD          # solo EURUSD
  python -m backtest.run_backtest --pairs EURUSD,GBPUSD   # 2 pares

v5 (2026-04-22) — Multi-par:
  - Nuevo argumento --pairs (default: EURUSD,GBPUSD,USDJPY)
  - Descarga datos para todos los pares seleccionados
  - Cache incluye hash de pares para evitar conflictos
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


def _parse_risk_mode(value: str) -> float | None:
    """Convierte el argumento --risk-mode a float o None."""
    if value.lower() in ("hist", "historico", "none"):
        return None
    try:
        v = float(value)
        if not (0 < v < 1):
            raise argparse.ArgumentTypeError(
                f"--risk-mode debe ser un decimal entre 0 y 1 (ej: 0.25) o 'hist'"
            )
        return v
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Valor invalido para --risk-mode: '{value}'. Usa 0.25, 0.10, o 'hist'."
        )


def main():
    parser = argparse.ArgumentParser(
        description="Backtest + Monte Carlo — WDC Confluence Strategy Multi-Par"
    )
    parser.add_argument("--years",     type=int,              default=2,      help="Anios de datos historicos (default: 2)")
    parser.add_argument("--sims",      type=int,              default=1000,   help="Simulaciones Monte Carlo (default: 1000)")
    parser.add_argument("--capital",   type=float,            default=50.0,   help="Capital inicial USD (default: 50)")
    parser.add_argument("--seed",      type=int,              default=42,     help="Semilla aleatoria (default: 42)")
    parser.add_argument("--no-cache",  action="store_true",                   help="Forzar re-descarga de datos")
    parser.add_argument(
        "--pairs",
        type=str,
        default="EURUSD,GBPUSD,USDJPY",
        help="Pares a operar separados por coma (default: EURUSD,GBPUSD,USDJPY)"
    )
    parser.add_argument(
        "--risk-mode",
        type=str,
        default="hist",
        metavar="RISK",
        help=(
            "Modo de riesgo para Monte Carlo. "
            "Opciones: 'hist' (PnL historico sin reescalar, default), "
            "'0.25' (Camino A — 25%% riesgo por trade), "
            "'0.10' (modo agresivo — 10%% riesgo). "
            "Ejemplo: --risk-mode 0.25"
        ),
    )
    args = parser.parse_args()

    risk_pct = _parse_risk_mode(args.risk_mode)
    symbols  = [s.strip().upper() for s in args.pairs.split(",") if s.strip()]

    # Generar nombre de cache unico para esta combinacion de pares
    pairs_key  = "_".join(sorted(symbols))
    cache_name = f"data_cache_{pairs_key}.pkl"

    logger.info("=" * 60)
    logger.info("  WDC Confluence Strategy — Backtest + Monte Carlo")
    logger.info("=" * 60)
    logger.info(f"  Pares          : {', '.join(symbols)}")
    logger.info(f"  Capital inicial : ${args.capital}")
    logger.info(f"  Periodo         : {args.years} anio(s)")
    logger.info(f"  Simulaciones MC : {args.sims}")
    logger.info(f"  Semilla         : {args.seed}")
    logger.info(f"  Modo riesgo MC  : {f'{risk_pct*100:.0f}% (Camino A)' if risk_pct else 'historico (lote 0.01)'}")
    logger.info("=" * 60)

    # ── 1. Cargar / descargar datos ────────────────────────────────────
    from .data_loader import download_data
    import pickle

    cache_path = Path(__file__).parent / "output" / cache_name
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists() and not args.no_cache:
        logger.info(f"Cargando datos desde cache ({cache_name}) — usa --no-cache para re-descargar")
        with open(cache_path, "rb") as f:
            multi_frames = pickle.load(f)
        for sym, frames in multi_frames.items():
            logger.info(f"  {sym}: M15={len(frames['M15'])} | H1={len(frames['H1'])} | H4={len(frames['H4'])}")
    else:
        logger.info(f"Descargando datos para {', '.join(symbols)} ({args.years} anio(s))...")
        multi_frames = download_data(years=args.years, symbols=symbols)
        with open(cache_path, "wb") as f:
            pickle.dump(multi_frames, f)
        logger.info("Datos guardados en cache")

    # Verificar que al menos 1 par tenga datos M15
    any_data = False
    for sym in symbols:
        if sym in multi_frames and not multi_frames[sym]["M15"].empty:
            any_data = True
        else:
            logger.warning(f"  {sym}: sin datos M15 — sera excluido del backtest")

    if not any_data:
        logger.error("No se pudieron descargar datos M15 para ningun par. Verifica tu conexion.")
        sys.exit(1)

    # ── 2. Ejecutar Backtest ───────────────────────────────────────────
    logger.info("Ejecutando backtest multi-par vela por vela...")
    from .backtest_runner import run_backtest
    trades_df = run_backtest(
        multi_frames,
        initial_capital=args.capital,
        seed=args.seed,
        symbols=symbols,
    )

    if trades_df.empty:
        logger.warning("El backtest no genero ningun trade. Revisa los datos o signal_engine.")
        sys.exit(1)

    logger.info(f"Backtest completado: {len(trades_df)} trades simulados")

    # ── 3. Monte Carlo ─────────────────────────────────────────────────
    logger.info(f"Ejecutando Monte Carlo ({args.sims} simulaciones) | modo: {'Camino A '+str(int(risk_pct*100))+'%' if risk_pct else 'historico'}...")
    from .monte_carlo import run_monte_carlo
    mc_results = run_monte_carlo(
        trades_df,
        initial_capital=args.capital,
        n_simulations=args.sims,
        seed=args.seed,
        risk_pct=risk_pct,
    )

    # ── 4. Reporte HTML ────────────────────────────────────────────────
    logger.info("Generando reporte HTML...")
    from .report import generate_report
    html_path = generate_report(
        trades_df,
        mc_results,
        initial_capital=args.capital,
    )

    logger.info("=" * 60)
    logger.info(f"  ✅ Reporte listo: {html_path}")
    logger.info(f"  📄 Trades CSV  : {html_path.parent / 'trades.csv'}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
