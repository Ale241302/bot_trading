# Trading Bot con IA - Pepperstone + OpenAI + Notion

Bot de trading automatizado que usa **OpenAI GPT-4o** para analizar el mercado Forex,
ejecutar operaciones en **MetaTrader 5 (Pepperstone)** y registrar cada operacion en **Notion**.

---

## Estructura del proyecto

```
bot_trading/
|
|-- main.py                  # Punto de entrada. Arranca el bot.
|
|-- modules/
|   |-- mt5_connector.py     # Conexion y datos de MetaTrader 5
|   |-- ai_analyst.py        # Analisis con OpenAI (decision BUY/SELL/HOLD)
|   |-- notion_logger.py     # Registro y lectura del log en Notion
|   `-- trader.py            # Ejecucion de ordenes en MT5
|
|-- strategy/
|   `-- prompt.txt           # AQUI configuras la estrategia de la IA
|
|-- .env                     # Credenciales y configuracion (NO subir a git)
|-- .env.example             # Plantilla de configuracion
|-- requirements.txt         # Dependencias Python
|-- Dockerfile               # Imagen Docker del bot
`-- docker-compose.yml       # Orquestacion Docker local
```

---

## Configuracion rapida

### 1. Clonar el repositorio
```bash
git clone https://github.com/Ale241302/bot_trading.git
cd bot_trading
```

### 2. Copiar y completar el .env
```bash
cp .env.example .env
```

Edita el archivo `.env` con tus datos reales:

```env
# MetaTrader 5 - Pepperstone Demo
MT5_LOGIN=61502806
MT5_PASSWORD=tu_password
MT5_SERVER=mt5-demo01.pepperstone.com

# Notion
NOTION_TOKEN=ntn_xxxxx
NOTION_DB_ID=3d9d924b8faf478f879973a3475a3510

# OpenAI
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4o

# Config del bot
TRADING_SYMBOL=EURUSD
LOT_SIZE=0.01
LOOP_INTERVAL_SECONDS=60
CANDLES_HISTORY=50
```

### 3. Configurar la estrategia de la IA
Edita `strategy/prompt.txt` para definir como debe operar la IA.
No necesitas tocar codigo, solo el texto del prompt.

---

## Como ejecutar

### Opcion A - Docker (recomendado)
```bash
docker-compose up --build
```

### Opcion B - Python directo (Windows con MT5 instalado)
```bash
pip install -r requirements.txt
python main.py
```

> NOTA: MetaTrader5 para Python solo funciona en Windows nativamente.
> Si usas Linux/Mac, ejecuta en Windows y monta los modulos de Notion/OpenAI en otro servicio.

---

## Flujo del bot

```
cada N segundos
      |
      v
[MT5] Obtener velas OHLCV
      |
      v
[Notion] Leer historial de operaciones recientes
      |
      v
[OpenAI] Analizar mercado + historial => BUY / SELL / HOLD
      |
   BUY o SELL
      |
      v
[MT5] Ejecutar orden con SL y TP
      |
      v
[Notion] Registrar operacion en el log
```

---

## Log en Notion

Cada operacion se registra automaticamente con:

| Campo | Ejemplo |
|---|---|
| Operacion | BUY EURUSD @ 1.08542 |
| Fecha | 2026-03-30 21:30 |
| Tipo | BUY / SELL |
| Par | EURUSD |
| Cantidad (Lotes) | 0.01 |
| Precio Entrada | 1.08542 |
| Precio Cierre | 1.08742 |
| Resultado (USD) | +20.00 |
| Motivo / Analisis IA | Tendencia alcista confirmada... |
| Estado | Abierta / Cerrada / Cancelada |

---

## Personalizar la estrategia

Solo edita `strategy/prompt.txt`. Ejemplos de instrucciones:

- "Opera solo en sesion de Londres (8am-12pm UTC)"
- "Evita operar los viernes"
- "Usa una estrategia de scalping agresivo en M5"
- "Se conservador, prefiere HOLD ante la duda"
- "No abras mas de 2 operaciones al dia"

---

## Seguridad

- El archivo `.env` esta en `.gitignore` y NUNCA se sube a GitHub
- Usa siempre la cuenta Demo para pruebas
- Revisa el log en Notion antes de conectar a cuenta real

---

## Dependencias

| Libreria | Uso |
|---|---|
| MetaTrader5 | Conexion con MT5 |
| openai | Analisis con GPT-4o |
| notion-client | Log en Notion |
| pandas | Procesamiento de velas |
| python-dotenv | Variables de entorno |
| schedule | Loop de tiempo |
