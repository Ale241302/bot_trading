"""
signal_engine.py
Replica determinista del arbol de 4 niveles del prompt.md WDC Híbrida v2.0.

Cambios v3 (2026-04-15):
- N2 ahora es La Tendencia Macro: se evalua H4 + H1. Si H4 contradice
  el bias del sentimiento -> HOLD. Elimina las entradas BUY en caida libre.
- N3 (sentimiento) ahora CONFIRMA la tendencia en lugar de dictarla:
    * Tendencia SELL -> se exige long_pct > 60% (masa atrapada comprando)
    * Tendencia BUY  -> se exige short_pct > 60% (masa atrapada vendiendo)
  Con sentimiento neutro (40-60%) se avanza solo si la tendencia es clara.
- N4: solo PinBar y Envolvente. Rebote/Rechazo_SMA50 siguen eliminados.
"""
import numpy as np
import pandas as pd
from typing import Tuple


# ─────────────────────────────────────────────
# UTILIDADES DE TENDENCIA
# ─────────────────────────────────────────────

def _sma(series: pd.Series, period: int) -> float:
    if len(series) < period:
        return float("nan")
    return series.iloc[-period:].mean()


def get_trend(df: pd.DataFrame, sma_period: int = 50) -> str:
    """
    Tendencia basada en SMA50.
    BUY  -> precio >0.03% por encima de SMA50
    SELL -> precio >0.03% por debajo de SMA50
    NEUTRAL -> dentro del margen
    """
    if len(df) < sma_period:
        return "NEUTRAL"
    close = df["Close"]
    sma_val = _sma(close, sma_period)
    if pd.isna(sma_val):
        return "NEUTRAL"
    last_close = close.iloc[-1]
    diff_pct = (last_close - sma_val) / sma_val
    if diff_pct > 0.0003:
        return "BUY"
    elif diff_pct < -0.0003:
        return "SELL"
    return "NEUTRAL"


# ─────────────────────────────────────────────
# DETECCION DE PATRONES (NIVEL 4)
# ─────────────────────────────────────────────

def _is_pin_bar_bullish(o: float, h: float, l: float, c: float) -> bool:
    """Mecha inferior >=60% del rango, cuerpo <=30%, cierre en tercio superior."""
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
    """Mecha superior >=60% del rango, cuerpo <=30%, cierre en tercio inferior."""
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
    return (
        prev_c < prev_o
        and curr_c > curr_o
        and curr_o <= prev_c
        and curr_c >= prev_o
    )


def _is_engulfing_bearish(prev_o, prev_c, curr_o, curr_c) -> bool:
    return (
        prev_c > prev_o
        and curr_c < curr_o
        and curr_o >= prev_c
        and curr_c <= prev_o
    )


def detect_pattern(m15: pd.DataFrame, bias: str) -> str | None:
    """
    Solo acepta PinBar y Envolvente confirmados.
    Rebote/Rechazo_SMA50 eliminados (ruidosos con SL=8p).
    """
    if len(m15) < 3:
        return None
    last = m15.iloc[-1]
    prev = m15.iloc[-2]
    o, h, l, c = last["Open"], last["High"], last["Low"], last["Close"]
    po, pc = prev["Open"], prev["Close"]

    if bias == "BUY":
        if _is_pin_bar_bullish(o, h, l, c):
            return "PinBar_Alcista"
        if _is_engulfing_bullish(po, pc, o, c):
            return "Envolvente_Alcista"
    elif bias == "SELL":
        if _is_pin_bar_bearish(o, h, l, c):
            return "PinBar_Bajista"
        if _is_engulfing_bearish(po, pc, o, c):
            return "Envolvente_Bajista"
    return None


# ─────────────────────────────────────────────
# SIMULACION DE INPUTS EXTERNOS
# ─────────────────────────────────────────────

def simulate_sentiment(rng: np.random.Generator) -> dict:
    short_pct = float(np.clip(rng.normal(55, 15), 30, 90))
    long_pct = 100.0 - short_pct
    return {"short_pct": short_pct, "long_pct": long_pct}


def simulate_av_score(rng: np.random.Generator) -> float:
    return float(np.clip(rng.normal(0.0, 0.15), -0.50, 0.50))


# ─────────────────────────────────────────────
# MOTOR DE CONFLUENCIA PRINCIPAL v3
# ─────────────────────────────────────────────

def evaluate_confluence(
    m15: pd.DataFrame,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    sentiment: dict,
    av_score: float,
    news_block: bool,
) -> Tuple[str, str, dict]:
    """
    Árbol WDC Híbrida v2.0 — 4 niveles estrictos.

    N1: Noticias HIGH -> HOLD
    N2: Tendencia Macro H4 + H1 -> define bias (BUY/SELL) o HOLD si conflicto
    N3: Sentimiento confirma tendencia:
        - bias=SELL: long_pct > 60% requerido (masa atrapada comprando)
        - bias=BUY:  short_pct > 60% requerido (masa atrapada vendiendo)
        - Neutro (40-60%): avanza solo si la tendencia es clara y unánime
    N4: Patron de vela confirmado (PinBar o Envolvente)
    """
    levels = {
        "nivel_1_noticias": "",
        "nivel_2_sentimiento": "",
        "nivel_3_tendencia": "",
        "nivel_4_patron": "",
    }

    # ── NIVEL 1: Noticias ─────────────────────────────────────────────────────
    if news_block:
        levels["nivel_1_noticias"] = "FALLO — evento HIGH impact"
        levels["nivel_2_sentimiento"] = "NO EVALUADO"
        levels["nivel_3_tendencia"] = "NO EVALUADO"
        levels["nivel_4_patron"] = "NO EVALUADO"
        return "HOLD", "N1 FALLO: noticia HIGH impact", levels

    levels["nivel_1_noticias"] = "OK — sin eventos HIGH"

    # ── NIVEL 2: Tendencia Macro H4 + H1 ─────────────────────────────────────
    # El H4 es el filtro macro principal. Si H4 dice SELL, no hay BUY.
    h4_trend = get_trend(h4)
    h1_trend = get_trend(h1)

    if h4_trend == "NEUTRAL" and h1_trend == "NEUTRAL":
        levels["nivel_2_sentimiento"] = "FALLO — H4=NEUTRAL H1=NEUTRAL, sin tendencia clara"
        levels["nivel_3_tendencia"] = "NO EVALUADO"
        levels["nivel_4_patron"] = "NO EVALUADO"
        return "HOLD", "N2 FALLO: H4 y H1 neutrales, sin direccion", levels

    if h4_trend != "NEUTRAL" and h1_trend != "NEUTRAL" and h4_trend != h1_trend:
        levels["nivel_2_sentimiento"] = f"FALLO — H4={h4_trend} vs H1={h1_trend} en conflicto"
        levels["nivel_3_tendencia"] = "NO EVALUADO"
        levels["nivel_4_patron"] = "NO EVALUADO"
        return "HOLD", f"N2 FALLO: H4={h4_trend} contradice H1={h1_trend}", levels

    # Bias lo dicta H4 si no es NEUTRAL, si no H1
    bias = h4_trend if h4_trend != "NEUTRAL" else h1_trend
    levels["nivel_2_sentimiento"] = f"OK — H4={h4_trend} H1={h1_trend} → bias={bias}"

    # ── NIVEL 3: Sentimiento confirma la tendencia ────────────────────────────
    short_pct = sentiment["short_pct"]
    long_pct = sentiment["long_pct"]

    if bias == "SELL":
        # En tendencia bajista: necesitamos que la masa esté atrapada LONG
        if long_pct >= 60:
            levels["nivel_3_tendencia"] = (
                f"OK — {long_pct:.0f}% retail long (masa atrapada comprando, confirma SELL)"
            )
        elif short_pct >= 60:
            # La masa también vende -> riesgo de squeeze, no entrar
            levels["nivel_3_tendencia"] = (
                f"FALLO — {short_pct:.0f}% retail short (masa en tu misma dirección, riesgo alto)"
            )
            levels["nivel_4_patron"] = "NO EVALUADO"
            return "HOLD", f"N3 FALLO: masa vende {short_pct:.0f}% con tendencia SELL — riesgo squeeze", levels
        else:
            # Sentimiento neutro (40-60%): solo avanzar si H4 y H1 son unanimes
            if h4_trend == "SELL" and h1_trend == "SELL":
                levels["nivel_3_tendencia"] = (
                    f"OK (neutro) — sentimiento {long_pct:.0f}%L/{short_pct:.0f}%S, "
                    f"H4+H1 unanimes SELL"
                )
            else:
                levels["nivel_3_tendencia"] = (
                    f"FALLO — sentimiento neutro ({long_pct:.0f}%L/{short_pct:.0f}%S) "
                    f"y tendencia no unanime"
                )
                levels["nivel_4_patron"] = "NO EVALUADO"
                return "HOLD", "N3 FALLO: sentimiento neutro sin tendencia unanime", levels

    elif bias == "BUY":
        # En tendencia alcista: necesitamos que la masa esté atrapada SHORT
        if short_pct >= 60:
            levels["nivel_3_tendencia"] = (
                f"OK — {short_pct:.0f}% retail short (masa atrapada vendiendo, confirma BUY)"
            )
        elif long_pct >= 60:
            levels["nivel_3_tendencia"] = (
                f"FALLO — {long_pct:.0f}% retail long (masa en tu misma dirección, riesgo alto)"
            )
            levels["nivel_4_patron"] = "NO EVALUADO"
            return "HOLD", f"N3 FALLO: masa compra {long_pct:.0f}% con tendencia BUY — riesgo", levels
        else:
            if h4_trend == "BUY" and h1_trend == "BUY":
                levels["nivel_3_tendencia"] = (
                    f"OK (neutro) — sentimiento {long_pct:.0f}%L/{short_pct:.0f}%S, "
                    f"H4+H1 unanimes BUY"
                )
            else:
                levels["nivel_3_tendencia"] = (
                    f"FALLO — sentimiento neutro ({long_pct:.0f}%L/{short_pct:.0f}%S) "
                    f"y tendencia no unanime"
                )
                levels["nivel_4_patron"] = "NO EVALUADO"
                return "HOLD", "N3 FALLO: sentimiento neutro sin tendencia unanime", levels

    # ── NIVEL 4: Patron de entrada ────────────────────────────────────────────
    pattern = detect_pattern(m15, bias)
    if not pattern:
        levels["nivel_4_patron"] = f"FALLO — sin patron valido para {bias} (solo PinBar/Envolvente)"
        return "HOLD", f"N4 FALLO: sin patron de entrada {bias}", levels

    levels["nivel_4_patron"] = f"OK — {pattern}"
    reason = (
        f"{levels['nivel_1_noticias']}. "
        f"N2 {levels['nivel_2_sentimiento']}. "
        f"N3 {levels['nivel_3_tendencia']}. "
        f"N4 OK: {pattern}. "
        f"→ {bias} | SL=8p TP=16p"
    )
    return bias, reason, levels
