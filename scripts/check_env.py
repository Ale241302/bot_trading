"""
check_env.py

Diagnostico rapido de todas las APIs usadas por market_context.
Ejecuta con:
  docker compose run --rm trading-bot python scripts/check_env.py
"""

import os
import json
import requests
from datetime import datetime, timezone

OK  = "\033[92m[OK]\033[0m"
ERR = "\033[91m[FAIL]\033[0m"
WRN = "\033[93m[WARN]\033[0m"

def sep(title):
    print(f"\n{'='*50}\n  {title}\n{'='*50}")

# ── 1. Variables de entorno ────────────────────────────────────────
sep("1. Variables de entorno")

VARS = [
    "FINNHUB_API_KEY",
    "ALPHAVANTAGE_API_KEY",
    "MYFXBOOK_EMAIL",
    "MYFXBOOK_PASSWORD",
    "JBLANKED_API_KEY",
    "OPENAI_API_KEY",
    "NOTION_TOKEN",
    "NOTION_DB_ID",
    "PINECONE_API_KEY",
]

for v in VARS:
    val = os.getenv(v, "")
    if val:
        masked = val[:6] + "..." + val[-4:] if len(val) > 10 else "***"
        print(f"  {OK} {v} = {masked}")
    else:
        tag = WRN if v in ("JBLANKED_API_KEY",) else ERR
        print(f"  {tag} {v} = (vacia)")

# ── 2. Finnhub /forex/candle ───────────────────────────────────────
sep("2. Finnhub /forex/candle (FREE)")
try:
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        print(f"  {ERR} FINNHUB_API_KEY vacia — ve a https://finnhub.io/register")
    else:
        now_ts  = int(datetime.now(timezone.utc).timestamp())
        from_ts = now_ts - 3600
        r = requests.get(
            "https://finnhub.io/api/v1/forex/candle",
            params={"symbol": "OANDA:EUR_USD", "resolution": "1",
                    "from": from_ts, "to": now_ts, "token": key},
            timeout=8,
        )
        if r.status_code == 200:
            data   = r.json()
            closes = data.get("c", [])
            status = data.get("s", "?")
            if closes:
                print(f"  {OK} Velas recibidas: {len(closes)} | ultimo cierre: {closes[-1]:.5f}")
            else:
                print(f"  {WRN} Respuesta OK pero sin velas. status={status}")
                print(f"       (mercado cerrado o fuera de horario Forex)")
        else:
            print(f"  {ERR} HTTP {r.status_code}: {r.text[:150]}")
            if r.status_code == 403:
                print(f"       → Regenera tu key en https://finnhub.io/dashboard")
except Exception as e:
    print(f"  {ERR} Excepcion: {e}")

# ── 3. Myfxbook ────────────────────────────────────────────────
sep("3. Myfxbook login + community outlook")
try:
    email = os.getenv("MYFXBOOK_EMAIL", "")
    pwd   = os.getenv("MYFXBOOK_PASSWORD", "")
    if not email or not pwd:
        print(f"  {ERR} MYFXBOOK_EMAIL o MYFXBOOK_PASSWORD vacias")
    else:
        # Login
        r = requests.get(
            "https://www.myfxbook.com/api/login.json",
            params={"email": email, "password": pwd},
            timeout=10,
        )
        data = r.json() if r.status_code == 200 else {}
        if not data.get("error", True):
            session = data.get("session", "")
            print(f"  {OK} Login exitoso | session = {session[:8]}...")

            # Endpoint 1: get-community-outlook.json (requiere portfolio vinculado)
            r2 = requests.get(
                "https://www.myfxbook.com/api/get-community-outlook.json",
                params={"session": session},
                timeout=10,
            )
            body2 = r2.json() if r2.status_code == 200 else {}
            syms  = body2.get("symbols", [])
            print(f"  [DEBUG] get-community-outlook symbols count: {len(syms)}")
            if syms:
                names = [s.get("name") for s in syms[:8]]
                print(f"  [DEBUG] Nombres: {names}")
                eurusd = next((s for s in syms
                               if "EUR" in str(s.get("name","")).upper()
                               and "USD" in str(s.get("name","")).upper()), None)
                if eurusd:
                    print(f"  {OK} EURUSD: long={eurusd.get('longPercentage')}% short={eurusd.get('shortPercentage')}%")
                else:
                    print(f"  {WRN} EURUSD no encontrado entre: {names}")
            else:
                print(f"  {WRN} symbols vacio. JSON completo: {json.dumps(body2)[:300]}")
                print(f"  CAUSA: cuenta sin portfolio vinculado en myfxbook.com")
                print(f"  ALTERNATIVA: probando endpoint publico sin sesion...")

                # Endpoint 2: sin sesion (endpoint publico antiguo)
                r3 = requests.get(
                    "https://www.myfxbook.com/api/get-community-outlook.json",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/122.0.0.0 Safari/537.36"},
                    timeout=10,
                )
                body3 = r3.json() if r3.status_code == 200 else {}
                syms3 = body3.get("symbols", [])
                print(f"  [DEBUG] Sin sesion: HTTP {r3.status_code} | symbols count: {len(syms3)}")
                if syms3:
                    names3 = [s.get("name") for s in syms3[:8]]
                    print(f"  {OK} Endpoint publico funciona! Nombres: {names3}")
                else:
                    print(f"  {WRN} Endpoint publico tambien vacio. JSON: {json.dumps(body3)[:200]}")
                    print(f"")
                    print(f"  SOLUCION: Vincula un broker demo en myfxbook.com:")
                    print(f"    1. Login en https://www.myfxbook.com")
                    print(f"    2. My Accounts > Add New Account")
                    print(f"    3. Conecta MT5 demo (cualquier broker)")
                    print(f"    4. Vuelve a correr este script")
        else:
            msg = data.get("message", r.text[:150])
            print(f"  {ERR} Login fallido: {msg}")
except Exception as e:
    print(f"  {ERR} Excepcion: {e}")

# ── 4. Alpha Vantage ──────────────────────────────────────────────
sep("4. Alpha Vantage NEWS_SENTIMENT")
try:
    key = os.getenv("ALPHAVANTAGE_API_KEY", "")
    if not key:
        print(f"  {ERR} ALPHAVANTAGE_API_KEY vacia")
    else:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "NEWS_SENTIMENT", "tickers": "FOREX:EUR",
                    "topics": "forex", "limit": 3, "apikey": key},
            timeout=12,
        )
        body = r.json() if r.status_code == 200 else {}
        if "Information" in body or "Note" in body:
            print(f"  {WRN} Limite: {body.get('Information', body.get('Note',''))[:100]}")
        elif body.get("feed"):
            print(f"  {OK} {len(body['feed'])} articulos recibidos")
        else:
            print(f"  {WRN} Sin feed: {str(body)[:150]}")
except Exception as e:
    print(f"  {ERR} Excepcion: {e}")

print("\n" + "="*50)
print("  Diagnostico completo")
print("="*50 + "\n")
