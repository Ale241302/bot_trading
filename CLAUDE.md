# CLAUDE.md — Contexto del proyecto `bot_trading`

> Este archivo se carga automáticamente cuando trabajas con Claude Code en este repo. Lee primero esto antes de cualquier cambio.

---

## 1. Qué es este proyecto

Bot de trading automatizado para Forex (EURUSD, GBPUSD, USDJPY) que opera en **MetaTrader 5 (Pepperstone Demo)** usando **OpenAI GPT-4o** como cerebro de decisión.

- **Estrategia**: WDC (Weekly Double Compounding) — ver `strategy/prompt.md`. Filtros de 4 niveles: Noticias → Tendencia H4+H1 → Sentimiento Myfxbook → Patrón en M15 (PinBar / Envolvente).
- **Riesgo**: 5% por trade en fase Crecimiento, 3% en Consolidación, 1% en Escudo. SL fijo 8 pips / TP 16 pips (RR 1:2) para EURUSD; otros pares en `backtest/pair_config.py`.
- **Persistencia**: Notion (log humano), Pinecone (memoria vectorial para contexto histórico de la IA).
- **Backtest**: módulo `backtest/` con simulador vela-a-vela + Monte Carlo. Resultados confirman 90% duplicar / 2% ruina con sweet spot 5%.

---

## 2. Arquitectura

```
main.py                          # Loop principal (schedule cada 60s)
  ├── modules/mt5_connector.py   # Conexión MT5, velas OHLCV, posiciones, órdenes
  ├── modules/ai_analyst.py      # OpenAI GPT-4o → JSON {action, lot, sl, tp, reason}
  ├── modules/trader.py          # Ejecuta BUY/SELL/CLOSE/MODIFY/TRAILING en MT5
  ├── modules/notion_logger.py   # Crea/actualiza filas en la DB de Notion
  ├── modules/pinecone_memory.py # Upsert + query semántica (llama-text-embed-v2)
  ├── modules/trade_monitor.py   # Detecta cierres y actualiza Notion + Pinecone
  ├── modules/capital_guard.py   # Fases, riesgo dinámico, circuit breakers
  │                              # ⚡ Fuente única de constantes (RISK_PCT, MAX_*)
  ├── modules/market_context.py  # Contexto técnico (yfinance) + Myfxbook (vía MyfxbookClient)
  ├── modules/myfxbook_client.py # Cliente Myfxbook único (singleton implícito en MarketContext)
  └── modules/logging_config.py  # setup_logging() + TerseFormatter (INFO limpio, WARN+ con prefijo)

backtest/                        # Backtesting offline
  ├── data_loader.py             # Descarga histórica yfinance (M15/H1/H4)
  ├── signal_engine.py           # Réplica determinista del prompt en código
  ├── backtest_runner.py         # Loop multi-par capital compartido
  ├── monte_carlo.py             # Bootstrap N simulaciones
  ├── report.py                  # HTML con plotly
  └── pair_config.py             # Parámetros por par (sl, tp, threshold, sentiment_base)

scripts/
  ├── check_env.py               # Diagnóstico de TODAS las conexiones (MT5/Myfxbook/Notion/Pinecone)
  └── debug_myfxbook.py          # Test puntual del cliente Myfxbook

tests/                           # Unit tests + integración
  ├── test_signal_engine.py      # Unit (puro, sin red): get_trend, pin bars, evaluate_confluence
  ├── test_capital_guard.py      # Unit: should_trade, fases, CBs diario y semanal
  ├── test_trader_unit.py        # Unit: _pip_size (JPY/EURUSD), _validate_decision
  └── test_*_connection.py       # Integración (requieren credenciales)

strategy/prompt.md               # ⚠️ ÚNICA fuente de verdad del prompt
                                 # (prompt.txt eliminado por contradecir parámetros)
SECURITY.md                      # Política de credenciales y rotación de claves
```

### Flujo de un ciclo (cada `LOOP_INTERVAL_SECONDS`)

1. `mt5.connect()` (reconecta si la sesión cayó).
2. `CapitalGuard.should_trade()` decide si se opera (horario, racha SL, stop diario).
3. (filtro de noticias eliminado tras quitar jblanked — el bot no filtra eventos macro hoy).
4. `mt5.get_candles()` para M15/H1/H4 + `get_open_positions()` + `get_pending_orders()`.
5. `TradeMonitor.check_closed_trades()` reconcilia tickets cerrados → actualiza Notion + Pinecone.
6. `MarketContext.get_myfxbook_sentiment()` (cache 55s) → se pasa a `AIAnalyst.analyze()` para evitar doble llamada.
7. `AIAnalyst.analyze()` envía el contexto completo a GPT-4o con retry exponencial → JSON con `action`.
8. Si `action != HOLD`: `Trader.execute()` lanza la orden y se loggea en Notion + Pinecone + `active_trades.json`.

---

## 3. Convenciones del código

- **Idioma**: comentarios y mensajes de log en **español**. Variables y funciones en inglés (Python convention).
- **Tipo de cuotas**: encoding UTF-8 en todos los archivos. Algunos comments tienen tildes/eñes — respetarlas.
- **Tiempo**: TODA lógica usa **UTC estricto** (`datetime.now(timezone.utc)`). La hora local solo se imprime como referencia visual.
- **Pips**: hardcoded `* 10` en `trader.py` para convertir pips → puntos. **Esto solo es correcto para pares de 5 dígitos (EURUSD, GBPUSD).** Para JPY (3 dígitos), oro o índices, el factor cambia. **Evitar tocar `trader.py` sin entender este punto** — es una tech-debt conocida.
- **Magic numbers MT5**: `234000` (mercado), `234001` (pendiente), `234002` (close), `234003` (close partial). Útil para identificar trades del bot vs manuales.
- **Estado local**: `active_trades.json` (TradeMonitor) y `capital_state.json` (CapitalGuard). Ambos están en `.gitignore`. Escrituras **atómicas** (`os.replace` tras `fsync`) — seguro ante crash mid-write.
- **Logging**: todos los módulos en `modules/` usan `logging.getLogger(__name__)`. `setup_logging()` se invoca al inicio de `main.py`. INFO sale como mensaje pelado (preserva emojis), WARNING+ con prefijo. Variables de entorno: `LOG_LEVEL` (default `INFO`) y `LOG_FILE` (opcional, rotación 5MB×5). Los scripts CLI (`scripts/check_env.py`, `backtest/*`, `tests/*`) mantienen `print()` por ser herramientas de línea.

---

## 4. Servicios externos activos

| Servicio | Variable env | Para qué |
|----------|--------------|----------|
| MetaTrader 5 | `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` | Datos de mercado + ejecución de órdenes |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | Decisión BUY/SELL/HOLD (default `gpt-4o`) |
| Notion | `NOTION_TOKEN`, `NOTION_DB_ID` | Log humano de operaciones |
| Pinecone | `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` | Memoria vectorial (índice integrado `llama-text-embed-v2`, sin OpenAI embeddings) |
| Myfxbook | `MYFXBOOK_EMAIL`, `MYFXBOOK_PASSWORD` | Sentimiento retail (community outlook) |
| yfinance | (sin key) | Velas técnicas EURUSD para `MarketContext` y backtest |

### ❌ Servicios desconectados (no usar — fueron removidos)

- **Finnhub** (`FINNHUB_API_KEY`)
- **jblanked** (`JBLANKED_API_KEY`) — calendario de noticias
- **Alpha Vantage** (`ALPHAVANTAGE_API_KEY`) — news sentiment

Si reactivas alguno, recuerda agregar la dependencia en `requirements.txt`, los bloques en `.env.example`, el código en `market_context.py` y la sección de diagnóstico en `scripts/check_env.py`.

---

## 5. Cómo ejecutar

### Bot completo (Windows + MT5 instalado)

```powershell
pip install -r requirements-windows.txt
python main.py
```

**Pre-requisito**: el terminal MT5 debe tener **AutoTrading activado** (botón verde en la barra). Si `terminal_info.trade_allowed = False`, ningún `order_send()` será aceptado.

### Solo diagnóstico de conexiones

```powershell
python scripts/check_env.py
```

Verifica MT5, Myfxbook, Notion y Pinecone sin abrir órdenes ni escribir datos.

### Backtest

```powershell
pip install -r requirements-backtest.txt
python -m backtest.run_backtest --years 2 --sims 1000 --capital 50
python -m backtest.run_backtest --pairs EURUSD,GBPUSD --risk-mode 0.05
```

Genera reporte HTML interactivo en `backtest/output/`.

### Docker (Linux/Mac — sin MT5)

Solo sirve para correr tests de Notion/Pinecone, **no el bot**.

```bash
docker compose up --build
```

---

## 6. Riesgos y comportamientos no obvios

1. **No hay filtro de noticias**: tras remover jblanked, el bot ya no bloquea por eventos macro HIGH. Si quieres reactivar, integra un nuevo proveedor en `MarketContext` y vuelve a cablear el filtro en `main.py` (PASO 2).
2. **MT5 + AutoTrading**: el flag `terminal_info.trade_allowed` se controla **manualmente** en el terminal. No hay forma de activarlo desde Python.
3. **Una sola llamada a Myfxbook por ciclo** (post-P1): `MarketContext` consulta vía `MyfxbookClient` con cache de 55s, y `main.py` pasa el resultado a `AIAnalyst.analyze(myfxbook_sentiment=...)`.
4. **`mt5.connect()` antes de `mt5.is_connected()`**: el patrón actual en `main.py` reconecta cada ciclo si la sesión cayó. Robusto pero verboso.
5. **`active_trades.json` se borra solo después de cierre**: si MT5 reporta el cierre pero `history_deals_get` aún no tiene el deal, `TradeMonitor` lo deja para el siguiente ciclo. No marca como cerrado prematuramente.
6. **Capital de trabajo**: si `CAPITAL_TRABAJO=50` y el balance demo es $50000, el bot opera contra $50, no contra el balance real. Útil para simular cuenta pequeña en demo grande.
7. **Retry OpenAI** (post-P0): `AIAnalyst._call_openai_with_retry` reintenta 3 veces con backoff exponencial 2s → 4s → 8s + jitter. Solo reintenta errores transientes (red, timeout, rate-limit, 5xx). 4xx fatales no se reintentan.
8. **Pip-factor en Trader** (post-P0): `Trader._pip_size(info)` ajusta el factor según `info.digits`. **USDJPY (3 dígitos) ya calcula SL/TP correctamente.** Antes era `* 10 * info.point` hardcoded — bug.
9. **Constantes de gestión centralizadas** (post-P1): `RISK_PCT`, `MAX_TRADES_*`, `MAX_*_SL`, `TRADE_HOUR_*`, `SL_PIPS`, `TP_PIPS` viven en `modules/capital_guard.py` (nivel módulo). `backtest/backtest_runner.py` las importa.
10. **Validación de respuesta IA** (post-auditoría): `AIAnalyst._validate_decision()` filtra acciones inválidas, normaliza `lot/sl_pips/tp_pips` y degrada a `HOLD` si hay problemas. Nunca crashea por JSON malformado.
11. **Phase context inyectado al prompt** (post-auditoría): `main.py` calcula la fase + lote sugerido y lo pasa explícitamente a `AIAnalyst.analyze(phase_context=...)`. Evita que la IA "olvide" el riesgo dinámico.
12. **CB semanal en CapitalGuard** (post-auditoría): `should_trade()` ahora bloquea con 5 SL en últimos 7 días. Antes solo aplicaba el CB diario (3 SL consecutivos).
13. **Sentimiento sintético en backtest**: `sentiment_for_backtest()` usa distribución normal por par. Los resultados son **sobreoptimistas** vs el sentimiento real. Validar siempre con paper-trading antes de operar live.
14. **Naming corregido**: `nivel_2_tendencia` y `nivel_3_sentimiento` ahora coinciden con el orden del `prompt.md` (antes estaban cruzados — bug latente).
15. **Atomic writes** en `active_trades.json` y `capital_state.json`. Si el archivo se corrompe, `TradeMonitor` lo respalda como `.corrupt` y arranca con estado vacío en lugar de tragar el error en silencio.

---

## 7. Estado actual y próximos pasos sugeridos

- ✅ Multi-par funcional en backtest (EURUSD/GBPUSD/USDJPY)
- ✅ Circuit breakers diario (3 SL) y semanal (5 SL en 7 días) implementados
- ✅ Diagnóstico unificado en `scripts/check_env.py`
- ✅ Backtest IA-driven (`backtest/run_ai_backtest.py`) con Monte Carlo integrado
- ✅ Comparador de niveles de riesgo (`backtest/compare_risk.py`)
- 🔧 Tech-debt visible: hardcode de pips en `trader.py`, doble llamada a Myfxbook, `print()` en lugar de logging estructurado, sin atomic writes
- 🧪 Cobertura de tests: solo integración por servicio. Sin unit tests de `trader`, `capital_guard`, `signal_engine` — el backtest hace de regresión

### 7.1 Validación de estrategia (mayo 2026)

Backtest AI-driven con `gpt-4o-mini`, riesgo 5%, sin sentimiento externo, 60 días yfinance:
- 🟢 **VIABLE**: PF 2.86, WR 57.1%, DD 14.8%, Capital $50→$78.24 (+56%)
- Monte Carlo 1000 sims: **Ruina 0%**, Duplican 20.3%, Mediana $78.79
- Comportamiento IA: 77.8% confirma técnico, 22.2% override→HOLD (la IA filtra)

Camino agresivo (riesgo 10%) **rechazado** tras run real: la IA decide diferente con capital distinto y alucina límites ("11 trades hoy" cuando solo había 7). PF 0.87, ruina 31.9%. Lección: el MC bootstrap reescalado **NO** es válido cuando hay un modelo discrecional en el medio.

### 7.2 Plan de paper-trading demo

1. **Ejecutar `python scripts/check_env.py`** y validar que MT5/OpenAI/Notion/Pinecone/Myfxbook estén OK.
2. **Activar AutoTrading** en MT5 (botón verde).
3. **Arrancar `python main.py`** con `CAPITAL_TRABAJO=50` en `.env`.
4. **Observar 4 semanas mínimo** en demo Pepperstone. Métricas a vigilar:
   - WR ≥ 48% (alarma si <42% en semana 2).
   - DD máximo ≤ 25% (pausar bot si supera 30% en cualquier momento).
   - PF ≥ 1.5 sobre 20+ trades (alarma si <1.2).
   - Si la IA hace override→HOLD <10%, revisar prompt — está siendo sello de goma.
5. **Si pasa demo 4 semanas** con esos umbrales: pasar a live con $50 reales.
6. **Si falla**: revisar Notion para ver qué tipo de trades fallaron y endurecer filtros.

---

## 8. Cómo trabajar con Claude Code en este repo

- Antes de modificar `trader.py`, `capital_guard.py` o `signal_engine.py` lee `instrucciones.md` (en este repo).
- Después de cualquier cambio en lógica de trading, corre el backtest y verifica que las métricas no regresen (WR ≥ 48%, ruina ≤ 5%, DD ≤ 20%).
- No removas servicios externos sin actualizar a la vez: `requirements.txt`, `.env.example`, código que los usa, y `scripts/check_env.py`.
- Mantén los commits enfocados: refactor, fix y feature por separado.
- El proyecto opera con dinero real (eventualmente). **Nunca** cambies parámetros de gestión de riesgo (`SL_PIPS`, `TP_PIPS`, `RIESGO_*`, `MAX_DAILY_LOSS_PCT`, `MAX_CONSECUTIVE_SL`) sin justificarlo con backtest + Monte Carlo.
