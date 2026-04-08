"""
check_env.py

Diagnostico rapido de todas las APIs usadas por market_context.
Ejecuta con:
  docker compose run --rm trading-bot python scripts/check_env.py
"""

import os
import json
import requests
from urllib.parse import quote, unquote
from datetime import datetime, timezone

OK  = "\033[92m[OK]\033[0m"
ERR = "\033[91m[FAIL]\033[0m"
WRN = "\033[93m[WARN]\033[0m"

def sep(title):
    print(f"\n{'='*50}\n  {title}\n{'='*50}")

def _safe_encode(token: str) -> str:
    """unquote() + quote() para evitar doble encoding de tokens Myfxbook."""
    return quote(unquote(token), safe='')

# ── 1. Variables de entorno ────────────────────────────────────
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

# ── 2. Finnhub ──────────────────────────────────────────────
sep("2. Finnhub (precio EURUSD)")
try:
    key = os.getenv("FINNHUB_API_KEY", "")
    if not key:
        print(f"  {ERR} FINNHUB_API_KEY vacia")
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
                print(f"  {OK} /forex/candle OK — {len(closes)} velas | cierre: {closes[-1]:.5f}")
            else:
                print(f"  {WRN} /forex/candle: sin velas (status={status})")
        elif r.status_code == 403:
            print(f"  {WRN} /forex/candle: 403 — plan Free no incluye velas historicas")
            r2 = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": "OANDA:EUR_USD", "token": key},
                timeout=8,
            )
            if r2.status_code == 200:
                q = r2.json()
                c = q.get("c", 0)
                if c:
                    pc = q.get("pc", 0)
                    cambio = round(((c - pc) / pc) * 100, 4) if pc else 0
                    print(f"  {OK} /quote EURUSD: {c:.5f} | cambio: {cambio:+.4f}%")
                else:
                    print(f"  {WRN} /quote precio 0 (mercado cerrado?)")
            else:
                print(f"  {ERR} /quote HTTP {r2.status_code}: {r2.text[:100]}")
        else:
            print(f"  {ERR} HTTP {r.status_code}: {r.text[:150]}")
except Exception as e:
    print(f"  {ERR} Excepcion: {e}")

# ── 3. Myfxbook ─────────────────────────────────────────────
sep("3. Myfxbook login + community outlook")
try:
    email = os.getenv("MYFXBOOK_EMAIL", "")
    pwd   = os.getenv("MYFXBOOK_PASSWORD", "")
    if not email or not pwd:
        print(f"  {ERR} MYFXBOOK_EMAIL o MYFXBOOK_PASSWORD vacias")
    else:
        login_url = (
            f"https://www.myfxbook.com/api/login.json"
            f"?email={_safe_encode(email)}&password={_safe_encode(pwd)}"
        )
        r    = requests.get(login_url, timeout=10)
        data = r.json() if r.status_code == 200 else {}

        if not data.get("error", True):
            session_raw = data.get("session", "")
            session_enc = _safe_encode(session_raw)
            print(f"  {OK} Login exitoso")
            print(f"  [DEBUG] session_raw = {session_raw[:12]}...")
            print(f"  [DEBUG] session_enc = {session_enc[:12]}...")

            outlook_url = (
                f"https://www.myfxbook.com/api/get-community-outlook.json"
                f"?session={session_enc}"
            )
            r2    = requests.get(outlook_url, timeout=10)
            body2 = r2.json() if r2.status_code == 200 else {}
            syms  = body2.get("symbols", [])
            print(f"  [DEBUG] symbols count: {len(syms)}")

            if syms:
                names = [s.get("name") for s in syms[:8]]
                print(f"  [DEBUG] Simbolos: {names}")
                eurusd = next(
                    (s for s in syms
                     if "EUR" in str(s.get("name","")).upper()
                     and "USD" in str(s.get("name","")).upper()),
                    None
                )
                if eurusd:
                    l_pct = eurusd.get('longPercentage',  eurusd.get('longVolume',  '?'))
                    s_pct = eurusd.get('shortPercentage', eurusd.get('shortVolume', '?'))
                    print(f"  {OK} EURUSD community outlook: long={l_pct}%  short={s_pct}%")
                else:
                    print(f"  {WRN} EURUSD no encontrado entre: {names}")
            else:
                err_msg = body2.get('message', 'sin mensaje')
                print(f"  {WRN} symbols vacio | mensaje: {err_msg}")
                print(f"  JSON: {json.dumps(body2)[:300]}")
                if "Invalid session" in err_msg:
                    print()
                    print(f"  POSIBLE CAUSA: La cuenta Myfxbook no tiene cartera vinculada.")
                    print(f"  Verifica en https://www.myfxbook.com/portfolio que Status=Active")
        else:
            msg = data.get("message", r.text[:150])
            print(f"  {ERR} Login fallido: {msg}")
except Exception as e:
    print(f"  {ERR} Excepcion: {e}")

# ── 4. Alpha Vantage ─────────────────────────────────────────
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

# ── 5. Pinecone ──────────────────────────────────────────────
sep("5. Pinecone")
try:
    pc_key = os.getenv("PINECONE_API_KEY", "")
    if not pc_key:
        print(f"  {ERR} PINECONE_API_KEY vacia")
    else:
        from pinecone import Pinecone
        pc      = Pinecone(api_key=pc_key)
        indexes = pc.list_indexes()
        names   = [idx.name for idx in indexes]
        if names:
            print(f"  {OK} Conectado | Indices: {names}")
        else:
            print(f"  {OK} Conectado | Sin indices aun")
except Exception as e:
    print(f"  {ERR} Excepcion Pinecone: {e}")

print("\n" + "="*50)
print("  Diagnostico completo")
print("="*50 + "\n")
