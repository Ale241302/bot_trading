"""
check_env.py

Diagnostico rapido de todas las conexiones del bot:
  MT5, Myfxbook, Notion, Pinecone.

Ejecuta con:
  python scripts/check_env.py
o dentro de Docker:
  docker compose run --rm trading-bot python scripts/check_env.py

Solo realiza lecturas/handshakes — no abre ordenes ni escribe en Notion.
"""

import os
import json
import requests
from urllib.parse import quote, unquote
from dotenv import load_dotenv

load_dotenv()

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
    "MT5_LOGIN",
    "MT5_PASSWORD",
    "MT5_SERVER",
    "MYFXBOOK_EMAIL",
    "MYFXBOOK_PASSWORD",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
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
        print(f"  {ERR} {v} = (vacia)")

# ── 2. MetaTrader 5 ─────────────────────────────────────────
sep("2. MetaTrader 5 (initialize + login + account_info)")
try:
    import MetaTrader5 as mt5

    login_raw = os.getenv("MT5_LOGIN", "")
    password  = os.getenv("MT5_PASSWORD", "")
    server    = os.getenv("MT5_SERVER", "")

    if not (login_raw and password and server):
        print(f"  {ERR} MT5_LOGIN / MT5_PASSWORD / MT5_SERVER vacias")
    else:
        try:
            login = int(login_raw)
        except ValueError:
            print(f"  {ERR} MT5_LOGIN no es numerico: {login_raw}")
            login = None

        if login is not None:
            if not mt5.initialize():
                print(f"  {ERR} mt5.initialize() fallo: {mt5.last_error()}")
            else:
                authorized = mt5.login(login, password=password, server=server)
                if not authorized:
                    print(f"  {ERR} login fallido: {mt5.last_error()}")
                else:
                    info = mt5.account_info()
                    term = mt5.terminal_info()
                    if info:
                        print(f"  {OK} Cuenta: {info.login} | Server: {info.server}")
                        print(f"  {OK} Balance: {info.balance} {info.currency} | Equity: {info.equity}")
                        print(f"  {OK} Apalancamiento: 1:{info.leverage} | Trade allowed: {info.trade_allowed}")
                    if term:
                        print(f"  {OK} Terminal conectado: {term.connected} | trade_allowed: {term.trade_allowed}")

                    symbol = os.getenv("TRADING_SYMBOL", "EURUSD")
                    sym_info = mt5.symbol_info(symbol)
                    if sym_info is None:
                        print(f"  {WRN} Simbolo {symbol} no disponible en este broker")
                    else:
                        tick = mt5.symbol_info_tick(symbol)
                        if tick:
                            print(f"  {OK} {symbol}: bid={tick.bid} ask={tick.ask} spread={sym_info.spread}")
                        else:
                            print(f"  {WRN} {symbol}: tick no disponible (mercado cerrado?)")
                mt5.shutdown()
except ImportError:
    print(f"  {ERR} paquete MetaTrader5 no instalado (pip install MetaTrader5)")
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

# ── 4. Notion (lectura de la DB, sin crear filas) ───────────
sep("4. Notion (databases.retrieve)")
try:
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DB_ID", "")
    if not notion_token or not notion_db_id:
        print(f"  {ERR} NOTION_TOKEN o NOTION_DB_ID vacias")
    else:
        from notion_client import Client
        client = Client(auth=notion_token)
        db     = client.databases.retrieve(notion_db_id)
        title_blocks = db.get("title", [])
        title  = "".join(t.get("plain_text", "") for t in title_blocks) or "(sin titulo)"
        props  = list(db.get("properties", {}).keys())
        print(f"  {OK} DB encontrada: {title}")
        print(f"  {OK} Propiedades ({len(props)}): {props[:8]}{' ...' if len(props) > 8 else ''}")

        required = [
            "Operación", "Fecha", "Tipo", "Par",
            "Cantidad (Lotes)", "Precio Entrada",
            "Motivo / Análisis IA", "Estado",
        ]
        missing = [p for p in required if p not in props]
        if missing:
            print(f"  {WRN} Propiedades requeridas faltantes: {missing}")
        else:
            print(f"  {OK} Todas las propiedades requeridas estan presentes")
except Exception as e:
    print(f"  {ERR} Excepcion Notion: {e}")

# ── 5. OpenAI (chat.completions.create con 5 tokens) ────────
sep("5. OpenAI (ping de 5 tokens)")
try:
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print(f"  {ERR} OPENAI_API_KEY vacia")
    else:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key, timeout=15)
        model  = os.getenv("OPENAI_MODEL", "gpt-4o")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Responde con la palabra: pong"},
                {"role": "user",   "content": "ping"},
            ],
            max_tokens=5,
            temperature=0,
        )
        content = (resp.choices[0].message.content or "").strip()
        usage   = resp.usage
        print(f"  {OK} Modelo {model} respondio: '{content}'")
        if usage:
            print(f"  {OK} Tokens prompt={usage.prompt_tokens} completion={usage.completion_tokens} total={usage.total_tokens}")
except Exception as e:
    print(f"  {ERR} Excepcion OpenAI: {e}")

# ── 6. Pinecone ──────────────────────────────────────────────
sep("6. Pinecone (list_indexes + describe_index_stats)")
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
            print(f"  {WRN} Conectado | Sin indices aun")

        target = os.getenv("PINECONE_INDEX_NAME", "bottrading")
        if target in names:
            try:
                idx   = pc.Index(target)
                stats = idx.describe_index_stats()
                total = stats.get("total_vector_count", "?")
                dim   = stats.get("dimension", "?")
                print(f"  {OK} Indice '{target}' | dimension: {dim} | vectores: {total}")
            except Exception as e:
                print(f"  {WRN} No se pudo leer stats de '{target}': {e}")
        else:
            print(f"  {WRN} Indice configurado '{target}' no existe en la cuenta")
except Exception as e:
    print(f"  {ERR} Excepcion Pinecone: {e}")

print("\n" + "="*50)
print("  Diagnostico completo")
print("="*50 + "\n")
