"""
market_context.py
================================================
Contexto externo de mercado — Estrategia ASM

Fuentes (todas FREE):
  1. Finnhub          → /forex/candle + /indicator (RSI, MACD) + /calendar/economic
  2. Myfxbook         → Community Outlook (% long/short) — sin API key
  3. jblanked         → calendario MQL5 + ForexFactory (sin key en FF)
  4. Alpha Vantage    → NEWS_SENTIMENT (ticker: FOREX:EUR)

Salidas públicas:
  get_context_text()   → str  : bloque completo para inyectar en el prompt
  should_hold_news()   → (bool, str): bloquear por noticia de alto impacto
================================================
"""

import os
import requests
from datetime import datetime, timezone, timedelta

try:
    import finnhub
    FINNHUB_OK = True
except ImportError:
    FINNHUB_OK = False

try:
    from jb_news import JBNews
    JBNEWS_OK = True
except ImportError:
    JBNEWS_OK = False


class MarketContext:
    NEWS_BLOCK_MINUTES = 30
    FINNHUB_SYMBOL     = "OANDA:EUR_USD"
    MYFXBOOK_URL       = "https://www.myfxbook.com/api/get-community-outlook.json"
    JBLANKED_FF_URL    = "https://www.jblanked.com/news/api/forex/calendar/today/"

    def __init__(self):
        self.finnhub_key  = os.getenv("FINNHUB_API_KEY", "")
        self.jblanked_key = os.getenv("JBLANKED_API_KEY", "")
        self.av_key       = os.getenv("ALPHAVANTAGE_API_KEY", "")

        self._finnhub_client = None
        if FINNHUB_OK and self.finnhub_key:
            self._finnhub_client = finnhub.Client(api_key=self.finnhub_key)

        self._jbnews_client = None
        if JBNEWS_OK and self.jblanked_key:
            self._jbnews_client = JBNews(api_key=self.jblanked_key)

        self._cache: dict = {}
        self._cache_ttl   = 55

    # ── Cache ───────────────────────────────────────────────────────

    def _cache_get(self, key: str):
        entry = self._cache.get(key)
        if not entry:
            return None
        age = (datetime.now(timezone.utc) - entry["ts"]).total_seconds()
        return entry["data"] if age < self._cache_ttl else None

    def _cache_set(self, key: str, data):
        self._cache[key] = {"data": data, "ts": datetime.now(timezone.utc)}

    # ── 1. Finnhub: RSI + MACD + velas (FREE) ───────────────────────

    def get_technical_signal(self) -> dict:
        cached = self._cache_get("technical")
        if cached:
            return cached

        result = {
            "rsi": None, "macd_signal": None, "macd_hist": None,
            "candle_trend": None, "last_price": None, "error": None
        }

        if not self.finnhub_key:
            result["error"] = "FINNHUB_API_KEY no configurada"
            self._cache_set("technical", result)
            return result

        now_ts  = int(datetime.now(timezone.utc).timestamp())
        from_ts = now_ts - 3600

        try:
            # RSI
            r_rsi = requests.get(
                "https://finnhub.io/api/v1/indicator",
                params={
                    "symbol": self.FINNHUB_SYMBOL, "resolution": "1",
                    "indicator": "rsi", "timeperiod": 14,
                    "from": from_ts, "to": now_ts,
                    "token": self.finnhub_key
                },
                timeout=6,
            )
            if r_rsi.status_code == 200:
                data = r_rsi.json()
                rsi_list = data.get("rsi", [])
                if rsi_list:
                    result["rsi"] = round(rsi_list[-1], 2)
                else:
                    result["error"] = f"Finnhub RSI vacío — respuesta: {str(data)[:100]}"
            else:
                result["error"] = f"Finnhub /indicator HTTP {r_rsi.status_code}: {r_rsi.text[:120]}"

            # MACD
            r_macd = requests.get(
                "https://finnhub.io/api/v1/indicator",
                params={
                    "symbol": self.FINNHUB_SYMBOL, "resolution": "1",
                    "indicator": "macd",
                    "from": from_ts, "to": now_ts,
                    "token": self.finnhub_key
                },
                timeout=6,
            )
            if r_macd.status_code == 200:
                md   = r_macd.json()
                macd = md.get("macd", [])
                sig  = md.get("macdSignal", [])
                if macd and sig:
                    result["macd_signal"] = "BUY" if macd[-1] > sig[-1] else "SELL"
                    result["macd_hist"]   = round(macd[-1] - sig[-1], 5)

            # Velas M1 — tendencia simple
            r_candle = requests.get(
                "https://finnhub.io/api/v1/forex/candle",
                params={
                    "symbol": self.FINNHUB_SYMBOL, "resolution": "1",
                    "from": from_ts, "to": now_ts,
                    "token": self.finnhub_key
                },
                timeout=6,
            )
            if r_candle.status_code == 200:
                candles = r_candle.json()
                closes  = candles.get("c", [])
                if len(closes) >= 5:
                    avg5   = sum(closes[-5:]) / 5
                    latest = closes[-1]
                    result["candle_trend"] = "UP" if latest > avg5 else "DOWN"
                    result["last_price"]   = round(latest, 5)

        except Exception as e:
            result["error"] = str(e)

        self._cache_set("technical", result)
        return result

    # ── 2. Myfxbook: % Long/Short sin API key ────────────────────────
    # JSON: {"symbols": [{"name": "EURUSD", "longPercentage": 44, "shortPercentage": 55, ...}]}

    def get_myfxbook_sentiment(self) -> dict:
        cached = self._cache_get("myfxbook")
        if cached:
            return cached

        result = {"long_pct": None, "short_pct": None, "signal": None, "error": None}

        try:
            r = requests.get(
                self.MYFXBOOK_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)"},
                timeout=10,
            )
            if r.status_code != 200:
                result["error"] = f"Myfxbook HTTP {r.status_code}"
                self._cache_set("myfxbook", result)
                return result

            data    = r.json()
            symbols = data.get("symbols", [])

            # Normalizar nombre: quitar /, -, _ y comparar en mayúsculas
            eurusd = None
            for s in symbols:
                raw_name = str(s.get("name", ""))
                normalized = raw_name.upper().replace("/", "").replace("-", "").replace("_", "").replace(" ", "")
                if normalized == "EURUSD":
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
            else:
                # Debug: listar qué nombres llegaron para saber el formato exacto
                names = [s.get("name", "?") for s in symbols[:8]]
                result["error"] = f"EURUSD no encontrado. Nombres disponibles: {names}"

        except Exception as e:
            result["error"] = f"Myfxbook excepción: {e}"

        self._cache_set("myfxbook", result)
        return result

    # ── 3a. Finnhub: Calendario económico FREE ────────────────────────

    def get_finnhub_calendar(self) -> list:
        cached = self._cache_get("finnhub_cal")
        if cached is not None:
            return cached

        events = []
        if not self.finnhub_key:
            return events

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={"from": today, "to": today, "token": self.finnhub_key},
                timeout=8,
            )
            if r.status_code == 200:
                for evt in r.json().get("economicCalendar", []):
                    country = str(evt.get("country", "")).upper()
                    if country not in ("US", "EU", "DE", "FR", "IT", "ES"):
                        continue
                    impact = str(evt.get("impact", "")).upper()
                    events.append({
                        "source":        "Finnhub",
                        "name":          evt.get("event", ""),
                        "country":       country,
                        "impact":        impact if impact else "LOW",
                        "time":          str(evt.get("time", "")),
                        "actual":        evt.get("actual",   "pendiente"),
                        "forecast":      evt.get("estimate", "-"),
                        "previous":      evt.get("prev",     "-"),
                        "currency":      "USD" if country == "US" else "EUR",
                        "strength":      "", "quality": "", "outcome": "", "ml_prediction": "",
                    })
            else:
                print(f"[MarketContext] Finnhub calendar HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:
            print(f"[MarketContext] Finnhub calendar error: {e}")

        self._cache_set("finnhub_cal", events)
        return events

    # ── 3b. jblanked: MQL5 + ForexFactory ────────────────────────────

    def get_news_calendar(self) -> list:
        cached = self._cache_get("news")
        if cached is not None:
            return cached

        events = []

        # jblanked MQL5 (requiere key)
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

        # jblanked ForexFactory (sin key)
        try:
            r_ff = requests.get(
                self.JBLANKED_FF_URL,
                headers={"Content-Type": "application/json"},
                timeout=8,
            )
            if r_ff.status_code == 200:
                raw_ff = r_ff.json()
                # Puede ser lista directa o dict con distintas claves
                if isinstance(raw_ff, dict):
                    raw_ff = raw_ff.get("calendar",
                             raw_ff.get("data",
                             raw_ff.get("events",
                             raw_ff.get("results", []))))
                for evt in (raw_ff or []):
                    currency = str(evt.get("currency", "")).upper()
                    if currency not in ("EUR", "USD"):
                        continue
                    impact_raw = str(evt.get("impact", evt.get("importance", ""))).upper()
                    impact     = impact_raw if impact_raw in ("HIGH", "MEDIUM", "LOW") else "LOW"
                    name = evt.get("name", evt.get("title", ""))
                    time = str(evt.get("date", evt.get("time", evt.get("datetime", ""))))
                    if not any(e["name"] == name and e["time"] == time for e in events):
                        events.append({
                            "source":        "ForexFactory",
                            "name":          name,
                            "currency":      currency,
                            "impact":        impact,
                            "strength":      "", "quality": "", "outcome": "",
                            "time":          time,
                            "actual":        evt.get("actual",   "pendiente"),
                            "forecast":      evt.get("forecast", "-"),
                            "previous":      evt.get("previous", "-"),
                            "ml_prediction": "",
                        })
            else:
                print(f"[MarketContext] ForexFactory HTTP {r_ff.status_code}: {r_ff.text[:120]}")
        except Exception as e:
            print(f"[MarketContext] ForexFactory error: {e}")

        # Finnhub calendar (deduplicado)
        for evt in self.get_finnhub_calendar():
            name = evt["name"]
            time = evt["time"]
            if not any(e["name"] == name and e["time"] == time for e in events):
                events.append(evt)

        self._cache_set("news", events)
        return events

    # ── 4. Alpha Vantage: NEWS_SENTIMENT ─────────────────────────────
    # Ticker correcto: "FOREX:EUR" (no FOREX:EURUSD)

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
                # Detectar límite de plan
                if "Information" in body or "Note" in body:
                    result["error"] = body.get("Information", body.get("Note", "AV: límite alcanzado"))[:120]
                    self._cache_set("av_sentiment", result)
                    return result

                feed = body.get("feed", [])
                result["articles"] = len(feed)
                scores = []
                for article in feed:
                    for ts in article.get("ticker_sentiment", []):
                        ticker = ts.get("ticker", "")
                        if "EUR" in ticker or "FOREX" in ticker:
                            try:
                                scores.append(float(ts["ticker_sentiment_score"]))
                            except Exception:
                                pass
                if scores:
                    avg = sum(scores) / len(scores)
                    result["score"] = round(avg, 4)
                    if avg >= 0.15:
                        result["label"] = "BULLISH 📈"
                    elif avg <= -0.15:
                        result["label"] = "BEARISH 📉"
                    else:
                        result["label"] = "NEUTRAL ➡️"
                elif feed:
                    result["error"] = f"AV: {len(feed)} artículos sin ticker EUR en sentiment"
                else:
                    result["error"] = "AV: respuesta vacía (posible límite diario 25 req)"
            else:
                result["error"] = f"AlphaVantage HTTP {r.status_code}"
        except Exception as e:
            result["error"] = f"AlphaVantage excepción: {e}"

        self._cache_set("av_sentiment", result)
        return result

    # ── Bloqueo por noticias ────────────────────────────────────────

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

    # ── Texto para el prompt ────────────────────────────────────────

    def get_context_text(self) -> str:
        lines = ["=== CONTEXTO EXTERNO DE MERCADO ==="]

        # 1. Finnhub técnico
        tech = self.get_technical_signal()
        lines.append("  [Finnhub — FREE /indicator + /forex/candle]")
        if tech["error"]:
            lines.append(f"    ⚠ {tech['error']}")
        else:
            if tech.get("last_price"):
                lines.append(f"    Precio EUR/USD : {tech['last_price']}")
            if tech.get("candle_trend"):
                lines.append(f"    Tendencia M1   : {tech['candle_trend']}")
            if tech["rsi"] is not None:
                warn = ""
                if tech["rsi"] >= 70:   warn = " ⚠️ SOBRECOMPRADO"
                elif tech["rsi"] <= 30: warn = " ⚠️ SOBREVENDIDO"
                lines.append(f"    RSI(14)        : {tech['rsi']}{warn}")
            if tech["macd_signal"]:
                lines.append(f"    MACD           : {tech['macd_signal']} (hist: {tech.get('macd_hist','?')})")

        # 2. Myfxbook
        lines.append("  [Myfxbook Community Outlook — sin API key]")
        mfx = self.get_myfxbook_sentiment()
        if mfx["error"]:
            lines.append(f"    ⚠ {mfx['error']}")
        else:
            lines.append(f"    Long: {mfx['long_pct']}%  Short: {mfx['short_pct']}%")
            lines.append(f"    Señal contraria: {mfx['signal']}")

        # 3. Alpha Vantage
        lines.append("  [Alpha Vantage NEWS_SENTIMENT — 25 req/día]")
        av = self.get_av_sentiment()
        if av["error"]:
            lines.append(f"    ⚠ {av['error']}")
        else:
            lines.append(f"    Score: {av['score']}  |  {av['label']}  ({av['articles']} artículos)")

        # 4. Calendario (3 fuentes)
        events   = self.get_news_calendar()
        high_evt = [e for e in events if e["impact"] == "HIGH"]
        med_evt  = [e for e in events if e["impact"] == "MEDIUM"]
        sources  = set(e.get("source", "") for e in events)
        lines.append(f"  [Calendario EUR/USD — fuentes: {', '.join(sources) if sources else 'ninguna'}]")

        if not events:
            lines.append("    Sin eventos registrados para hoy")
        else:
            lines.append(f"    {len(high_evt)} alto impacto | {len(med_evt)} medio impacto")
            for e in sorted(high_evt, key=lambda x: x["time"])[:5]:
                ml  = f" | ML: {e['ml_prediction']}" if e.get("ml_prediction") else ""
                lines.append(
                    f"    🔴 {e['time'][:16]} | {e['name']} ({e.get('currency','?')})"
                    f" | Actual: {e['actual']} Prev: {e['previous']}{ml} [{e.get('source','')}]"
                )
            for e in sorted(med_evt, key=lambda x: x["time"])[:3]:
                lines.append(
                    f"    🟡 {e['time'][:16]} | {e['name']} ({e.get('currency','?')}) [{e.get('source','')}]"
                )

        # Bloqueo por noticia
        hold, reason = self.should_hold_news()
        if hold:
            lines.append(f"  {reason}")
            lines.append("  ➡️ ACCIÓN RECOMENDADA: HOLD — esperar que pase la noticia.")

        lines.append("")
        return "\n".join(lines)
