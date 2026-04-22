"""
signal_engine.py
Replica determinista del arbol de 4 niveles del prompt.md WDC Hibrida v2.0.

Cambios v5 (2026-04-22):
- PER-PAIR CALIBRATION: evaluate_confluence() acepta `symbol` para aplicar
  parametros distintos segun el par:
    EURUSD: threshold=0.0010, sl=8p,  tp=16p, sentiment_bias=long  (60% long historico)
    GBPUSD: threshold=0.0012, sl=15p, tp=30p, sentiment_bias=long  (55% long historico)
    USDJPY: threshold=0.0015, sl=15p, tp=30p, sentiment_bias=short (55% short historico)
- simulate_sentiment() acepta `symbol` para usar la distribucion correcta por par.
- get_trend() threshold ya era configurable; ahora backtest_runner lo pasa por par.
- SL/TP se retornan en el reason string para que backtest_runner los use.
"""
import numpy as np
import pandas as pd
from typing import Tuple


# —————————————————————————————————————————————
# CONFIGURACION POR PAR
# —————————————————————————————————————————————

PAIR_CONFIG = {
    "EURUSD": {
        "trend_threshold": 0.0010,
        "sl_pips": 8,
        "tp_pips": 16,
        # Retail historicamente 60-65% long en EURUSD
        "sentiment_mean": 62.0,
        "sentiment_std": 12.0,
    },
    "GBPUSD": {
        "trend_threshold": 0.0012,
        "sl_pips": 15,
        "tp_pips": 30,
        # Retail historicamente 55-60% long en GBPUSD
        "sentiment_mean": 57.0,
        "sentiment_std": 13.0,
    },
    "USDJPY": {
        "trend_threshold": 0.0015,
        "sl_pips": 15,
        "tp_pips": 30,
        # Retail historicamente 55% short en USDJPY (buscan safe-haven)
        "sentiment_mean": 43.0,   # long_pct bajo = short dominante
        "sentiment_std": 13.0,
    },
}

# Fallback si llega un par no registrado
_DEFAULT_CONFIG = PAIR_CONFIG["EURUSD"]


def get_pair_config(symbol: str) -> dict:
    return PAIR_CONFIG.get(symbol, _DEFAULT_CONFIG)


# —————————————————————————————————————————————
# UTILIDADES DE TENDENCIA
# —————————————————————————————————————————————

def _sma(series: pd.Series, period: int) -> float:
    if len(series) < period:
        return float("nan")
    return series.iloc[-period:].mean()


def get_trend(df: pd.DataFrame, sma_period: int = 50, threshold: float = 0.0010) -> str:
    """
    Tendencia basada en SMA50.
    threshold configurable por par:
      EURUSD=0.0010, GBPUSD=0.0012, USDJPY=0.0015
    BUY    -> precio > threshold% por encima de SMA50
    SELL   -> precio > threshold% por debajo de SMA50
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
    if diff_pct > threshold:
        return "BUY"
    elif diff_pct < -threshold:
        return "SELL"
    return "NEUTRAL"


# —————————————————————————————————————————————
# DETECCION DE PATRONES (NIVEL 4)
# —————————————————————————————————————————————

def _is_pin_bar_bullish(o: float, h: float, l: float, c: float) -> bool:
    """
    Mecha inferior >= 55% del rango
    Cuerpo <= 35% del rango
    Mecha superior <= 25% del rango
    Cierre en 55% superior
    """
    total = h - l
    if total == 0:
        return False
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    return (
        lower_wick >= 0.55 * total
        and body <= 0.35 * total
        and upper_wick <= 0.25 * total
        and c > (l + 0.55 * total)
    )


def _is_pin_bar_bearish(o: float, h: float, l: float, c: float) -> bool:
    """
    Mecha superior >= 55% del rango
    Cuerpo <= 35% del rango
    Mecha inferior <= 25% del rango
    Cierre en 55% inferior
    """
    total = h - l
    if total == 0:
        return False
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return (
        upper_wick >= 0.55 * total
        and body <= 0.35 * total
        and lower_wick <= 0.25 * total
        and c < (h - 0.55 * total)
    )


def _is_engulfing_bullish(prev_o, prev_c, curr_o, curr_c) -> bool:
    """
    Vela alcista que engulla el cuerpo bajista previo.
    curr_o puede estar dentro del cuerpo anterior (relajado v4).
    """
    return (
        prev_c < prev_o
        and curr_c > curr_o
        and curr_o < prev_o
        and curr_c > prev_o
    )


def _is_engulfing_bearish(prev_o, prev_c, curr_o, curr_c) -> bool:
    """
    Vela bajista que engulla el cuerpo alcista previo.
    curr_o puede estar dentro del cuerpo anterior (relajado v4).
    """
    return (
        prev_c > prev_o
        and curr_c < curr_o
        and curr_o > prev_o
        and curr_c < prev_o
    )


def detect_pattern(m15: pd.DataFrame, bias: str) -> str | None:
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


# —————————————————————————————————————————————
# SIMULACION DE INPUTS EXTERNOS
# —————————————————————————————————————————————

def simulate_sentiment(rng: np.random.Generator, symbol: str = "EURUSD") -> dict:
    """
    Distribucion de sentimiento calibrada por par.
    EURUSD: media=62% long (retail historicamente long en EUR)
    GBPUSD: media=57% long (similar al EUR pero menos extremo)
    USDJPY: media=43% long (retail busca safe-haven = mas short)
    """
    cfg = get_pair_config(symbol)
    long_pct = float(np.clip(rng.normal(cfg["sentiment_mean"], cfg["sentiment_std"]), 30, 85))
    short_pct = 100.0 - long_pct
    return {"short_pct": short_pct, "long_pct": long_pct}


def simulate_av_score(rng: np.random.Generator) -> float:
    return float(np.clip(rng.normal(0.0, 0.15), -0.50, 0.50))


# —————————————————————————————————————————————
# MOTOR DE CONFLUENCIA PRINCIPAL v5
# —————————————————————————————————————————————

def evaluate_confluence(
    m15: pd.DataFrame,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    sentiment: dict,
    av_score: float,
    news_block: bool,
    trend_threshold: float = 0.0010,
    symbol: str = "EURUSD",
) -> Tuple[str, str, dict]:
    """
    Arbol WDC Hibrida v2.0 — 4 niveles estrictos.
    v5: calibracion por par via `symbol`.

    N1: Noticias HIGH -> HOLD
    N2: Tendencia Macro H4 + H1 -> define bias (BUY/SELL) o HOLD si conflicto
    N3: Sentimiento confirma tendencia:
        - bias=SELL: long_pct > 60% requerido (masa atrapada comprando)
        - bias=BUY:  short_pct > 60% requerido (masa atrapada vendiendo)
        - Neutro (40-60%): avanza solo si H4+H1 unanimes
    N4: Patron de vela confirmado (PinBar o Envolvente)

    Retorna (action, reason, levels).
    El reason incluye SL/TP del par para que backtest_runner los use.
    """
    cfg = get_pair_config(symbol)
    sl = cfg["sl_pips"]
    tp = cfg["tp_pips"]

    levels = {
        "nivel_1_noticias": "",
        "nivel_2_sentimiento": "",
        "nivel_3_tendencia": "",
        "nivel_4_patron": "",
    }

    # ── NIVEL 1: Noticias ──────────────────────────────────────────────────────
    if news_block:
        levels["nivel_1_noticias"] = "FALLO — evento HIGH impact"
        levels["nivel_2_sentimiento"] = "NO EVALUADO"
        levels["nivel_3_tendencia"] = "NO EVALUADO"
        levels["nivel_4_patron"] = "NO EVALUADO"
        return "HOLD", "N1 FALLO: noticia HIGH impact", levels

    levels["nivel_1_noticias"] = "OK — sin eventos HIGH"

    # ── NIVEL 2: Tendencia Macro H4 + H1 ──────────────────────────────────────
    h4_trend = get_trend(h4, threshold=trend_threshold)
    h1_trend = get_trend(h1, threshold=trend_threshold)

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

    bias = h4_trend if h4_trend != "NEUTRAL" else h1_trend
    levels["nivel_2_sentimiento"] = f"OK — H4={h4_trend} H1={h1_trend} → bias={bias}"

    # ── NIVEL 3: Sentimiento confirma la tendencia ─────────────────────────────
    short_pct = sentiment["short_pct"]
    long_pct = sentiment["long_pct"]

    if bias == "SELL":
        if long_pct >= 60:
            levels["nivel_3_tendencia"] = (
                f"OK — {long_pct:.0f}% retail long (masa atrapada comprando, confirma SELL)"
            )
        elif short_pct >= 60:
            levels["nivel_3_tendencia"] = (
                f"FALLO — {short_pct:.0f}% retail short (masa en tu misma direccion, riesgo squeeze)"
            )
            levels["nivel_4_patron"] = "NO EVALUADO"
            return "HOLD", f"N3 FALLO: masa vende {short_pct:.0f}% con tendencia SELL — riesgo squeeze", levels
        else:
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
        if short_pct >= 60:
            levels["nivel_3_tendencia"] = (
                f"OK — {short_pct:.0f}% retail short (masa atrapada vendiendo, confirma BUY)"
            )
        elif long_pct >= 60:
            levels["nivel_3_tendencia"] = (
                f"FALLO — {long_pct:.0f}% retail long (masa en tu misma direccion, riesgo)"
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

    # ── NIVEL 4: Patron de entrada ─────────────────────────────────────────────
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
        f"→ {bias} | SL={sl}p TP={tp}p"
    )
    return bias, reason, levels