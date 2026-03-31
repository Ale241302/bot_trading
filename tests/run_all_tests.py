"""
run_all_tests.py
================================================
Ejecuta todos los tests del proyecto en orden.

Como ejecutarlo:
  python tests/run_all_tests.py
================================================
"""

import subprocess
import sys
import os

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

tests = [
    ("Notion Connection",    "tests/test_notion_connection.py"),
    ("MetaTrader 5",         "tests/test_mt5_connection.py"),
]

print(f"\n{BOLD}================================================{RESET}")
print(f"{BOLD}   EJECUTANDO TODOS LOS TESTS DEL BOT{RESET}")
print(f"{BOLD}================================================{RESET}\n")

passed = []
failed = []

for name, path in tests:
    print(f"{YELLOW}{BOLD}>>> Ejecutando: {name}{RESET}")
    result = subprocess.run([sys.executable, path], capture_output=False)
    if result.returncode == 0:
        passed.append(name)
        print(f"{GREEN}>>> {name}: OK{RESET}\n")
    else:
        failed.append(name)
        print(f"{RED}>>> {name}: FALLO{RESET}\n")

print(f"{BOLD}================================================{RESET}")
print(f"{BOLD}  RESUMEN{RESET}")
print(f"{BOLD}================================================{RESET}")
for name in passed:
    print(f"{GREEN}  [OK]    {name}{RESET}")
for name in failed:
    print(f"{RED}  [FALLO] {name}{RESET}")
print(f"{BOLD}================================================{RESET}\n")

if failed:
    sys.exit(1)
