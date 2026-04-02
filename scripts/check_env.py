"""
check_env.py

Diagnostico rapido de todas las APIs usadas por market_context.
Ejecuta con:
  docker compose run --rm trading-bot python scripts/check_env.py
"""

import os
import json
import requests
from urllib.parse import quote
from datetime import datetime, timezone

OK  = "\033[92m[OK]\033[0m"
ERR = "\033[91m[FAIL]\033[0m"
WRN = "\033[93m[WARN]\033[0m"

def sep(title):
    print(f"\n{'='*50}\n  {title}\n{'='*50}")

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
        print(f"  {ERR} FINNHUB_API_KEY vacia — ve a https://finnhub.io/register")
    else:
        # Primero intentar /forex/candle (requiere plan Starter o superior)
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
                print(f"  {WRN} /forex/candle: sin velas (status={status}) — mercado cerrado?")
        elif r.status_code == 403:
            print(f"  {WRN} /forex/candle: 403 — plan Free no incluye velas históricas")
            print(f"       Usando /quote como alternativa (precio actual, siempre FREE)...")
            # Fallback: /quote siempre disponible en plan Free
            r2 = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": "OANDA:EUR_USD", "token": key},
                timeout=8,
            )
            if r2.status_code == 200:
                q = r2.json()
                c = q.get("c", 0)   # precio actual
                h = q.get("h", 0)   # high del dia
                l = q.get("l", 0)   # low del dia
                pc= q.get("pc", 0)  # cierre anterior
                if c:
                    cambio = round(((c - pc) / pc) * 100, 4) if pc else 0
                    print(f"  {OK} /quote (FREE) — EURUSD: {c:.5f} | H:{h:.5f} L:{l:.5f} | cambio: {cambio:+.4f}%")
                    print(f"       ℹ️  market_context.py ya usa /quote como fallback automático")
                else:
                    print(f"  {WRN} /quote retornó precio 0 (mercado cerrado?)")
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
        # ⚠️  IMPORTANTE: usar quote() para evitar doble encoding del token de sesión
        #  Si se usa params={}, requests re-encodea '/' a '%252F' y Myfxbook
        #  responde "Invalid session"
        login_url = (
            f"https://www.myfxbook.com/api/login.json"
            f"?email={quote(email, safe='')}&password={quote(pwd, safe='')}"
        )
        r    = requests.get(login_url, timeout=10)
        data = r.json() if r.status_code == 200 else {}

        if not data.get("error", True):
            session = data.get("session", "")
            print(f"  {OK} Login exitoso | session = {session[:8]}...")

            # Outlook con sesión — URL manual para evitar doble encoding
            outlook_url = (
                f"https://www.myfxbook.com/api/get-community-outlook.json"
                f"?session={quote(session, safe='')}"
            )
            r2    = requests.get(outlook_url, timeout=10)
            body2 = r2.json() if r2.status_code == 200 else {}
            syms  = body2.get("symbols", [])
            print(f"  [DEBUG] get-community-outlook symbols count: {len(syms)}")

            if syms:
                names  = [s.get("name") for s in syms[:8]]
                print(f"  [DEBUG] Simbolos disponibles: {names}")
                eurusd = next(
                    (s for s in syms
                     if "EUR" in str(s.get("name","")).upper()
                     and "USD" in str(s.get("name","")).upper()),
                    None
                )
                if eurusd:
                    l_pct = eurusd.get('longPercentage',  eurusd.get('longVolume',  '?'))
                    s_pct = eurusd.get('shortPercentage', eurusd.get('shortVolume', '?'))
                    print(f"  {OK} EURUSD: long={l_pct}%  short={s_pct}%")
                else:
                    print(f"  {WRN} EURUSD no encontrado entre: {names}")
            else:
                err_msg = body2.get('message', 'sin mensaje')
                print(f"  {WRN} symbols vacio | mensaje: {err_msg}")
                print(f"  JSON completo: {json.dumps(body2)[:300]}")
                print()
                if "Invalid session" in err_msg:
                    print(f"  DIAGNOSTICO: El token de sesión fue re-encoded por requests.")
                    print(f"               Ya corregido en market_context.py con quote().")
                    print(f"               Este script ahora también usa quote() — si sigue")
                    print(f"               fallando, la cuenta no tiene portfolio vinculado.")
                print(f"  SOLUCION: Vincula tu cuenta Pepperstone Demo en myfxbook.com:")
                print(f"    • My Accounts > tu cuenta > asegúrate que Status = 'Active'")
                print(f"    • Si aparece 'Pending', acepta el email de verificacion")
                print(f"    • La cuenta debe estar en modo 'Public' (no Private)")
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
            print(f"  {OK} Conectado | Sin indices aún (se crean al correr el bot)")
except Exception as e:
    print(f"  {ERR} Excepcion Pinecone: {e}")

print("\n" + "="*50)
print("  Diagnostico completo")
print("="*50 + "\n")
