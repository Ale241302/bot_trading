"""
debug_myfxbook.py
Diagnostico profundo del problema de sesion Myfxbook.
Ejecuta con:
  docker compose run --rm trading-bot python scripts/debug_myfxbook.py
"""
import os
import requests
from urllib.parse import quote, unquote

email = os.getenv("MYFXBOOK_EMAIL", "")
pwd   = os.getenv("MYFXBOOK_PASSWORD", "")

print(f"EMAIL  : {email}")
print(f"PWD    : {pwd}")
print(f"PWD encoded: {quote(unquote(pwd), safe='')}")
print()

# Intento 1: unquote+quote
login_url = (
    f"https://www.myfxbook.com/api/login.json"
    f"?email={quote(unquote(email), safe='')}"
    f"&password={quote(unquote(pwd), safe='')}"
)
print("LOGIN URL:", login_url)
r = requests.get(login_url, timeout=10)
data = r.json()
print("LOGIN RESPONSE:", data)
print()

if not data.get("error", True):
    session = data["session"]
    print(f"SESSION RAW     : {session}")
    print(f"SESSION unquoted: {unquote(session)}")
    session_enc = quote(unquote(session), safe='')
    print(f"SESSION encoded : {session_enc}")
    print()

    # Intento A: session encoded
    url_a = f"https://www.myfxbook.com/api/get-community-outlook.json?session={session_enc}"
    print("OUTLOOK URL A (encoded):", url_a)
    r_a = requests.get(url_a, timeout=10)
    print("RESPONSE A:", r_a.text[:300])
    print()

    # Intento B: session crudo sin ningún encoding
    url_b = f"https://www.myfxbook.com/api/get-community-outlook.json?session={session}"
    print("OUTLOOK URL B (raw):", url_b)
    r_b = requests.get(url_b, timeout=10)
    print("RESPONSE B:", r_b.text[:300])
    print()

    # Intento C: usando params={} de requests
    print("OUTLOOK URL C (params={}):")
    r_c = requests.get(
        "https://www.myfxbook.com/api/get-community-outlook.json",
        params={"session": unquote(session)},
        timeout=10
    )
    print("REAL URL enviada:", r_c.url)
    print("RESPONSE C:", r_c.text[:300])
else:
    print("LOGIN FALLO - no se puede continuar")
