"""
test_notion_pinecone_integration.py
================================================
Test de integración completo: Notion + Pinecone.

Flujo que verifica este test:
  1. Crear 3 operaciones de prueba en Notion
  2. Crear las mismas 3 operaciones en Pinecone (vectorial)
  3. Leer y consultar los registros del Pinecone
     (búsqueda semántica con distintas queries)
  4. Leer el historial de Notion como comprobación cruzada
  5. Eliminar los registros de prueba de Notion (archivado)
  6. Eliminar los vectores de prueba de Pinecone

Cómo ejecutarlo:
  python tests/test_notion_pinecone_integration.py

Requisitos en .env:
  NOTION_TOKEN, NOTION_DB_ID,
  PINECONE_API_KEY, PINECONE_INDEX_NAME
================================================
"""

import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timezone
from modules.notion_logger import NotionLogger
from modules.pinecone_memory import PineconeMemory

# ── Colores para la consola ───────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):      print(f"{GREEN}  [PASS]{RESET} {msg}")
def fail(msg):    print(f"{RED}  [FAIL]{RESET} {msg}")
def info(msg):    print(f"{YELLOW}  [INFO]{RESET} {msg}")
def section(msg): print(f"\n{BOLD}{CYAN}--- {msg} ---{RESET}")

# ── Datos de prueba ───────────────────────────────────────────────────────────
TEST_OPERATIONS = [
    {
        "symbol":      "EURUSD",
        "action":      "BUY",
        "lot_size":    0.01,
        "price_open":  1.08520,
        "price_close": 1.08820,
        "result_usd":  30.0,
        "status":      "Cerrada",
        "reason":      "[TEST] Tendencia alcista en M15, cruce EMA 20/50 confirmado con volumen creciente.",
    },
    {
        "symbol":      "GBPUSD",
        "action":      "SELL",
        "lot_size":    0.01,
        "price_open":  1.27340,
        "price_close": 1.27040,
        "result_usd":  30.0,
        "status":      "Cerrada",
        "reason":      "[TEST] Rechazo de resistencia en 1.2740, divergencia bajista en RSI H1.",
    },
    {
        "symbol":      "XAUUSD",
        "action":      "BUY",
        "lot_size":    0.01,
        "price_open":  2315.50,
        "price_close": None,
        "result_usd":  None,
        "status":      "Abierta",
        "reason":      "[TEST] Soporte clave en 2310, presión compradora en apertura NY.",
    },
]

# IDs que se van acumulando para el cleanup al final
notion_page_ids: list[str] = []
pinecone_vector_ids: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# PASO 1 – Crear registros en Notion
# ─────────────────────────────────────────────────────────────────────────────
def step1_create_notion(logger: NotionLogger):
    section("PASO 1 / 6 · Crear registros en Notion")
    errors = 0

    for i, op in enumerate(TEST_OPERATIONS, 1):
        try:
            now   = datetime.now(timezone.utc).isoformat()
            title = (
                f"[TEST-{i}] {op['action']} {op['symbol']} "
                f"@ {op['price_open']} | {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            props = {
                "Operación":            {"title":     [{"text": {"content": title}}]},
                "Fecha":                {"date":      {"start": now}},
                "Tipo":                 {"select":    {"name": op["action"]}},
                "Par":                  {"rich_text": [{"text": {"content": op["symbol"]}}]},
                "Cantidad (Lotes)":     {"number":    op["lot_size"]},
                "Precio Entrada":       {"number":    op["price_open"]},
                "Motivo / Análisis IA": {"rich_text": [{"text": {"content": op["reason"]}}]},
                "Estado":               {"select":    {"name": op["status"]}},
            }
            if op["price_close"] is not None:
                props["Precio Cierre"]    = {"number": op["price_close"]}
            if op["result_usd"] is not None:
                props["Resultado (USD)"] = {"number": op["result_usd"]}

            response = logger.client.pages.create(
                parent={"database_id": logger.db_id},
                properties=props,
            )
            page_id = response["id"]
            notion_page_ids.append(page_id)
            ok(f"Notion [{i}/3] {op['action']} {op['symbol']} creado | ID: {page_id[:8]}...")
        except Exception as e:
            fail(f"Notion [{i}/3] {op['symbol']}: {e}")
            errors += 1

    if errors == 0:
        ok(f"Los 3 registros fueron creados en Notion correctamente.")
    else:
        fail(f"{errors} registros fallaron en Notion.")
    return errors == 0


# ─────────────────────────────────────────────────────────────────────────────
# PASO 2 – Crear registros en Pinecone
# ─────────────────────────────────────────────────────────────────────────────
def step2_create_pinecone(memory: PineconeMemory):
    section("PASO 2 / 6 · Crear registros en Pinecone (vectorial)")
    errors = 0

    for i, op in enumerate(TEST_OPERATIONS, 1):
        try:
            vector_id = memory.log_operation(
                symbol=op["symbol"],
                action=op["action"],
                lot_size=op["lot_size"],
                price_open=op["price_open"],
                reason=op["reason"],
                price_close=op["price_close"],
                result_usd=op["result_usd"],
                status=op["status"],
            )
            pinecone_vector_ids.append(vector_id)
            ok(f"Pinecone [{i}/3] {op['action']} {op['symbol']} guardado | ID: {vector_id}")
        except Exception as e:
            fail(f"Pinecone [{i}/3] {op['symbol']}: {e}")
            errors += 1

    if errors == 0:
        ok("Los 3 vectores fueron insertados en Pinecone correctamente.")
        info("Esperando 5s para que Pinecone indexe los registros...")
        time.sleep(5)  # Pinecone serverless tiene latencia de indexación
    else:
        fail(f"{errors} vectores fallaron en Pinecone.")
    return errors == 0


# ─────────────────────────────────────────────────────────────────────────────
# PASO 3 – Leer y consultar Pinecone
# ─────────────────────────────────────────────────────────────────────────────
def step3_read_pinecone(memory: PineconeMemory):
    section("PASO 3 / 6 · Leer registros del Pinecone (búsqueda semántica)")

    queries = [
        ("operaciones BUY con ganancia",                                    None),
        ("SELL GBPUSD resistencia RSI bajista",                             {"action": {"$eq": "SELL"}}),
        ("operación abierta oro XAUUSD soporte compra",                     {"symbol": {"$eq": "XAUUSD"}}),
        ("historial reciente trading forex resultado cerrado",               None),
    ]

    total_found = 0
    for q_text, q_filter in queries:
        try:
            results = memory.query_similar(query=q_text, top_k=3, filter_by=q_filter)
            total_found += len(results)
            ok(f"Query: '{q_text[:55]}...' → {len(results)} resultado(s)")
            for r in results:
                info(
                    f"    ▸ {r.get('action','')} {r.get('symbol','')} "
                    f"@ {r.get('price_open','')} | {r.get('status','')} "
                    f"| Score: {r.get('score','')}"
                )
        except Exception as e:
            fail(f"Query fallida '{q_text[:40]}': {e}")

    print()
    # Mostrar resumen de contexto tal como lo ve la IA
    section("  Contexto generado para la IA (get_stats_context)")
    try:
        ctx = memory.get_stats_context()
        print(ctx)
        ok("get_stats_context() ejecutado correctamente.")
    except Exception as e:
        fail(f"get_stats_context() falló: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PASO 4 – Leer historial de Notion
# ─────────────────────────────────────────────────────────────────────────────
def step4_read_notion(logger: NotionLogger):
    section("PASO 4 / 6 · Leer historial de Notion (comprobación cruzada)")
    try:
        ops = logger.get_recent_operations(limit=5)
        ok(f"Se leyeron {len(ops)} operaciones recientes de Notion.")
        for i, op in enumerate(ops, 1):
            result_str = f"{op['result']} USD" if op.get("result") else "abierta"
            info(
                f"  {i}. [{op['date']}] {op['type']} {op['symbol']} "
                f"| Resultado: {result_str}"
            )
            if op.get("reason"):
                reason_short = op["reason"][:70] + "..." if len(op["reason"]) > 70 else op["reason"]
                info(f"     Motivo: {reason_short}")
    except Exception as e:
        fail(f"Error al leer Notion: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PASO 5 – Eliminar registros de prueba en Notion
# ─────────────────────────────────────────────────────────────────────────────
def step5_delete_notion(logger: NotionLogger):
    section("PASO 5 / 6 · Eliminar registros de prueba en Notion")
    if not notion_page_ids:
        info("No hay páginas de prueba para eliminar.")
        return

    errors = 0
    for i, page_id in enumerate(notion_page_ids, 1):
        try:
            logger.client.pages.update(page_id=page_id, archived=True)
            ok(f"Notion [{i}/{len(notion_page_ids)}] archivado | ID: {page_id[:8]}...")
        except Exception as e:
            fail(f"Notion [{i}] no se pudo archivar {page_id[:8]}: {e}")
            errors += 1

    if errors == 0:
        ok("Todos los registros de Notion fueron eliminados (archivados).")
    else:
        fail(f"{errors} registros no pudieron eliminarse. Elimínalos manualmente.")
        for pid in notion_page_ids:
            info(f"  https://notion.so/{pid.replace('-', '')}")


# ─────────────────────────────────────────────────────────────────────────────
# PASO 6 – Eliminar vectores de prueba en Pinecone
# ─────────────────────────────────────────────────────────────────────────────
def step6_delete_pinecone(memory: PineconeMemory):
    section("PASO 6 / 6 · Eliminar vectores de prueba en Pinecone")
    if not pinecone_vector_ids:
        info("No hay vectores de prueba para eliminar.")
        return

    try:
        memory.index.delete(
            ids=pinecone_vector_ids,
            namespace="operations",
        )
        ok(f"Eliminados {len(pinecone_vector_ids)} vectores de Pinecone:")
        for vid in pinecone_vector_ids:
            info(f"  ▸ {vid}")
    except Exception as e:
        fail(f"Error al eliminar vectores de Pinecone: {e}")
        info("IDs pendientes de borrar manualmente:")
        for vid in pinecone_vector_ids:
            info(f"  ▸ {vid}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{BOLD}{'='*56}")
    print("  TEST DE INTEGRACIÓN — Notion + Pinecone")
    print(f"{'='*56}{RESET}")
    print(f"{YELLOW}  Operaciones de prueba: {len(TEST_OPERATIONS)}{RESET}")
    print(f"{YELLOW}  Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")

    # Inicializar clientes
    try:
        logger = NotionLogger()
        ok("NotionLogger inicializado.")
    except Exception as e:
        fail(f"No se pudo inicializar NotionLogger: {e}")
        sys.exit(1)

    try:
        memory = PineconeMemory()
        ok("PineconeMemory inicializada.")
    except Exception as e:
        fail(f"No se pudo inicializar PineconeMemory: {e}")
        sys.exit(1)

    # Ejecutar pasos en secuencia — el cleanup siempre corre aunque fallen pasos anteriores
    notion_ok  = step1_create_notion(logger)
    pinecone_ok = step2_create_pinecone(memory)

    if pinecone_ok:
        step3_read_pinecone(memory)
    else:
        info("PASO 3 omitido porque la inserción en Pinecone falló.")

    if notion_ok:
        step4_read_notion(logger)
    else:
        info("PASO 4 omitido porque la inserción en Notion falló.")

    # Cleanup siempre
    step5_delete_notion(logger)
    step6_delete_pinecone(memory)

    print(f"\n{BOLD}{'='*56}")
    print(f"{GREEN}  Test de integración completado.{RESET}")
    print(f"{BOLD}{'='*56}{RESET}\n")
