"""
data_loader.py
Descarga datos historicos OHLCV de EURUSD en M15, H1 y H4
usando yfinance. Devuelve DataFrames sincronizados por timestamp UTC.

Limites reales de yfinance por intervalo:
  - 15m : max 60 dias por request  -> se descarga en chunks de 55 dias
  - 1h  : max 730 dias (2 anios)   -> una sola llamada
  - 4h  : max 730 dias (2 anios)   -> una sola llamada
"""
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

SYMBOL = "EURUSD=X"

# Limites duros de yfinance para datos intraday
_MAX_DAYS = {
    "1h":  729,   # justo bajo 730 para evitar errores de borde
    "4h":  729,
    "15m":  59,   # max 60 dias, usamos 59 para seguridad
}
_CHUNK_DAYS = {
    "15m": 55,    # chunks solapados para M15
}


def download_data(years: int = 2) -> dict[str, pd.DataFrame]:
    """
    Descarga EURUSD para M15, H1 y H4 por los ultimos `years` anios.

    - H1 y H4: una sola llamada, hasta 729 dias (respeta el limite de yfinance).
    - M15: descarga en chunks de 55 dias y concatena (limite de 60 dias/request).

    Retorna dict con keys 'M15', 'H1', 'H4'.
    """
    end = datetime.utcnow()
    requested_days = min(years * 365, 729)  # nunca pedir mas de 729 dias

    frames = {}

    # ── H1 y H4: una sola llamada con el maximo disponible ──────────────────
    for tf, interval in [("H1", "1h"), ("H4", "4h")]:
        days = min(requested_days, _MAX_DAYS[interval])
        start = end - timedelta(days=days)
        logger.info(
            f"Descargando {tf} ({interval}) desde {start.date()} hasta {end.date()}"
        )
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
        else:
            df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        frames[tf] = df
        logger.info(f"  {tf}: {len(df)} velas descargadas")

    # ── M15: chunks de 55 dias hasta cubrir `requested_days` ────────────────
    logger.info("Descargando M15 (chunks de 55 dias)...")
    chunk_size = _CHUNK_DAYS["15m"]
    m15_days = min(requested_days, _MAX_DAYS["15m"])  # yfinance hard-limit
    chunks = []
    chunk_end = end
    days_covered = 0

    while days_covered < m15_days:
        remaining = m15_days - days_covered
        this_chunk = min(chunk_size, remaining)
        chunk_start = chunk_end - timedelta(days=this_chunk)

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

        days_covered += this_chunk
        chunk_end = chunk_start  # mover ventana hacia atras

    if chunks:
        m15 = pd.concat(chunks)
        m15 = m15[~m15.index.duplicated(keep="last")]
        m15.sort_index(inplace=True)
    else:
        m15 = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    frames["M15"] = m15
    logger.info(f"  M15: {len(m15)} velas descargadas ({m15_days} dias)")

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
