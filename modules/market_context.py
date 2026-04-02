"""
market_context.py
================================================
Contexto externo de mercado — Estrategia ASM

Fuentes:
  1. Finnhub (finnhub-client)  → señales técnicas M1 (RSI, MACD, resumen)
  2. jb-news (PyPI oficial)    → calendario económico con Outcome/Strength/Quality
                                  + ML predictions y GPT sentiment

Salidas públicas:
  get_context_text()   → str  : bloque completo para inyectar en el prompt
  should_hold_news()   → (bool, str): ¿bloquear por noticia de alto impacto?
================================================
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── Imports condicionales (si la librería no está, el módulo degrada gracefully) ──
try:
    import finnhub
    FINNHUB_OK = True
except ImportError:
    FINNHUB_OK = False

try:
    from jb_news import JBNews          # pip install jb-news
    JBNEWS_OK = True
except ImportError:
    JBNEWS_OK = False

import requests  # fallback para cualquier llamada HTTP directa


class MarketContext:
    """
    Obtiene y formatea contexto externo de mercado en cada ciclo del bot.
    Si alguna fuente no está disponible (sin key o error de red),
    el módulo continúa operando con las fuentes disponibles.
    """

    # Bloquear operaciones si hay evento HIGH en los próximos N minutos
    NEWS_BLOCK_MINUTES = 30

    # Símbolo Finnhub para EURUSD Forex (OANDA feed)
    FINNHUB_SYMBOL = "OANDA:EUR_USD"

    def __init__(self):
        self.finnhub_key  = os.getenv("FINNHUB_API_KEY", "")
        self.jblanked_key = os.getenv("JBLANKED_API_KEY", "")

        # Inicializar cliente Finnhub
        self._finnhub_client = None
        if FINNHUB_OK and self.finnhub_key:
            self._finnhub_client = finnhub.Client(api_key=self.finnhub_key)

        # Inicializar cliente jb-news
        self._jbnews_client = None
        if JBNEWS_OK and self.jblanked_key:
            self._jbnews_client = JBNews(api_key=self.jblanked_key)

        # Caché simple (evita llamadas repetidas en el mismo ciclo de 60s)
        self._cache: dict = {}
        self._cache_ttl   = 55   # segundos

    # ── Caché ────────────────────────────────────────────────────────────

    def _cache_get(self, key: str):
        entry = self._cache.get(key)
        if not entry:
            return None
        age = (datetime.now(timezone.utc) - entry["ts"]).total_seconds()
        return entry["data"] if age < self._cache_ttl else None

    def _cache_set(self, key: str, data):
        self._cache[key] = {"data": data, "ts": datetime.now(timezone.utc)}

    # ── Finnhub: Señales técnicas ────────────────────────────────────────

    def get_technical_signal(self) -> dict:
        """
        Retorna resumen técnico de Finnhub en M1.
        {signal, buy, sell, neutral, rsi, macd_signal, adx, error}
        """
        cached = self._cache_get("technical")
        if cached:
            return cached

        result = {"signal": None, "buy": 0, "sell": 0, "neutral": 0,
                  "rsi": None, "macd_signal": None, "adx": None, "error": None}

        if not self._finnhub_client:
            result["error"] = "Finnhub no configurado (FINNHUB_API_KEY ausente)"
            return result

        try:
            # Resumen de indicadores técnicos
            scan = self._finnhub_client.technical_indicator(
                symbol=self.FINNHUB_SYMBOL, resolution="1",
                _from=int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()),
                to=int(datetime.now(timezone.utc).timestamp()),
                indicator="sma",   # solo para validar conexión
            )

            # Aggregate indicators (endpoint correcto)
            r = requests.get(
                "https://finnhub.io/api/v1/scan/technical-indicator",
                params={"symbol": self.FINNHUB_SYMBOL, "resolution": "1",
                        "token": self.finnhub_key},
                timeout=5,
            )
            if r.status_code == 200:
                d = r.json()
                ta = d.get("technicalAnalysis", {})
                result["signal"]  = ta.get("signal", "UNKNOWN").upper()
                result["buy"]     = ta.get("buy", 0)
                result["sell"]    = ta.get("sell", 0)
                result["neutral"] = ta.get("neutral", 0)
                trend = d.get("trend", {})
                result["adx"]     = trend.get("adx")

            # RSI
            now_ts  = int(datetime.now(timezone.utc).timestamp())
            from_ts = now_ts - 3600
            r2 = requests.get(
                "https://finnhub.io/api/v1/indicator",
                params={"symbol": self.FINNHUB_SYMBOL, "resolution": "1",
                        "indicator": "rsi", "timeperiod": 14,
                        "from": from_ts, "to": now_ts,
                        "token": self.finnhub_key},
                timeout=5,
            )
            if r2.status_code == 200:
                rsi_list = r2.json().get("rsi", [])
                if rsi_list:
                    result["rsi"] = round(rsi_list[-1], 2)

            # MACD
            r3 = requests.get(
                "https://finnhub.io/api/v1/indicator",
                params={"symbol": self.FINNHUB_SYMBOL, "resolution": "1",
                        "indicator": "macd",
                        "from": from_ts, "to": now_ts,
                        "token": self.finnhub_key},
                timeout=5,
            )
            if r3.status_code == 200:
                md   = r3.json()
                macd = md.get("macd", [])
                sig  = md.get("macdSignal", [])
                if macd and sig:
                    result["macd_signal"] = "BUY" if macd[-1] > sig[-1] else "SELL"

        except Exception as e:
            result["error"] = str(e)

        self._cache_set("technical", result)
        return result

    # ── jb-news: Calendario económico ───────────────────────────────────

    def get_news_calendar(self) -> list[dict]:
        """
        Obtiene eventos económicos del día para EUR y USD.
        Cada evento: {name, currency, impact, strength, quality,
                      outcome, time, actual, forecast, previous, ml_prediction}
        """
        cached = self._cache_get("news")
        if cached is not None:
            return cached

        events = []

        if not self.jblanked_key:
            self._cache_set("news", events)
            return events

        try:
            if self._jbnews_client:
                # Usar la librería oficial jb-news
                raw = self._jbnews_client.calendar()
            else:
                # Fallback: GET directo
                r = requests.get(
                    "https://www.jblanked.com/news/api/calendar/",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Api-Key {self.jblanked_key}"},
                    timeout=8,
                )
                raw = r.json() if r.status_code == 200 else []

            for evt in (raw or []):
                currency = str(evt.get("currency", "")).upper()
                if currency not in ("EUR", "USD"):
                    continue

                # strength / impact — jblanked usa Outcome, Strength, Quality
                strength = str(evt.get("strength", "")).upper()   # STRONG/WEAK
                quality  = str(evt.get("quality",  "")).upper()   # GOOD/BAD
                outcome  = str(evt.get("outcome",  "")).upper()   # metéón del evento

                # Mapear impact para el bot: HIGH si strength=STRONG
                impact = "HIGH" if strength == "STRONG" else "MEDIUM" if strength == "WEAK" else "LOW"

                events.append({
                    "name":          evt.get("name", ""),
                    "currency":      currency,
                    "impact":        impact,
                    "strength":      strength,
                    "quality":       quality,
                    "outcome":       outcome,
                    "time":          str(evt.get("date", "")),
                    "actual":        evt.get("actual",   "pendiente"),
                    "forecast":      evt.get("forecast", "-"),
                    "previous":      evt.get("previous", "-"),
                    "ml_prediction": evt.get("ml_prediction", ""),  # bullish/bearish
                })

        except Exception as e:
            print(f"[MarketContext] jb-news error: {e}")

        self._cache_set("news", events)
        return events

    # ── Bloqueo por noticias de alto impacto ────────────────────────────

    def should_hold_news(self) -> tuple[bool, str]:
        """
        True si hay evento de impacto HIGH en los próximos NEWS_BLOCK_MINUTES.
        """
        events = self.get_news_calendar()
        now    = datetime.now(timezone.utc)
        window = now + timedelta(minutes=self.NEWS_BLOCK_MINUTES)

        for evt in events:
            if evt["impact"] != "HIGH":
                continue
            time_str = evt["time"]
            evt_dt   = None
            for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
                try:
                    evt_dt = datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

            if evt_dt and now <= evt_dt <= window:
                mins_left = int((evt_dt - now).total_seconds() / 60)
                return True, (
                    f"⚠️ NOTICIA ALTO IMPACTO en {mins_left} min: "
                    f"{evt['name']} ({evt['currency']}) | "
                    f"Strength: {evt['strength']} | Quality: {evt['quality']}"
                )

        return False, ""

    # ── Texto para el prompt ────────────────────────────────────────────

    def get_context_text(self) -> str:
        """Bloque completo de contexto externo listo para inyectar en el prompt."""
        lines = ["=== CONTEXTO EXTERNO DE MERCADO ==="]

        # ── Señales técnicas Finnhub ──
        tech = self.get_technical_signal()
        if tech["error"]:
            lines.append(f"  Finnhub : {tech['error']}")
        else:
            rsi_warn = ""
            if tech["rsi"]:
                if   tech["rsi"] >= 70: rsi_warn = " ⚠️ SOBRECOMPRADO"
                elif tech["rsi"] <= 30: rsi_warn = " ⚠️ SOBREVENDIDO"

            lines.append(f"  Finnhub señal M1  : {tech['signal']}")
            lines.append(f"    BUY:{tech['buy']} SELL:{tech['sell']} NEUTRAL:{tech['neutral']}")
            if tech["rsi"]:
                lines.append(f"    RSI(14)           : {tech['rsi']}{rsi_warn}")
            if tech["macd_signal"]:
                lines.append(f"    MACD              : {tech['macd_signal']}")
            if tech["adx"] is not None:
                adx_label = "tendencia fuerte" if tech["adx"] > 25 else "mercado lateral"
                lines.append(f"    ADX               : {tech['adx']:.1f} ({adx_label})")

        # ── Noticias económicas ──
        events   = self.get_news_calendar()
        high_evt = [e for e in events if e["impact"] == "HIGH"]
        med_evt  = [e for e in events if e["impact"] == "MEDIUM"]

        if not events and not self.jblanked_key:
            lines.append("  jblanked: no configurado (JBLANKED_API_KEY ausente)")
        elif not events:
            lines.append("  Noticias hoy EUR/USD: ninguna registrada")
        else:
            lines.append(f"  Noticias hoy EUR/USD: {len(high_evt)} alto, {len(med_evt)} medio impacto")
            for e in high_evt[:4]:
                ml = f" | ML: {e['ml_prediction']}" if e["ml_prediction"] else ""
                lines.append(
                    f"    🔴 {e['time'][:16]} | {e['name']} ({e['currency']}) "
                    f"| Actual: {e['actual']} Forecast: {e['forecast']} "
                    f"| Strength: {e['strength']} Quality: {e['quality']}{ml}"
                )
            for e in med_evt[:2]:
                lines.append(
                    f"    🟡 {e['time'][:16]} | {e['name']} ({e['currency']})"
                )

        # ── Advertencia de bloqueo ──
        hold, reason = self.should_hold_news()
        if hold:
            lines.append(f"  {reason}")
            lines.append("  ➡️ ACCIÓN RECOMENDADA: HOLD — esperar que pase la noticia.")

        lines.append("")
        return "\n".join(lines)
