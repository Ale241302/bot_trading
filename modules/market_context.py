"""
market_context.py
================================================
Contexto externo de mercado para el bot ASM.

Fuentes:
  1. Finnhub          → señales técnicas en tiempo real (RSI, MACD, EMA)
  2. jblanked News API → calendario económico con impacto y ML prediction

Resultado:
  - market_context.get_context_text()  → bloque listo para inyectar en el prompt
  - market_context.should_hold_news()  → True si hay noticia de alto impacto próxima
================================================
"""

import os
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional


class MarketContext:
    """
    Obtiene y formatea contexto externo de mercado:
      - Señales técnicas de Finnhub
      - Noticias económicas de jblanked
    """

    # ── Configuración ────────────────────────────────────────────────────────
    FINNHUB_BASE   = "https://finnhub.io/api/v1"
    JBLANKED_BASE  = "https://www.jblanked.com/news/api"

    # Noticias: bloquear operaciones si hay evento de alto impacto en los próximos N minutos
    NEWS_BLOCK_MINUTES = 30

    # Símbolo en formato Finnhub (Forex)
    FINNHUB_FOREX_SYMBOL = "OANDA:EUR_USD"

    def __init__(self):
        self.finnhub_key  = os.getenv("FINNHUB_API_KEY", "")
        self.jblanked_key = os.getenv("JBLANKED_API_KEY", "")
        self._cache: dict = {}          # caché simple por tipo + timestamp
        self._cache_ttl   = 60          # segundos antes de refrescar

    # ── Utilidades ───────────────────────────────────────────────────────────

    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache:
            return False
        age = (datetime.now(timezone.utc) - self._cache[key]["ts"]).total_seconds()
        return age < self._cache_ttl

    def _set_cache(self, key: str, data):
        self._cache[key] = {"data": data, "ts": datetime.now(timezone.utc)}

    def _get_cache(self, key: str):
        return self._cache[key]["data"]

    # ── Finnhub: Señales técnicas ─────────────────────────────────────────────

    def get_technical_signal(self, symbol: str = "EURUSD") -> dict:
        """
        Llama a Finnhub /scan/technical-indicator.
        Retorna resumen de señales: {signal, buy_count, sell_count, neutral_count,
                                     rsi, macd_signal, adx}
        """
        cache_key = f"technical_{symbol}"
        if self._is_cache_valid(cache_key):
            return self._get_cache(cache_key)

        result = {
            "signal": "UNKNOWN",
            "buy_count": 0,
            "sell_count": 0,
            "neutral_count": 0,
            "rsi": None,
            "macd_signal": None,
            "trend": None,
            "error": None,
        }

        if not self.finnhub_key:
            result["error"] = "FINNHUB_API_KEY no configurada"
            return result

        try:
            # 1. Aggregate indicator (resumen general)
            r = requests.get(
                f"{self.FINNHUB_BASE}/scan/technical-indicator",
                params={
                    "symbol": self.FINNHUB_FOREX_SYMBOL,
                    "resolution": "1",   # M1
                    "token": self.finnhub_key,
                },
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                summary = data.get("technicalAnalysis", {})
                result["signal"]        = summary.get("signal", "UNKNOWN").upper()
                result["buy_count"]     = summary.get("buy", 0)
                result["sell_count"]    = summary.get("sell", 0)
                result["neutral_count"] = summary.get("neutral", 0)
                result["trend"]         = data.get("trend", {}).get("adx", None)

            # 2. RSI individual
            r2 = requests.get(
                f"{self.FINNHUB_BASE}/indicator",
                params={
                    "symbol": self.FINNHUB_FOREX_SYMBOL,
                    "resolution": "1",
                    "indicator": "rsi",
                    "timeperiod": 14,
                    "token": self.finnhub_key,
                },
                timeout=5,
            )
            if r2.status_code == 200:
                rsi_data = r2.json().get("rsi", [])
                if rsi_data:
                    result["rsi"] = round(rsi_data[-1], 2)

            # 3. MACD
            r3 = requests.get(
                f"{self.FINNHUB_BASE}/indicator",
                params={
                    "symbol": self.FINNHUB_FOREX_SYMBOL,
                    "resolution": "1",
                    "indicator": "macd",
                    "token": self.finnhub_key,
                },
                timeout=5,
            )
            if r3.status_code == 200:
                macd_raw = r3.json()
                macd     = macd_raw.get("macd", [])
                signal   = macd_raw.get("macdSignal", [])
                if macd and signal:
                    diff = macd[-1] - signal[-1]
                    result["macd_signal"] = "BUY" if diff > 0 else "SELL"

        except Exception as e:
            result["error"] = str(e)

        self._set_cache(cache_key, result)
        return result

    # ── jblanked: Calendario económico ───────────────────────────────────────

    def get_news_calendar(self, currency: str = "EUR") -> list[dict]:
        """
        Obtiene el calendario económico del día desde jblanked News API.
        Filtra solo eventos que afecten EUR o USD.
        Retorna lista de: {name, currency, impact, time, outcome, ml_prediction}
        """
        cache_key = f"news_{currency}"
        if self._is_cache_valid(cache_key):
            return self._get_cache(cache_key)

        events = []

        if not self.jblanked_key:
            self._set_cache(cache_key, events)
            return events

        try:
            r = requests.get(
                f"{self.JBLANKED_BASE}/calendar/",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Api-Key {self.jblanked_key}",
                },
                timeout=8,
            )
            if r.status_code == 200:
                raw = r.json()
                # Filtrar por monedas relevantes para EURUSD
                for event in raw:
                    evt_currency = event.get("currency", "").upper()
                    if evt_currency not in ["EUR", "USD"]:
                        continue

                    strength = event.get("strength", "").upper()   # HIGH / MEDIUM / LOW
                    events.append({
                        "name":          event.get("name", ""),
                        "currency":      evt_currency,
                        "impact":        strength,
                        "time":          event.get("date", ""),
                        "actual":        event.get("actual", "pendiente"),
                        "forecast":      event.get("forecast", "-"),
                        "previous":      event.get("previous", "-"),
                        "outcome":       event.get("outcome", ""),
                        "ml_prediction": event.get("ml_prediction", ""),  # bullish/bearish
                    })
        except Exception:
            pass

        self._set_cache(cache_key, events)
        return events

    # ── Lógica de bloqueo por noticias ────────────────────────────────────────

    def should_hold_news(self) -> tuple[bool, str]:
        """
        Retorna (bloquear: bool, motivo: str).
        Bloquea si hay un evento de impacto ALTO en los próximos NEWS_BLOCK_MINUTES.
        """
        events = self.get_news_calendar()
        now    = datetime.now(timezone.utc)
        window = now + timedelta(minutes=self.NEWS_BLOCK_MINUTES)

        for evt in events:
            if evt["impact"] != "HIGH":
                continue
            try:
                # Intentar parsear el campo time (puede venir en varios formatos)
                time_str = evt["time"]
                # Formatos comunes de jblanked
                for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
                    try:
                        evt_dt = datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        evt_dt = None

                if evt_dt and now <= evt_dt <= window:
                    mins_left = int((evt_dt - now).total_seconds() / 60)
                    return True, (
                        f"⚠️ NOTICIA ALTO IMPACTO en {mins_left} min: "
                        f"{evt['name']} ({evt['currency']}) — HOLD obligatorio."
                    )
            except Exception:
                continue

        return False, ""

    # ── Texto para el prompt ──────────────────────────────────────────────────

    def get_context_text(self) -> str:
        """
        Genera el bloque completo de contexto externo para inyectar en el user_message.
        Incluye: señales técnicas Finnhub + calendario de noticias jblanked.
        """
        lines = ["=== CONTEXTO EXTERNO DE MERCADO ==="]

        # ── Señales técnicas ──
        tech = self.get_technical_signal()
        if tech["error"]:
            lines.append(f"  Finnhub: no disponible ({tech['error']})")
        else:
            rsi_tag = ""
            if tech["rsi"]:
                if tech["rsi"] >= 70:
                    rsi_tag = " ⚠️ SOBRECOMPRADO"
                elif tech["rsi"] <= 30:
                    rsi_tag = " ⚠️ SOBREVENDIDO"

            lines.append(f"  Señal técnica Finnhub (M1): {tech['signal']}")
            lines.append(f"    Indicadores BUY: {tech['buy_count']} | SELL: {tech['sell_count']} | NEUTRAL: {tech['neutral_count']}")
            if tech["rsi"]:
                lines.append(f"    RSI(14): {tech['rsi']}{rsi_tag}")
            if tech["macd_signal"]:
                lines.append(f"    MACD signal: {tech['macd_signal']}")
            if tech["trend"]:
                adx_val = tech["trend"]
                adx_tag = "tendencia fuerte" if adx_val > 25 else "mercado lateral"
                lines.append(f"    ADX: {adx_val:.1f} ({adx_tag})")

        # ── Noticias económicas ──
        events = self.get_news_calendar()
        high_impact   = [e for e in events if e["impact"] == "HIGH"]
        medium_impact = [e for e in events if e["impact"] == "MEDIUM"]

        if not events:
            lines.append("  Noticias: no disponibles (revisar JBLANKED_API_KEY)")
        else:
            lines.append(f"  Noticias hoy (EUR/USD): {len(high_impact)} alto impacto, {len(medium_impact)} medio")
            for evt in high_impact[:3]:   # máximo 3 para no saturar el prompt
                ml = f" | ML: {evt['ml_prediction']}" if evt["ml_prediction"] else ""
                lines.append(
                    f"    🔴 [{evt['time'][:16]}] {evt['name']} ({evt['currency']}) "
                    f"| Actual: {evt['actual']} | Forecast: {evt['forecast']}{ml}"
                )
            for evt in medium_impact[:2]:
                lines.append(
                    f"    🟡 [{evt['time'][:16]}] {evt['name']} ({evt['currency']})"
                )

        # ── Bloqueo por noticia próxima ──
        hold_news, hold_reason = self.should_hold_news()
        if hold_news:
            lines.append(f"  {hold_reason}")
            lines.append("  ACCIÓN RECOMENDADA: HOLD — esperar que pase la noticia.")

        lines.append("")
        return "\n".join(lines)
