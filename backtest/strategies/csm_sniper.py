"""
csm_sniper.py
Lógica determinista de la estrategia "Francotirador de Fuerza Relativa
con Compounding Agresivo" para cuentas micro.

Reglas:
  1. LUNES (00:00 UTC): calcular Currency Strength sobre las últimas 5
     sesiones H4 (≈ 5 días, equivalente a una semana de mercado FX).
     Seleccionar la divisa más fuerte y la más débil. Operar el cruce
     que las conecta directamente, en la dirección de la fuerte.
  2. MAR-JUE: buscar entrada en pullback. Solo se permite UNA posición
     viva a la vez en la semana. Si toca SL, no se reentra.
  3. VIE 21:00 UTC: cierre forzado.
  4. SL = ATR(14) M15 × 1.5 (en pips). TP = SL × 3 (RR 1:3).
  5. Lote = capital × risk_pct / (sl_pips × pip_value_per_lot).
     Mínimo 0.01, redondeo a 0.01.

Sin IA. 100% determinista. Para que `seed=42 ⇒ resultado idéntico`,
no se usa RNG en este módulo.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..csm_pair_specs import CSM_PAIR_SPECS, CURRENCIES, find_pair


# ─────────────────────────── parámetros ────────────────────────────────
ATR_PERIOD            = 14
ATR_MULT_SL           = 1.5
RR_RATIO              = 3.0          # TP = SL × 3
RSI_PERIOD            = 14
RSI_LONG_PULLBACK     = 40           # buy si RSI < 40 + tendencia alcista
RSI_SHORT_PULLBACK    = 60           # sell si RSI > 60 + tendencia bajista
H1_TREND_FAST         = 20           # SMA20
H1_TREND_SLOW         = 50           # SMA50
STRENGTH_LOOKBACK_H4  = 30           # últimas 30 velas H4 ≈ 5 sesiones
LOT_MIN               = 0.01
LOT_MAX               = 5.00         # cap razonable para cuenta micro
LOT_STEP              = 0.01

# Cierre forzado: viernes 21:00 UTC (mercado FX cierra ~22:00 UTC viernes)
FRIDAY_CLOSE_HOUR_UTC = 21


# ─────────────────────────── tipos ─────────────────────────────────────
@dataclass
class WeekPick:
    """Resultado de la selección del lunes."""
    week_start: pd.Timestamp        # lunes 00:00 UTC
    strongest: str                  # divisa más fuerte (ej. "GBP")
    weakest: str                    # divisa más débil (ej. "JPY")
    pair_symbol: str                # cruce a operar (ej. "GBPJPY")
    direction: int                  # +1 LONG sobre pair_symbol, -1 SHORT
    strength_table: dict[str, float]  # debug: ranking completo


@dataclass
class EntrySignal:
    """Señal de entrada en pullback intra-semana."""
    timestamp: pd.Timestamp
    side: str                       # "BUY" | "SELL"
    entry_price: float
    sl_price: float
    tp_price: float
    sl_pips: float
    tp_pips: float
    lot: float


# ─────────────────────────── currency strength ─────────────────────────
def compute_currency_strength(
    h4_data: dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    lookback_bars: int = STRENGTH_LOOKBACK_H4,
) -> dict[str, float]:
    """
    Calcula la fuerza de cada divisa al cierre de `asof` (o la vela H4
    inmediatamente anterior).

    Para cada divisa C, score[C] = mean( signed_return(p) for p in pairs
    que contengan C ), donde el signo es:
      +Δ% si C es base del cruce (sube C ⇒ sube precio)
      -Δ% si C es quote (sube C ⇒ baja precio)

    Δ% = (close_now / close_lookback) - 1
    """
    scores: dict[str, list[float]] = {c: [] for c in CURRENCIES}

    for symbol, spec in CSM_PAIR_SPECS.items():
        df = h4_data.get(symbol)
        if df is None or df.empty:
            continue

        # Última vela H4 con cierre <= asof (no leaking)
        idx = df.index.searchsorted(asof, side="right") - 1
        if idx < lookback_bars:
            continue

        close_now = float(df.iloc[idx]["Close"])
        close_then = float(df.iloc[idx - lookback_bars]["Close"])
        if close_then <= 0:
            continue

        ret = (close_now / close_then) - 1.0
        scores[spec["base"]].append(+ret)
        scores[spec["quote"]].append(-ret)

    return {
        c: (float(np.mean(v)) if v else 0.0)
        for c, v in scores.items()
    }


def pick_pair_of_week(
    h4_data: dict[str, pd.DataFrame],
    monday_ts: pd.Timestamp,
    excluded_currencies: set[str] | None = None,
) -> WeekPick | None:
    """
    Selecciona el cruce de la semana basándose en strength. Retorna None
    si no encuentra un cruce directo entre la fuerte y la débil.

    `excluded_currencies` permite excluir divisas (p.ej. evitar XAU/XAG
    si el broker no lo opera).
    """
    excluded = excluded_currencies or set()
    table = compute_currency_strength(h4_data, asof=monday_ts)

    # Filtrar divisas sin score (no había datos suficientes)
    candidates = {c: s for c, s in table.items() if c not in excluded}
    if len(candidates) < 2:
        return None

    strongest = max(candidates, key=candidates.get)
    weakest = min(candidates, key=candidates.get)

    # Si la fuerza no diverge realmente, no operamos
    if candidates[strongest] - candidates[weakest] < 0.001:  # < 0.1% spread
        return None

    pair_info = find_pair(strongest, weakest)
    if pair_info is None:
        # Sin cruce directo: probamos con el segundo más fuerte/débil
        sorted_curr = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        for strong_alt, _ in sorted_curr[:3]:
            for weak_alt, _ in sorted(candidates.items(), key=lambda x: x[1])[:3]:
                if strong_alt == weak_alt:
                    continue
                pair_info = find_pair(strong_alt, weak_alt)
                if pair_info:
                    strongest, weakest = strong_alt, weak_alt
                    break
            if pair_info:
                break
        if pair_info is None:
            return None

    pair_symbol, swap = pair_info
    # Si swap == +1 → LONG sobre el par. Si swap == -1 → SHORT (porque el
    # broker cotiza inverso: precio sube ⇒ la weakest se aprecia).
    direction = +1 if swap == +1 else -1

    return WeekPick(
        week_start=monday_ts,
        strongest=strongest,
        weakest=weakest,
        pair_symbol=pair_symbol,
        direction=direction,
        strength_table=table,
    )


# ─────────────────────────── indicadores ───────────────────────────────
def atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """ATR clásico (SMA sobre TrueRange)."""
    high = df["High"]
    low = df["Low"]
    close_prev = df["Close"].shift(1)
    tr = pd.concat(
        [(high - low),
         (high - close_prev).abs(),
         (low - close_prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def rsi(closes: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI Wilder."""
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def h1_trend(h1_df: pd.DataFrame, asof: pd.Timestamp) -> int:
    """+1 alcista, -1 bajista, 0 neutral. Vela en cierre <= asof."""
    if len(h1_df) < H1_TREND_SLOW + 1:
        return 0
    idx = h1_df.index.searchsorted(asof, side="right") - 1
    if idx < H1_TREND_SLOW:
        return 0
    sub = h1_df.iloc[: idx + 1]
    sma_fast = sub["Close"].iloc[-H1_TREND_FAST:].mean()
    sma_slow = sub["Close"].iloc[-H1_TREND_SLOW:].mean()
    if sma_fast > sma_slow * 1.0005:
        return +1
    if sma_fast < sma_slow * 0.9995:
        return -1
    return 0


# ─────────────────────────── entrada en pullback ───────────────────────
def find_pullback_entry(
    m15_df: pd.DataFrame,
    h1_df: pd.DataFrame,
    direction: int,
    pip_size: float,
    pip_value_per_lot: float,
    capital: float,
    risk_pct: float,
    asof: pd.Timestamp,
) -> EntrySignal | None:
    """
    Busca entrada en la última vela M15 cerrada antes de `asof`.

    Reglas:
      - dirección LONG (+1): RSI(14) M15 < RSI_LONG_PULLBACK + cierre verde
        (close > prev close) + tendencia H1 alcista.
      - dirección SHORT (-1): simétrica.

    SL = ATR(14) M15 × 1.5 (en pips, redondeo a entero).
    TP = SL × 3.
    Lot = capital × risk_pct / (sl_pips × pip_value_per_lot), clamped a [0.01, 5.0].

    Retorna None si no hay señal o si los indicadores no están listos.
    """
    if m15_df.empty or len(m15_df) < ATR_PERIOD + 2:
        return None

    idx = m15_df.index.searchsorted(asof, side="right") - 1
    if idx < ATR_PERIOD + 1:
        return None

    sub = m15_df.iloc[: idx + 1]
    last = sub.iloc[-1]
    prev = sub.iloc[-2]

    # Filtro de tendencia H1
    trend = h1_trend(h1_df, last.name)
    if direction == +1 and trend != +1:
        return None
    if direction == -1 and trend != -1:
        return None

    # RSI pullback
    rsi_series = rsi(sub["Close"])
    rsi_now = rsi_series.iloc[-1]
    if pd.isna(rsi_now):
        return None

    if direction == +1:
        if rsi_now >= RSI_LONG_PULLBACK:
            return None
        if last["Close"] <= prev["Close"]:  # quiero confirmación alcista
            return None
        side = "BUY"
    else:
        if rsi_now <= RSI_SHORT_PULLBACK:
            return None
        if last["Close"] >= prev["Close"]:
            return None
        side = "SELL"

    # ATR → SL en pips
    atr_series = atr(sub)
    atr_now = atr_series.iloc[-1]
    if pd.isna(atr_now) or atr_now <= 0:
        return None

    sl_pips = max(5.0, round((atr_now * ATR_MULT_SL) / pip_size))
    tp_pips = sl_pips * RR_RATIO

    entry_price = float(last["Close"])
    if side == "BUY":
        sl_price = entry_price - sl_pips * pip_size
        tp_price = entry_price + tp_pips * pip_size
    else:
        sl_price = entry_price + sl_pips * pip_size
        tp_price = entry_price - tp_pips * pip_size

    # Lote por riesgo
    risk_usd = capital * risk_pct
    raw_lot = risk_usd / (sl_pips * pip_value_per_lot)
    lot = max(LOT_MIN, min(LOT_MAX, math.floor(raw_lot / LOT_STEP) * LOT_STEP))
    if lot < LOT_MIN:
        return None

    return EntrySignal(
        timestamp=last.name,
        side=side,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        lot=round(lot, 2),
    )


def is_friday_close_time(ts: pd.Timestamp) -> bool:
    """True si la vela cae en viernes ≥ 21:00 UTC (cierre forzado)."""
    return ts.weekday() == 4 and ts.hour >= FRIDAY_CLOSE_HOUR_UTC


def next_monday_open(ts: pd.Timestamp) -> pd.Timestamp:
    """Siguiente lunes 00:00 UTC desde `ts` (incluye el propio si es lunes 00:00)."""
    ts_utc = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
    days_ahead = (7 - ts_utc.weekday()) % 7
    if days_ahead == 0 and ts_utc.hour == 0 and ts_utc.minute == 0:
        return ts_utc.normalize()
    if days_ahead == 0:
        days_ahead = 7
    monday = (ts_utc + pd.Timedelta(days=days_ahead)).normalize()
    return monday
