"""
data_loader.py
Descarga datos historicos OHLCV de multiples pares en M15, H1 y H4
usando yfinance. Devuelve DataFrames sincronizados por timestamp UTC.

v5 (2026-04-22) — Multi-par:
  - download_data() ahora acepta lista de simbolos y descarga para cada uno.
  - Retorna dict[str, dict[str, pd.DataFrame]]:
    {"EURUSD": {"M15": df, "H1": df, "H4": df}, "GBPUSD": {...}, ...}
  - Retrocompatible: si se pasa un solo simbolo, funciona igual.

Limites reales de yfinance por intervalo:
  - 15m : max 60 dias por request  -> se descarga en chunks de 55 dias
  - 1h  : max 730 dias (2 anios)   -> una sola llamada
  - 4h  : max 730 dias (2 anios)   -> una sola llamada
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import logging

from .pair_config import PAIR_SPECS, DEFAULT_PAIRS

logger = logging.getLogger(__name__)

# Limites duros de yfinance para datos intraday
_MAX_DAYS = {
    "1h":  729,   # justo bajo 730 para evitar errores de borde
    "4h":  729,
    "15m":  59,   # max 60 dias, usamos 59 para seguridad
}
_CHUNK_DAYS = {
    "15m": 55,    # chunks solapados para M15
}


def download_data(
    years: int = 2,
    symbols: list[str] | None = None,
) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Descarga datos para cada simbolo en `symbols` (default: DEFAULT_PAIRS).
    Para cada uno descarga M15, H1 y H4.

    Retorna dict anidado:
      { "EURUSD": {"M15": df, "H1": df, "H4": df},
        "GBPUSD": {"M15": df, "H1": df, "H4": df},
        "USDJPY": {"M15": df, "H1": df, "H4": df} }
    """
    if symbols is None:
        symbols = DEFAULT_PAIRS

    end = datetime.utcnow()
    requested_days = min(years * 365, 729)

    all_frames = {}

    for symbol in symbols:
        spec = PAIR_SPECS.get(symbol)
        if spec is None:
            logger.warning(f"Simbolo {symbol} no tiene spec en PAIR_SPECS, saltando.")
            continue

        yf_ticker = spec["yf_symbol"]
        logger.info(f"── Descargando {symbol} ({yf_ticker}) ──")

        frames = {}

        # ── H1 y H4: una sola llamada ──────────────────────────────────
        for tf, interval in [("H1", "1h"), ("H4", "4h")]:
            days = min(requested_days, _MAX_DAYS[interval])
            start = end - timedelta(days=days)
            logger.info(
                f"  {symbol} {tf} ({interval}) desde {start.date()} hasta {end.date()}"
            )
            df = yf.download(
                yf_ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=True,
                progress=False,
            )
            if not df.empty:
                df = _clean(df)
            else:
                df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            frames[tf] = df
            logger.info(f"    {tf}: {len(df)} velas descargadas")

        # ── M15: chunks de 55 dias ─────────────────────────────────────
        logger.info(f"  {symbol} M15 (chunks de 55 dias)...")
        chunk_size = _CHUNK_DAYS["15m"]
        m15_days = min(requested_days, _MAX_DAYS["15m"])
        chunks = []
        chunk_end = end
        days_covered = 0

        while days_covered < m15_days:
            remaining = m15_days - days_covered
            this_chunk = min(chunk_size, remaining)
            chunk_start = chunk_end - timedelta(days=this_chunk)

            df_chunk = yf.download(
                yf_ticker,
                start=chunk_start.strftime("%Y-%m-%d"),
                end=chunk_end.strftime("%Y-%m-%d"),
                interval="15m",
                auto_adjust=True,
                progress=False,
            )
            if not df_chunk.empty:
                chunks.append(_clean(df_chunk))

            days_covered += this_chunk
            chunk_end = chunk_start

        if chunks:
            m15 = pd.concat(chunks)
            m15 = m15[~m15.index.duplicated(keep="last")]
            m15.sort_index(inplace=True)
        else:
            m15 = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        frames["M15"] = m15
        logger.info(f"    M15: {len(m15)} velas descargadas ({m15_days} dias)")

        all_frames[symbol] = frames

    return all_frames


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza columnas, elimina NaN y asegura index UTC."""
    if df.empty:
        return df

    # yfinance puede devolver MultiIndex de columnas
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)

    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df.sort_index(inplace=True)
    return df
