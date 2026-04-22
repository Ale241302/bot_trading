# test_myfxbook.py
import os
from dotenv import load_dotenv
load_dotenv()
from modules.myfxbook_client import MyfxbookClient

client = MyfxbookClient()

# Test 1: Login
print("Email:", os.getenv("MYFXBOOK_EMAIL", "NO CONFIGURADO"))
print("Password:", "***" if os.getenv("MYFXBOOK_PASSWORD") else "NO CONFIGURADO")

result = client._login()
print("Login exitoso:", result)
print("Session token:", client._session)

# Test 2: Sentimiento
if result:
    sentiment = client.get_sentiment("EURUSD")
    print("Sentimiento EURUSD:", sentiment)