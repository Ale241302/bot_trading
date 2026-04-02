# ─────────────────────────────────────────────────────────────
# Dockerfile
# Imagen Linux para correr tests, Notion y Pinecone.
# MetaTrader5 NO se instala aquí (solo existe para Windows).
# El bot completo (main.py) requiere Windows + MT5.
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema
RUN apt-get update && apt-get install -y \
    wget curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python (sin MT5)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

# Por defecto corre el test de integración.
# Para el bot completo usar Windows con requirements-windows.txt
CMD ["python", "tests/test_notion_pinecone_integration.py"]
