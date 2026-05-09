"""
market_context.py
================================================
Contexto externo de mercado — Estrategia ASM

Fuentes (todas FREE):
  1. yfinance EURUSD=X       → velas M1 + RSI/MACD calculados localmente (sin API key)
  2. Myfxbook (vía MyfxbookClient) → % long/short EURUSD
================================================
"""

from datetime import datetime, timezone

from modules.myfxbook_client import MyfxbookClient

try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False


class MarketContext:
    YF_SYMBOL = "EURUSD=X"

    def __init__(self, myfxbook_client: MyfxbookClient | None = None):
        self.myfxbook = myfxbook_client or MyfxbookClient()
        self._cache: dict = {}
        self._cache_ttl   = 55

    # ── Cache ────────────────────────────────────────────────────

    def _cache_get(self, key):
        e = self._cache.get(key)
        if not e:
            return None
        return e["data"] if (datetime.now(timezone.utc) - e["ts"]).total_seconds() < self._cache_ttl else None

    def _cache_set(self, key, data):
        self._cache[key] = {"data": data, "ts": datetime.now(timezone.utc)}

    # ── Helpers RSI / MACD ───────────────────────────────────────

    @staticmethod
    def _calc_rsi(closes: list, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

    @staticmethod
    def _calc_ema(closes: list, period: int) -> list:
        if len(closes) < period:
            return []
        k   = 2 / (period + 1)
        ema = [sum(closes[:period]) / period]
        for p in closes[period:]:
            ema.append(p * k + ema[-1] * (1 - k))
        return ema

    def _calc_macd(self, closes: list):
        ema12 = self._calc_ema(closes, 12)
        ema26 = self._calc_ema(closes, 26)
        if not ema12 or not ema26:
            return None, None, None
        offset    = len(ema12) - len(ema26)
        macd_line = [ema12[i + offset] - ema26[i] for i in range(len(ema26))]
        signal    = self._calc_ema(macd_line, 9)
        if not signal:
            return None, None, None
        hist = macd_line[-1] - signal[-1]
        return round(macd_line[-1], 6), round(signal[-1], 6), round(hist, 6)

    # ── 1. yfinance → velas M1 EURUSD (GRATIS, sin API key) ──────

    def get_technical_signal(self) -> dict:
        cached = self._cache_get("technical")
        if cached:
            return cached

        result = {
            "rsi": None, "macd_signal": None, "macd_hist": None,
            "candle_trend": None, "last_price": None,
            "high": None, "low": None, "source": None, "error": None
        }

        if not YFINANCE_OK:
            result["error"] = "yfinance no instalado — corre: pip install yfinance"
            self._cache_set("technical", result)
            return result

        try:
            ticker = yf.Ticker(self.YF_SYMBOL)
            # period="1d" interval="1m" → ~390 velas del día actual (mercado abierto)
            df = ticker.history(period="1d", interval="1m")

            if df is None or df.empty:
                # Fallback: últimas 5 días para asegurar datos (fin de semana)
                df = ticker.history(period="5d", interval="5m")

            if df is None or df.empty:
                result["error"] = "yfinance: sin datos (mercado cerrado o símbolo inválido)"
                self._cache_set("technical", result)
                return result

            closes = df["Close"].dropna().tolist()
            highs  = df["High"].dropna().tolist()
            lows   = df["Low"].dropna().tolist()

            if not closes:
                result["error"] = "yfinance: lista de cierres vacía"
                self._cache_set("technical", result)
                return result

            result["source"]     = "yfinance"
            result["last_price"] = round(closes[-1], 5)
            result["high"]       = round(max(highs[-20:]), 5) if highs else None
            result["low"]        = round(min(lows[-20:]),  5) if lows  else None

            if len(closes) >= 5:
                result["candle_trend"] = "UP" if closes[-1] > sum(closes[-5:]) / 5 else "DOWN"

            result["rsi"] = self._calc_rsi(closes)

            ml, sl, hist = self._calc_macd(closes)
            if ml is not None:
                result["macd_signal"] = "BUY" if ml > sl else "SELL"
                result["macd_hist"]   = hist

        except Exception as e:
            result["error"] = f"yfinance excepción: {e}"

        self._cache_set("technical", result)
        return result

    # ── 2. Myfxbook (vía MyfxbookClient) ──────────────────────────

    def get_myfxbook_sentiment(self, symbol: str = "EURUSD") -> dict:
        """
        Devuelve el sentimiento Myfxbook para `symbol` con cache de 55s.
        Estructura: {long_pct, short_pct, signal, error}.
        """
        cache_key = f"myfxbook_{symbol}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        result = {"long_pct": None, "short_pct": None, "signal": None, "error": None}
        sentiment = self.myfxbook.get_sentiment(symbol)

        if sentiment is None:
            result["error"] = f"Myfxbook: sentimiento no disponible para {symbol}"
            self._cache_set(cache_key, result)
            return result

        long_pct  = round(float(sentiment["long_pct"]),  1)
        short_pct = round(float(sentiment["short_pct"]), 1)
        result["long_pct"]  = long_pct
        result["short_pct"] = short_pct

        if short_pct >= 65:
            result["signal"] = f"CONTRA-SIGNAL BUY ({short_pct}% short)"
        elif long_pct >= 65:
            result["signal"] = f"CONTRA-SIGNAL SELL ({long_pct}% long)"
        else:
            result["signal"] = "NEUTRAL (mercado dividido)"

        self._cache_set(cache_key, result)
        return result

    # ── Texto para el prompt ──────────────────────────────────────

    def get_context_text(self) -> str:
        lines = ["=== CONTEXTO EXTERNO DE MERCADO ==="]

        tech = self.get_technical_signal()
        src  = tech.get("source", "yfinance")
        lines.append(f"  [Técnico — {src} velas M1 + RSI/MACD calculados]")
        if tech["error"]:
            lines.append(f"    ⚠ {tech['error']}")
        else:
            if tech.get("last_price"):
                lines.append(f"    Precio EUR/USD : {tech['last_price']}")
            if tech.get("high") and tech.get("low"):
                lines.append(f"    H/L (20 velas) : {tech['high']} / {tech['low']}")
            if tech.get("candle_trend"):
                lines.append(f"    Tendencia M1   : {tech['candle_trend']}")
            if tech["rsi"] is not None:
                warn = " ⚠️ SOBRECOMPRADO" if tech["rsi"] >= 70 else " ⚠️ SOBREVENDIDO" if tech["rsi"] <= 30 else ""
                lines.append(f"    RSI(14)        : {tech['rsi']}{warn}")
            if tech["macd_signal"]:
                lines.append(f"    MACD           : {tech['macd_signal']} (hist: {tech.get('macd_hist','?')})")

        lines.append("  [Myfxbook Community Outlook — login autenticado]")
        mfx = self.get_myfxbook_sentiment()
        if mfx["error"]:
            lines.append(f"    ⚠ {mfx['error']}")
        else:
            lines.append(f"    Long: {mfx['long_pct']}%  Short: {mfx['short_pct']}%")
            lines.append(f"    Señal contraria : {mfx['signal']}")

        lines.append("")
        return "\n".join(lines)
