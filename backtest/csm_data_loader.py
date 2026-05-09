"""
csm_data_loader.py
Descarga datos OHLCV para los 30 instrumentos del universo CSM.

Reutiliza yfinance pero importa CSM_PAIR_SPECS (no PAIR_SPECS) para no
mezclar configuraciones con el backtest WDC.

Limites yfinance idénticos a data_loader.py:
  - 15m : max 60 dias por request  → chunks de 55 dias
  - 1h  : max 730 dias              → una sola llamada
  - 4h  : max 730 dias              → una sola llamada

Tolera fallos parciales: si un par no tiene datos, lo loguea y sigue.
"""
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from .csm_pair_specs import CSM_PAIR_SPECS, CSM_DEFAULT_PAIRS

logger = logging.getLogger(__name__)

_MAX_DAYS = {"1h": 729, "4h": 729, "15m": 59}
_CHUNK_DAYS_M15 = 55


def download_csm_data(
    years: int = 2,
    symbols: list[str] | None = None,
    timeframes: tuple[str, ...] = ("M15", "H1", "H4"),
) -> dict[str, dict[str, pd.DataFrame]]:
    """
    Descarga datos para cada par del universo CSM.

    Retorna { "EURUSD": {"M15": df, "H1": df, "H4": df}, ... }

    `timeframes` permite ahorrar llamadas si solo se necesita un subset
    (ej. el CSM scoring solo necesita H4).
    """
    if symbols is None:
        symbols = CSM_DEFAULT_PAIRS

    end = datetime.now(timezone.utc).replace(tzinfo=None)
    requested_days = min(years * 365, 729)

    all_frames: dict[str, dict[str, pd.DataFrame]] = {}

    for symbol in symbols:
        spec = CSM_PAIR_SPECS.get(symbol)
        if spec is None:
            logger.warning(f"Símbolo {symbol} no está en CSM_PAIR_SPECS, saltando.")
            continue

        yf_ticker = spec["yf_symbol"]
        logger.info(f"── {symbol} ({yf_ticker}) ──")
        frames: dict[str, pd.DataFrame] = {}

        # H1 / H4 — una sola llamada
        for tf, interval in (("H1", "1h"), ("H4", "4h")):
            if tf not in timeframes:
                continue
            days = min(requested_days, _MAX_DAYS[interval])
            start = end - timedelta(days=days)
            try:
                df = yf.download(
                    yf_ticker,
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval=interval,
                    auto_adjust=True,
                    progress=False,
                )
            except Exception as e:
                logger.warning(f"  {symbol} {tf}: error de descarga ({e}); df vacío.")
                df = pd.DataFrame()
            df = _clean(df) if not df.empty else _empty_ohlcv()
            frames[tf] = df
            logger.info(f"  {tf}: {len(df)} velas")

        # M15 — chunks
        if "M15" in timeframes:
            chunks = []
            chunk_end = end
            days_covered = 0
            m15_days = min(requested_days, _MAX_DAYS["15m"])
            while days_covered < m15_days:
                this_chunk = min(_CHUNK_DAYS_M15, m15_days - days_covered)
                chunk_start = chunk_end - timedelta(days=this_chunk)
                try:
                    df_c = yf.download(
                        yf_ticker,
                        start=chunk_start.strftime("%Y-%m-%d"),
                        end=chunk_end.strftime("%Y-%m-%d"),
                        interval="15m",
                        auto_adjust=True,
                        progress=False,
                    )
                except Exception as e:
                    logger.warning(f"  {symbol} M15 chunk {chunk_start.date()}: {e}")
                    df_c = pd.DataFrame()
                if not df_c.empty:
                    chunks.append(_clean(df_c))
                days_covered += this_chunk
                chunk_end = chunk_start

            if chunks:
                m15 = pd.concat(chunks)
                m15 = m15[~m15.index.duplicated(keep="last")]
                m15.sort_index(inplace=True)
            else:
                m15 = _empty_ohlcv()
            frames["M15"] = m15
            logger.info(f"  M15: {len(m15)} velas")

        all_frames[symbol] = frames

    return all_frames


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza columnas y asegura index UTC."""
    if df.empty:
        return df
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


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
