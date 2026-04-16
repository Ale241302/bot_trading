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
    # Yahoo intraday data (M15, H1, H4) is strictly limited to the last 60-730 days.
    # To ensure synchronization and availability, we limit all to the last 59 days.
    end   = datetime.utcnow()
    start = end - timedelta(days=59)

    frames = {}

    # ---- H1 y H4 ----
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
        if not df.empty:
            df = _clean(df)
        frames[tf] = df
        logger.info(f"  {tf}: {len(df)} velas descargadas")

    # ---- M15 ----
    logger.info("Descargando M15...")
    m15 = yf.download(
        SYMBOL,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="15m",
        auto_adjust=True,
        progress=False,
    )
    if not m15.empty:
        m15 = _clean(m15)
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
