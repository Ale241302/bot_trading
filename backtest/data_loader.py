"""
data_loader.py
Descarga datos historicos OHLCV de EURUSD en M15, H1 y H4
usando yfinance. Devuelve DataFrames sincronizados por timestamp UTC.
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

SYMBOL = "EURUSD=X"

INTERVAL_MAP = {
    "M15": "15m",
    "H1":  "1h",
    "H4":  "4h",
}

def download_data(years: int = 2) -> dict[str, pd.DataFrame]:
    """
    Descarga EURUSD para M15, H1 y H4 por los ultimos `years` anios.
    yfinance limita datos intraday:
      - 15m: maximo 60 dias por llamada -> se descarga en chunks de 55 dias
      - 1h:  maximo 730 dias (2 anios)
      - 4h:  maximo 730 dias (2 anios)
    Retorna dict con keys 'M15', 'H1', 'H4'.
    """
    end   = datetime.utcnow()
    start = end - timedelta(days=365 * years)

    frames = {}

    # ---- H1 y H4 (sin limite de chunk) ----
    for tf, interval in [("H1", "1h"), ("H4", "4h")]:
        logger.info(f"Descargando {tf} ({interval}) desde {start.date()} hasta {end.date()}")
        df = yf.download(
            SYMBOL,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
        df = _clean(df)
        frames[tf] = df
        logger.info(f"  {tf}: {len(df)} velas descargadas")

    # ---- M15 (maximo 60 dias por request -> chunks) ----
    logger.info("Descargando M15 en chunks de 55 dias...")
    chunk_days = 55
    chunks = []
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(chunk_end - timedelta(days=chunk_days), start)
        df_chunk = yf.download(
            SYMBOL,
            start=chunk_start.strftime("%Y-%m-%d"),
            end=chunk_end.strftime("%Y-%m-%d"),
            interval="15m",
            auto_adjust=True,
            progress=False,
        )
        if not df_chunk.empty:
            chunks.append(_clean(df_chunk))
        chunk_end = chunk_start - timedelta(minutes=15)

    if chunks:
        m15 = pd.concat(chunks).sort_index()
        m15 = m15[~m15.index.duplicated(keep="first")]
    else:
        m15 = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    frames["M15"] = m15
    logger.info(f"  M15: {len(m15)} velas descargadas")

    return frames


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
