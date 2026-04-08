"""
market_context.py
================================================
Contexto externo de mercado — Estrategia ASM

Fuentes (todas FREE):
  1. Finnhub /forex/candle  → velas M1 + RSI/MACD calculados localmente
  2. Myfxbook login API     → sesión autenticada → % long/short EURUSD
  3. Alpha Vantage          → NEWS_SENTIMENT (FOREX:EUR)
  4. jblanked MQL5          → calendario económico (con key)
================================================
"""

import os
import math
import requests
from urllib.parse import urlencode, quote, unquote
from datetime import datetime, timezone, timedelta

try:
    from jb_news import JBNews
    JBNEWS_OK = True
except ImportError:
    JBNEWS_OK = False


def _safe_encode(token: str) -> str:
    """
    Codifica un token de sesión de Myfxbook de forma segura.
    Myfxbook devuelve el session token ya parcialmente encoded
    (ej: 'YgE6H%2Babc' donde %2B = '+', %2F = '/').
    Si se pasa directamente a quote(), el '%' se re-encodea a '%25',
    produciendo 'YgE6H%252Babc' — Myfxbook responde 'Invalid session'.
    Solución: unquote() primero para obtener el token limpio, luego quote().
    """
    return quote(unquote(token), safe='')


class MarketContext:
    NEWS_BLOCK_MINUTES  = 30
    FINNHUB_SYMBOL      = "OANDA:EUR_USD"
    MFX_LOGIN_URL       = "https://www.myfxbook.com/api/login.json"
    MFX_OUTLOOK_URL     = "https://www.myfxbook.com/api/get-community-outlook.json"

    def __init__(self):
        self.finnhub_key  = os.getenv("FINNHUB_API_KEY", "")
        self.jblanked_key = os.getenv("JBLANKED_API_KEY", "")
        self.av_key       = os.getenv("ALPHAVANTAGE_API_KEY", "")
        self.mfx_email    = os.getenv("MYFXBOOK_EMAIL", "")
        self.mfx_password = os.getenv("MYFXBOOK_PASSWORD", "")
        self._mfx_session = None

        self._jbnews_client = None
        if JBNEWS_OK and self.jblanked_key:
            self._jbnews_client = JBNews(api_key=self.jblanked_key)

        self._cache: dict = {}
        self._cache_ttl   = 55

    # ── Cache ────────────────────────────────────────────────

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

    # ── 1. Finnhub /forex/candle + RSI/MACD propios ───────────────────

    def get_technical_signal(self) -> dict:
        cached = self._cache_get("technical")
        if cached:
            return cached

        result = {
            "rsi": None, "macd_signal": None, "macd_hist": None,
            "candle_trend": None, "last_price": None,
            "high": None, "low": None, "error": None
        }

        if not self.finnhub_key:
            result["error"] = "FINNHUB_API_KEY no configurada"
            self._cache_set("technical", result)
            return result

        now_ts  = int(datetime.now(timezone.utc).timestamp())
        from_ts = now_ts - 7200

        try:
            r = requests.get(
                "https://finnhub.io/api/v1/forex/candle",
                params={
                    "symbol": self.FINNHUB_SYMBOL, "resolution": "1",
                    "from": from_ts, "to": now_ts,
                    "token": self.finnhub_key
                },
                timeout=8,
            )
            if r.status_code != 200:
                result["error"] = f"Finnhub /forex/candle HTTP {r.status_code}: {r.text[:120]}"
                self._cache_set("technical", result)
                return result

            candles = r.json()
            closes  = candles.get("c", [])
            highs   = candles.get("h", [])
            lows    = candles.get("l", [])
            status  = candles.get("s", "")

            if status == "no_data" or not closes:
                result["error"] = "Finnhub: sin datos de velas (mercado cerrado o fuera de horario)"
                self._cache_set("technical", result)
                return result

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
            result["error"] = str(e)

        self._cache_set("technical", result)
        return result

    # ── 2. Myfxbook login → sesión → outlook EURUSD ─────────────────
    #
    #  IMPORTANTE: Myfxbook devuelve el session token ya parcialmente
    #  encoded (ej: 'YgE6H%2Babc'). Usar _safe_encode() que hace
    #  unquote() + quote() para evitar doble encoding (%25xx).

    def _mfx_get_session(self):
        if self._mfx_session:
            return self._mfx_session
        if not self.mfx_email or not self.mfx_password:
            return None
        try:
            url = (
                f"{self.MFX_LOGIN_URL}"
                f"?email={_safe_encode(self.mfx_email)}"
                f"&password={_safe_encode(self.mfx_password)}"
            )
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if not data.get("error", True):
                    self._mfx_session = data.get("session", "")
                    return self._mfx_session
                print(f"[MarketContext] Myfxbook login error: {data.get('message', data)}")
        except Exception as e:
            print(f"[MarketContext] Myfxbook login excepción: {e}")
        return None

    def get_myfxbook_sentiment(self) -> dict:
        cached = self._cache_get("myfxbook")
        if cached:
            return cached

        result = {"long_pct": None, "short_pct": None, "signal": None, "error": None}

        session = self._mfx_get_session()
        if not session:
            result["error"] = "Myfxbook: sin sesión (revisar MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD)"
            self._cache_set("myfxbook", result)
            return result

        try:
            # _safe_encode: unquote() + quote() para evitar doble %25xx
            url = f"{self.MFX_OUTLOOK_URL}?session={_safe_encode(session)}"
            r   = requests.get(url, timeout=10)

            if r.status_code != 200:
                result["error"] = f"Myfxbook outlook HTTP {r.status_code}"
                self._cache_set("myfxbook", result)
                return result

            data = r.json()
            if data.get("error"):
                # Sesión inválida — limpiar caché y reintentar una vez
                self._mfx_session = None
                session2 = self._mfx_get_session()
                if session2:
                    url2 = f"{self.MFX_OUTLOOK_URL}?session={_safe_encode(session2)}"
                    r    = requests.get(url2, timeout=10)
                    data = r.json() if r.status_code == 200 else {}

            symbols = data.get("symbols", [])
            eurusd  = None
            for s in symbols:
                name = str(s.get("name", "")).upper()
                name = name.replace("/","").replace("-","").replace("_","").replace(" ","")
                if name == "EURUSD":
                    eurusd = s
                    break

            if eurusd:
                long_pct  = float(eurusd.get("longPercentage",  eurusd.get("longVolume",  0)))
                short_pct = float(eurusd.get("shortPercentage", eurusd.get("shortVolume", 0)))
                result["long_pct"]  = round(long_pct,  1)
                result["short_pct"] = round(short_pct, 1)
                if short_pct >= 65:
                    result["signal"] = f"CONTRA-SIGNAL BUY ({short_pct}% short)"
                elif long_pct >= 65:
                    result["signal"] = f"CONTRA-SIGNAL SELL ({long_pct}% long)"
                else:
                    result["signal"] = "NEUTRAL (mercado dividido)"
            elif symbols:
                names = [s.get("name", "?") for s in symbols[:8]]
                result["error"] = f"EURUSD no encontrado. Disponibles: {names}"
            else:
                result["error"] = f"Myfxbook: symbols vacío. {data.get('message', 'sin mensaje')}"

        except Exception as e:
            result["error"] = f"Myfxbook excepción: {e}"

        self._cache_set("myfxbook", result)
        return result

    # ── 3. Alpha Vantage: NEWS_SENTIMENT ─────────────────────────────

    def get_av_sentiment(self) -> dict:
        cached = self._cache_get("av_sentiment")
        if cached:
            return cached

        result = {"score": None, "label": None, "articles": 0, "error": None}

        if not self.av_key:
            result["error"] = "ALPHAVANTAGE_API_KEY no configurada"
            self._cache_set("av_sentiment", result)
            return result

        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "topics":   "forex",
                    "tickers":  "FOREX:EUR",
                    "limit":    10,
                    "apikey":   self.av_key,
                },
                timeout=12,
            )
            if r.status_code == 200:
                body = r.json()
                if "Information" in body or "Note" in body:
                    result["error"] = body.get("Information", body.get("Note", "AV límite"))[:120]
                    self._cache_set("av_sentiment", result)
                    return result
                feed = body.get("feed", [])
                result["articles"] = len(feed)
                scores = []
                for article in feed:
                    for ts in article.get("ticker_sentiment", []):
                        if "EUR" in ts.get("ticker", "") or "FOREX" in ts.get("ticker", ""):
                            try:
                                scores.append(float(ts["ticker_sentiment_score"]))
                            except Exception:
                                pass
                if scores:
                    avg = sum(scores) / len(scores)
                    result["score"] = round(avg, 4)
                    result["label"] = "BULLISH 📈" if avg >= 0.15 else "BEARISH 📉" if avg <= -0.15 else "NEUTRAL ➡️"
                elif feed:
                    result["error"] = f"AV: {len(feed)} artículos sin score EUR"
                else:
                    result["error"] = "AV: sin artículos (límite diario o key inválida)"
            else:
                result["error"] = f"AlphaVantage HTTP {r.status_code}"
        except Exception as e:
            result["error"] = f"AlphaVantage excepción: {e}"

        self._cache_set("av_sentiment", result)
        return result

    # ── 4. jblanked: calendario económico MQL5 ───────────────────────

    def get_news_calendar(self) -> list:
        cached = self._cache_get("news")
        if cached is not None:
            return cached

        events = []

        if self.jblanked_key:
            try:
                if self._jbnews_client:
                    raw = self._jbnews_client.calendar() or []
                else:
                    r = requests.get(
                        "https://www.jblanked.com/news/api/calendar/",
                        headers={"Authorization": f"Api-Key {self.jblanked_key}"},
                        timeout=8,
                    )
                    raw = r.json() if r.status_code == 200 else []
                for evt in raw:
                    currency = str(evt.get("currency", "")).upper()
                    if currency not in ("EUR", "USD"):
                        continue
                    strength = str(evt.get("strength", "")).upper()
                    impact   = "HIGH" if strength == "STRONG" else "MEDIUM" if strength == "WEAK" else "LOW"
                    events.append({
                        "source":        "jblanked-MQL5",
                        "name":          evt.get("name", ""),
                        "currency":      currency,
                        "impact":        impact,
                        "strength":      strength,
                        "quality":       str(evt.get("quality", "")).upper(),
                        "outcome":       str(evt.get("outcome", "")).upper(),
                        "time":          str(evt.get("date", "")),
                        "actual":        evt.get("actual",   "pendiente"),
                        "forecast":      evt.get("forecast", "-"),
                        "previous":      evt.get("previous", "-"),
                        "ml_prediction": evt.get("ml_prediction", ""),
                    })
            except Exception as e:
                print(f"[MarketContext] jblanked MQL5 error: {e}")

        self._cache_set("news", events)
        return events

    # ── Bloqueo por noticias ───────────────────────────────────────

    def should_hold_news(self) -> tuple:
        events = self.get_news_calendar()
        now    = datetime.now(timezone.utc)
        window = now + timedelta(minutes=self.NEWS_BLOCK_MINUTES)
        for evt in events:
            if evt["impact"] != "HIGH":
                continue
            evt_dt = None
            for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
                try:
                    evt_dt = datetime.strptime(evt["time"], fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if evt_dt and now <= evt_dt <= window:
                mins = int((evt_dt - now).total_seconds() / 60)
                return True, (
                    f"⚠️ ALTO IMPACTO en {mins} min: {evt['name']} "
                    f"({evt.get('currency','?')}) [{evt.get('source','')}]"
                )
        return False, ""

    # ── Texto para el prompt ───────────────────────────────────────

    def get_context_text(self) -> str:
        lines = ["=== CONTEXTO EXTERNO DE MERCADO ==="]

        tech = self.get_technical_signal()
        lines.append("  [Técnico — Finnhub /forex/candle + RSI/MACD calculados]")
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

        lines.append("  [Alpha Vantage NEWS_SENTIMENT — 25 req/día]")
        av = self.get_av_sentiment()
        if av["error"]:
            lines.append(f"    ⚠ {av['error']}")
        else:
            lines.append(f"    Score: {av['score']}  |  {av['label']}  ({av['articles']} artículos)")

        events   = self.get_news_calendar()
        high_evt = [e for e in events if e["impact"] == "HIGH"]
        med_evt  = [e for e in events if e["impact"] == "MEDIUM"]
        sources  = set(e.get("source", "") for e in events)
        lines.append(f"  [Calendario EUR/USD — {', '.join(sources) if sources else 'sin fuentes activas'}]")
        if not events:
            lines.append("    Sin eventos (verifica JBLANKED_API_KEY)")
        else:
            lines.append(f"    {len(high_evt)} alto impacto | {len(med_evt)} medio impacto")
            for e in sorted(high_evt, key=lambda x: x["time"])[:5]:
                ml = f" | ML: {e['ml_prediction']}" if e.get("ml_prediction") else ""
                lines.append(
                    f"    🔴 {e['time'][:16]} | {e['name']} ({e.get('currency','?')})"
                    f" | Actual: {e['actual']} Prev: {e['previous']}{ml}"
                )
            for e in sorted(med_evt, key=lambda x: x["time"])[:3]:
                lines.append(f"    🟡 {e['time'][:16]} | {e['name']} ({e.get('currency','?')})")

        hold, reason = self.should_hold_news()
        if hold:
            lines.append(f"  {reason}")
            lines.append("  ➡️ ACCIÓN RECOMENDADA: HOLD — esperar que pase la noticia.")

        lines.append("")
        return "\n".join(lines)
