"""
myfxbook_client.py
──────────────────────────────────────────────
Cliente para la API de Myfxbook.
Obtiene el sentimiento de la comunidad (community outlook)
para un símbolo dado.

Variables de entorno requeridas:
  MYFXBOOK_EMAIL    - email de la cuenta Myfxbook
  MYFXBOOK_PASSWORD - contraseña de la cuenta Myfxbook
──────────────────────────────────────────────
"""

import os
import logging
from urllib.parse import unquote

import requests

logger = logging.getLogger(__name__)


class MyfxbookClient:
    """
    Cliente ligero para la API pública de Myfxbook.

    Uso:
        client = MyfxbookClient()
        sentiment = client.get_sentiment("EURUSD")
        # {"long_pct": 62.5, "short_pct": 37.5}
    """

    LOGIN_URL   = "https://www.myfxbook.com/api/login.json"
    OUTLOOK_URL = "https://www.myfxbook.com/api/get-community-outlook.json"

    def __init__(self):
        self._email    = os.getenv("MYFXBOOK_EMAIL", "")
        self._password = os.getenv("MYFXBOOK_PASSWORD", "")
        self._session: str | None = None   # token cacheado

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _login(self) -> bool:
        """
        Obtiene un token de sesión de Myfxbook y lo guarda en self._session.
        Devuelve True si el login fue exitoso, False en caso contrario.
        """
        if not self._email or not self._password:
            logger.warning(
                "MyfxbookClient: MYFXBOOK_EMAIL o MYFXBOOK_PASSWORD no están configurados."
            )
            return False

        try:
            response = requests.get(
                self.LOGIN_URL,
                params={
                    "email":    unquote(self._email),
                    "password": unquote(self._password),
                },
                timeout=10,
            )
            data = response.json()
        except Exception as exc:
            logger.error("MyfxbookClient: error en login HTTP: %s", exc)
            return False

        if data.get("error", True):
            logger.error(
                "MyfxbookClient: login fallido. Respuesta: %s",
                data.get("message", data),
            )
            return False

        # Guardar el token TAL CUAL lo devuelve la API (ya viene URL-encoded).
        # NO aplicar unquote() aquí: el token contiene caracteres especiales
        # (%2B, %2F, %3D) que deben mantenerse encoded para que la API los
        # acepte correctamente en peticiones posteriores.
        self._session = data.get("session", "")
        logger.info("MyfxbookClient: sesión obtenida correctamente.")
        return True

    def _ensure_session(self) -> bool:
        """Reutiliza la sesión cacheada o realiza un nuevo login."""
        if self._session:
            return True
        return self._login()

    # ------------------------------------------------------------------
    # Community Outlook
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """
        Normaliza el símbolo al formato que devuelve Myfxbook.
        Ejemplos:
          EURUSD  -> EUR/USD
          eurusd  -> EUR/USD
          EUR/USD -> EUR/USD  (sin cambio)
        Los pares de 6 letras sin barra se dividen en 3+3.
        """
        symbol = symbol.upper().replace("-", "").replace("_", "")
        if "/" not in symbol and len(symbol) == 6:
            return f"{symbol[:3]}/{symbol[3:]}"
        return symbol

    def get_sentiment(self, symbol: str) -> dict | None:
        """
        Retorna el sentimiento de la comunidad para el símbolo.

        Retorna:
            {"long_pct": float, "short_pct": float}  si hay datos.
            None si hay error o el símbolo no se encuentra.
        """
        if not self._ensure_session():
            return None

        normalized = self._normalize_symbol(symbol)

        for attempt in range(2):   # un reintento si la sesión expiró
            try:
                # IMPORTANTE: pasar el session token como parte de la URL
                # directamente (sin params=) para evitar que requests lo
                # re-encodee y corrompa los caracteres especiales del token.
                url = f"{self.OUTLOOK_URL}?session={self._session}"
                response = requests.get(url, timeout=10)
                data = response.json()
            except Exception as exc:
                logger.error("MyfxbookClient: error HTTP al obtener outlook: %s", exc)
                return None

            if data.get("error", True):
                if attempt == 0:
                    # La sesión puede haber expirado; renovar y reintentar
                    logger.warning(
                        "MyfxbookClient: sesión inválida (%s), renovando…",
                        data.get("message", ""),
                    )
                    self._session = None
                    if not self._login():
                        return None
                    continue
                else:
                    logger.error(
                        "MyfxbookClient: outlook fallido tras renovación. %s",
                        data.get("message", data),
                    )
                    return None

            # Buscar el símbolo en la lista de symbols
            symbols_list = data.get("symbols", [])
            for entry in symbols_list:
                name = entry.get("name", "").upper()
                if name == normalized or name.replace("/", "") == normalized.replace("/", ""):
                    long_pct  = float(entry.get("longsPercentage",  0))
                    short_pct = float(entry.get("shortsPercentage", 0))
                    logger.info(
                        "MyfxbookClient: %s -> %.1f%% long / %.1f%% short",
                        symbol, long_pct, short_pct,
                    )
                    return {"long_pct": long_pct, "short_pct": short_pct}

            logger.warning(
                "MyfxbookClient: símbolo '%s' ('%s') no encontrado en community outlook.",
                symbol, normalized,
            )
            return None

        return None
