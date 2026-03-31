"""
test_notion_connection.py
================================================
Test de conexion con Notion

Que verifica este test:
  1. Que el token de integracion es valido
  2. Que la base de datos existe y es accesible
  3. Que se puede escribir una operacion de prueba
  4. Que se puede leer el historial de operaciones
  5. Que el registro de prueba se elimina al final

Como ejecutarlo:
  python tests/test_notion_connection.py
================================================
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from notion_client import Client
from modules.notion_logger import NotionLogger

# ------------------------------------------------
# Colores para la consola
# ------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}  [PASS]{RESET} {msg}")
def fail(msg): print(f"{RED}  [FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}  [INFO]{RESET} {msg}")

TEST_PAGE_ID = None  # guarda el ID del registro de prueba para eliminarlo al final


def test_token_valid():
    print("\n--- Test 1: Validar token de Notion ---")
    token = os.getenv("NOTION_TOKEN")
    if not token:
        fail("NOTION_TOKEN no esta definido en .env")
        sys.exit(1)

    client = Client(auth=token)
    try:
        # Una llamada simple para verificar que el token es valido
        result = client.users.me()
        ok(f"Token valido | Bot: {result.get('name', 'N/A')} | Tipo: {result.get('type', 'N/A')}")
    except Exception as e:
        fail(f"Token invalido o sin permisos: {e}")
        sys.exit(1)

    return client


def test_database_accessible(client: Client):
    print("\n--- Test 2: Acceso a la base de datos ---")
    db_id = os.getenv("NOTION_DB_ID")
    if not db_id:
        fail("NOTION_DB_ID no esta definido en .env")
        return

    try:
        db = client.databases.retrieve(database_id=db_id)
        title = db["title"][0]["text"]["content"] if db.get("title") else "Sin titulo"
        ok(f"Base de datos accesible: '{title}'")
        ok(f"ID: {db_id}")
        info(f"Propiedades: {list(db['properties'].keys())}")
    except Exception as e:
        fail(f"No se pudo acceder a la DB: {e}")
        info("Verifica que la integracion tenga acceso a la pagina en Notion.")


def test_write_operation():
    global TEST_PAGE_ID
    print("\n--- Test 3: Escribir operacion de prueba ---")

    logger = NotionLogger()
    try:
        # Llamamos directamente al cliente para capturar el ID de la pagina creada
        from datetime import datetime, timezone
        now   = datetime.now(timezone.utc).isoformat()
        title = f"[TEST] BUY EURUSD @ 1.08500 | {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        response = logger.client.pages.create(
            parent={"database_id": logger.db_id},
            properties={
                "Operacion":            {"title":     [{"text": {"content": title}}]},
                "Fecha":                {"date":      {"start": now}},
                "Tipo":                 {"select":    {"name": "BUY"}},
                "Par":                  {"rich_text": [{"text": {"content": "EURUSD"}}]},
                "Cantidad (Lotes)":     {"number":    0.01},
                "Precio Entrada":       {"number":    1.08500},
                "Motivo / Analisis IA": {"rich_text": [{"text": {"content": "[PRUEBA] Tendencia alcista detectada en M15. Volumen creciente."}}]},
                "Estado":               {"select":    {"name": "Cerrada"}},
                "Precio Cierre":        {"number":    1.08700},
                "Resultado (USD)":      {"number":    20.0},
            }
        )
        TEST_PAGE_ID = response["id"]
        ok(f"Operacion de prueba creada exitosamente")
        ok(f"ID de la pagina: {TEST_PAGE_ID}")
        info(f"Puedes verla en: https://notion.so/{TEST_PAGE_ID.replace('-', '')}")
    except Exception as e:
        fail(f"No se pudo escribir en Notion: {e}")


def test_read_operations():
    print("\n--- Test 4: Leer historial de operaciones ---")
    logger = NotionLogger()
    try:
        operations = logger.get_recent_operations(limit=5)
        ok(f"Se leyeron {len(operations)} operaciones recientes")
        for i, op in enumerate(operations, 1):
            info(f"  {i}. [{op['date']}] {op['type']} {op['symbol']} | Resultado: {op['result']} USD")
            info(f"     Motivo: {op['reason'][:80]}..." if len(op['reason']) > 80 else f"     Motivo: {op['reason']}")
    except Exception as e:
        fail(f"No se pudo leer el historial: {e}")


def test_delete_test_record(client: Client):
    global TEST_PAGE_ID
    print("\n--- Test 5: Limpiar registro de prueba ---")
    if not TEST_PAGE_ID:
        info("No hay registro de prueba que limpiar.")
        return

    try:
        client.pages.update(page_id=TEST_PAGE_ID, archived=True)
        ok(f"Registro de prueba eliminado (archivado): {TEST_PAGE_ID}")
    except Exception as e:
        fail(f"No se pudo eliminar el registro de prueba: {e}")
        info(f"Puedes eliminarlo manualmente desde Notion.")


if __name__ == "__main__":
    print("================================================")
    print("  TEST DE CONEXION - Notion")
    print("================================================")

    client = test_token_valid()
    test_database_accessible(client)
    test_write_operation()
    test_read_operations()
    test_delete_test_record(client)

    print("\n================================================")
    print(f"{GREEN}  Todos los tests de Notion completados.{RESET}")
    print("================================================\n")
