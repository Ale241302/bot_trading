"""
signal_engine.py
Replica determinista del arbol de 4 niveles del prompt.md.
Todos los valores de sentimiento y Alpha Vantage se generan
con distribuciones estadisticas plausibles (no hay historico real).
"""
import numpy as np
import pandas as pd
from typing import Tuple


# ─────────────────────────────────────────────
# UTILIDADES DE TENDENCIA
# ─────────────────────────────────────────────

def _sma(series: pd.Series, period: int) -> float:
    """SMA de los ultimos `period` valores de una Serie."""
    if len(series) < period:
        return float("nan")
    return series.iloc[-period:].mean()


def get_trend(df: pd.DataFrame, sma_period: int = 50) -> str:
    """
    Tendencia basada en SMA50:
    precio > SMA50 -> 'BUY' (alcista)
    precio < SMA50 -> 'SELL' (bajista)
    precio ~= SMA50 -> 'NEUTRAL'
    """
    if len(df) < sma_period:
        return "NEUTRAL"
    close = df["Close"]
    sma_val = _sma(close, sma_period)
    if pd.isna(sma_val):
        return "NEUTRAL"
    last_close = close.iloc[-1]
    diff_pct = (last_close - sma_val) / sma_val
    if diff_pct > 0.0003:   # precio >0.03% sobre SMA50
        return "BUY"
    elif diff_pct < -0.0003:
        return "SELL"
    return "NEUTRAL"


# ─────────────────────────────────────────────
# DETECCION DE PATRONES (NIVEL 4)
# ─────────────────────────────────────────────

def _body_ratio(o: float, h: float, l: float, c: float) -> float:
    """Ratio cuerpo / rango total de la vela."""
    total = h - l
    if total == 0:
        return 0.0
    return abs(c - o) / total


def _is_pin_bar_bullish(o: float, h: float, l: float, c: float) -> bool:
    """Mecha larga inferior (>=60% del rango), cuerpo pequenio, cierre en tercio superior."""
    total = h - l
    if total == 0:
        return False
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    return (
        lower_wick >= 0.60 * total
        and body <= 0.30 * total
        and upper_wick <= 0.20 * total
        and c > (l + 0.60 * total)
    )


def _is_pin_bar_bearish(o: float, h: float, l: float, c: float) -> bool:
    """Mecha larga superior (>=60% del rango), cuerpo pequenio, cierre en tercio inferior."""
    total = h - l
    if total == 0:
        return False
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return (
        upper_wick >= 0.60 * total
        and body <= 0.30 * total
        and lower_wick <= 0.20 * total
        and c < (h - 0.60 * total)
    )


def _is_engulfing_bullish(prev_o, prev_c, curr_o, curr_c) -> bool:
    """Vela envolvente alcista: vela actual alcista envuelve la anterior bajista."""
    return (
        prev_c < prev_o          # vela anterior bajista
        and curr_c > curr_o      # vela actual alcista
        and curr_o <= prev_c     # abre por debajo del cierre anterior
        and curr_c >= prev_o     # cierra por encima de la apertura anterior
    )


def _is_engulfing_bearish(prev_o, prev_c, curr_o, curr_c) -> bool:
    """Vela envolvente bajista."""
    return (
        prev_c > prev_o
        and curr_c < curr_o
        and curr_o >= prev_c
        and curr_c <= prev_o
    )


def detect_pattern(m15: pd.DataFrame, bias: str) -> str | None:
    """
    Detecta patrones de entrada validos para el bias dado.
    Retorna nombre del patron o None.
    """
    if len(m15) < 3:
        return None

    last   = m15.iloc[-1]
    prev   = m15.iloc[-2]
    o, h, l, c = last["Open"], last["High"], last["Low"], last["Close"]
    po, pc    = prev["Open"], prev["Close"]

    sma50 = _sma(m15["Close"], 50)

    if bias == "BUY":
        near_sma_support = not pd.isna(sma50) and abs(c - sma50) / sma50 < 0.0015
        if _is_pin_bar_bullish(o, h, l, c):
            return "PinBar_Alcista"
        if _is_engulfing_bullish(po, pc, o, c):
            return "Envolvente_Alcista"
        if near_sma_support and c > po:
            return "Rebote_SMA50"

    elif bias == "SELL":
        near_sma_resistance = not pd.isna(sma50) and abs(c - sma50) / sma50 < 0.0015
        if _is_pin_bar_bearish(o, h, l, c):
            return "PinBar_Bajista"
        if _is_engulfing_bearish(po, pc, o, c):
            return "Envolvente_Bajista"
        if near_sma_resistance and c < po:
            return "Rechazo_SMA50"

    return None


# ─────────────────────────────────────────────
# SIMULACION DE SENTIMIENTO Y ALPHA VANTAGE
# (sin historico descargable)
# ─────────────────────────────────────────────

def simulate_sentiment(rng: np.random.Generator) -> dict:
    """
    Genera porcentajes Long/Short plausibles basados en
    distribuciones reales de Myfxbook EURUSD:
    - short_pct ~ Normal(55, 15) clipeada en [30, 90]
    - Rango extremo (>=75%) ocurre ~15% del tiempo
    """
    short_pct = float(np.clip(rng.normal(55, 15), 30, 90))
    long_pct  = 100.0 - short_pct
    return {"short_pct": short_pct, "long_pct": long_pct}


def simulate_av_score(rng: np.random.Generator) -> float:
    """
    Score Alpha Vantage EUR ~ Normal(0, 0.15) clipeado en [-0.50, +0.50].
    Divergencia fuerte (|score|>=0.15) ocurre ~32% del tiempo.
    """
    return float(np.clip(rng.normal(0.0, 0.15), -0.50, 0.50))


# ─────────────────────────────────────────────
# MOTOR DE CONFLUENCIA PRINCIPAL
# ─────────────────────────────────────────────

def evaluate_confluence(
    m15: pd.DataFrame,
    h1:  pd.DataFrame,
    h4:  pd.DataFrame,
    sentiment: dict,
    av_score: float,
    news_block: bool,
) -> Tuple[str, str, dict]:
    """
    Evalua los 4 niveles del prompt.md en orden estricto.
    Retorna (accion, razon_texto, confluence_levels_dict).
    accion puede ser: 'BUY', 'SELL', 'HOLD'
    """
    levels = {
        "nivel_1_noticias": "",
        "nivel_2_sentimiento": "",
        "nivel_3_tendencia": "",
        "nivel_4_patron": "",
    }

    # ── NIVEL 1: Noticias ──────────────────────────────────────────────
    if news_block:
        levels["nivel_1_noticias"] = "FALLO — evento HIGH impact"
        levels["nivel_2_sentimiento"] = "NO EVALUADO"
        levels["nivel_3_tendencia"]   = "NO EVALUADO"
        levels["nivel_4_patron"]      = "NO EVALUADO"
        return "HOLD", "N1 FALLO: noticia HIGH impact", levels

    levels["nivel_1_noticias"] = "OK — sin eventos HIGH"

    # ── NIVEL 2: Sentimiento Myfxbook ──────────────────────────────────
    short_pct = sentiment["short_pct"]
    long_pct  = sentiment["long_pct"]
    skip_n3   = False
    bias      = None

    if short_pct >= 75:
        bias   = "BUY"
        skip_n3 = True
        levels["nivel_2_sentimiento"] = f"EXTREMO — {short_pct:.0f}% short → sesgo BUY"
        levels["nivel_3_tendencia"]   = "OMITIDO — sentimiento extremo"
    elif long_pct >= 75:
        bias   = "SELL"
        skip_n3 = True
        levels["nivel_2_sentimiento"] = f"EXTREMO — {long_pct:.0f}% long → sesgo SELL"
        levels["nivel_3_tendencia"]   = "OMITIDO — sentimiento extremo"
    elif short_pct >= 65:
        bias   = "BUY"
        levels["nivel_2_sentimiento"] = f"FUERTE — {short_pct:.0f}% short → sesgo BUY (confirmar N3)"
    elif long_pct >= 65:
        bias   = "SELL"
        levels["nivel_2_sentimiento"] = f"FUERTE — {long_pct:.0f}% long → sesgo SELL (confirmar N3)"
    else:
        levels["nivel_2_sentimiento"] = f"NEUTRAL — {short_pct:.0f}% short / {long_pct:.0f}% long"

    # ── NIVEL 3: Tendencia multi-timeframe + Alpha Vantage ─────────────
    if not skip_n3:
        h1_trend  = get_trend(h1)
        m15_trend = get_trend(m15)

        if bias is None:
            # Sentimiento neutral: bias lo define la tendencia
            if h1_trend == "BUY" and m15_trend == "BUY":
                bias = "BUY"
            elif h1_trend == "SELL" and m15_trend == "SELL":
                bias = "SELL"
            else:
                levels["nivel_3_tendencia"] = f"FALLO — H1={h1_trend} M15={m15_trend} en conflicto"
                levels["nivel_4_patron"]    = "NO EVALUADO"
                return "HOLD", f"N3 FALLO: H1={h1_trend} M15={m15_trend} en conflicto", levels
        else:
            # Sentimiento fuerte: verificar que tendencia no contradiga
            if h1_trend != bias and h1_trend != "NEUTRAL":
                levels["nivel_3_tendencia"] = f"FALLO — H1={h1_trend} contradice sesgo {bias}"
                levels["nivel_4_patron"]    = "NO EVALUADO"
                return "HOLD", f"N3 FALLO: H1={h1_trend} contradice sesgo sentimiento {bias}", levels

        # Alpha Vantage divergencia
        if bias == "BUY" and av_score <= -0.15:
            levels["nivel_3_tendencia"] = f"FALLO — divergencia AV bearish ({av_score:.2f}) vs sesgo BUY"
            levels["nivel_4_patron"]    = "NO EVALUADO"
            return "HOLD", f"N3 FALLO: AV Score {av_score:.2f} bearish vs BUY", levels
        if bias == "SELL" and av_score >= 0.15:
            levels["nivel_3_tendencia"] = f"FALLO — divergencia AV bullish ({av_score:.2f}) vs sesgo SELL"
            levels["nivel_4_patron"]    = "NO EVALUADO"
            return "HOLD", f"N3 FALLO: AV Score {av_score:.2f} bullish vs SELL", levels

        av_label = "Bullish" if av_score > 0.05 else ("Bearish" if av_score < -0.05 else "Neutral")
        levels["nivel_3_tendencia"] = (
            f"OK — H1={h1_trend} M15={m15_trend} alineados + AV {av_label} ({av_score:.2f})"
        )

    # ── NIVEL 4: Patron de entrada ─────────────────────────────────────
    if bias is None:
        levels["nivel_4_patron"] = "NO EVALUADO — sin bias definido"
        return "HOLD", "N4 sin bias definido", levels

    pattern = detect_pattern(m15, bias)
    if not pattern:
        levels["nivel_4_patron"] = f"FALLO — sin patron valido para {bias}"
        return "HOLD", f"N4 FALLO: sin patron de entrada {bias}", levels

    levels["nivel_4_patron"] = f"OK — {pattern}"
    reason = (
        f"N1 OK. {levels['nivel_2_sentimiento']}. "
        f"{levels['nivel_3_tendencia']}. N4 OK: {pattern}. "
        f"→ {bias} | SL=8p TP=16p"
    )
    return bias, reason, levels
