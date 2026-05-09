# instrucciones.md — Guía para trabajar este repo con Claude Opus 4.7

> Lee esto + `CLAUDE.md` antes de pedirle a Opus 4.7 cualquier cambio sobre el código. Está dividido en: cómo dirigir a Opus 4.7 · convenciones · auditoría función por función · mejoras priorizadas · plantillas de prompts.

---

## 1. Cómo dirigir a Opus 4.7 en este proyecto

Opus 4.7 es un modelo capaz pero "obediente": si le pides un cambio mal acotado, lo ejecuta sin cuestionarlo. Para evitar que rompa la estrategia o introduzca regresiones de riesgo:

### Reglas de oro al pedir cambios

1. **Define el alcance en una frase**. "Refactor `trader.py` para soportar oro" no es alcance — sí lo es: "modifica `_execute_market` y `_execute_pending` para que el factor pips→puntos venga de `info.digits` en vez de hardcoded `* 10`, sin tocar la firma pública de `execute()`".
2. **Cita archivo y líneas**. `[trader.py:50](modules/trader.py#L50)` — no "en el trader". Esto evita que toque código colateral.
3. **Especifica qué NO debe tocar**. Ej.: "No cambies `capital_guard.py` ni `strategy/prompt.md` en este PR".
4. **Pide verificación antes de claim "listo"**. Que corra `python scripts/check_env.py` o el backtest según el alcance, y que pegue la salida.
5. **Para cambios en lógica de riesgo**: exige justificación con backtest + Monte Carlo. Nunca aceptes "creo que funciona".
6. **Para cambios en `prompt.md`**: revisa diff a mano. La IA ejecuta lo que dice ese archivo.

### Anti-patrones a vetar explícitamente

- ❌ "Mejora el código" → demasiado abierto. Pedir refactor concreto con criterio de aceptación.
- ❌ Nuevas dependencias sin justificación. Cada `pip install` es deuda.
- ❌ Comentarios largos tipo "// removed X" o docstrings de 30 líneas. Solo el "porqué" no obvio.
- ❌ "Por compatibilidad" cuando no hay caller que lo justifique. Borrar y listo.
- ❌ Try/except amplios que tragan errores. Solo capturar lo que se sabe manejar.

### Patrón de prompt recomendado

```
Contexto: [archivo + líneas + qué hace hoy].
Cambio: [una frase con verbo concreto].
Restricción: [qué NO debe tocar].
Verificación: [comando que prueba que funciona + métrica esperada].
```

---

## 2. Convenciones de este repo

### Estilo

- **Idioma**: comentarios y mensajes en español. Identificadores en inglés.
- **Type hints** donde aporten (firmas públicas, dicts complejos). No obsesionarse en privadas triviales.
- **f-strings** siempre, no `%` ni `.format()`.
- **`pathlib.Path`** > `os.path.join` cuando se manipulan rutas.
- **`datetime.now(timezone.utc)`** siempre. **Nunca** `datetime.now()` para lógica.
- **Imports al tope, agrupados**: stdlib, third-party, locales (con línea en blanco entre grupos).

### Documentación

- **Docstring solo si el "qué" no es obvio.** Para una función con nombre `_calc_rsi(closes, period)`, el docstring estorba.
- **Comentar el "por qué", no el "qué"**: el código ya dice qué hace.
- **Ejemplos** en docstring solo cuando el formato de entrada/salida es no trivial (ver `myfxbook_client.py:get_sentiment`).
- **Referencias a tickets/incidentes** en commit messages, no en código (los comentarios no envejecen bien).

### Logging y errores

- Migrar gradualmente de `print()` a `logging.getLogger(__name__)`. `myfxbook_client.py` y `data_loader.py` ya lo usan — copiar ese patrón.
- Niveles: `INFO` para eventos normales (orden ejecutada), `WARNING` para condiciones recuperables (Myfxbook 1ª sesión inválida), `ERROR` para fallos del ciclo.
- **No** capturar `Exception` genérico salvo en frontera externa (HTTP request, subprocess). Internamente, dejar que el error suba.

### Tests

- Cualquier nueva función pura (sin I/O) debe tener un test. Ej.: `signal_engine.detect_pattern`, `capital_guard._consecutive_sl_today`.
- Para I/O usar el patrón de `tests/test_*.py`: integration test con servicio real, marcado opcional (skip si no hay env vars).
- El **backtest sirve como regresión de la estrategia**. Cualquier cambio en `signal_engine.py` o `pair_config.py` requiere:
  ```bash
  python -m backtest.run_backtest --years 2 --sims 1000
  ```
  con WR, PF, ruina y DD comparados contra el baseline (commit `be7601d` v5).

---

## 3. Auditoría función por función

Severidad: 🔴 crítico · 🟠 importante · 🟡 mejora · 🔵 nota

### `main.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `run_bot()` global | 🟠 | Todos los servicios son globales (`mt5`, `ai`, `notion`, ...). Difícil de testear y de inyectar mocks. **Mejora**: encapsular en clase `TradingBot` con DI. |
| L53 `if not mt5.connect()` | 🟡 | Sin diferencia entre "conectó ahora" y "ya estaba conectado". Es ruido en logs. |
| L63 `os.getenv("CAPITAL_TRABAJO", 50)` | 🟡 | Variable no documentada en `.env.example`. Añadirla con comentario. |
| L107-122 contexto IA | 🟡 | Concatena 4 fuentes (history, pinecone, capital, market). Si una falla (timeout Notion), el ciclo entero se detiene. **Mejora**: cada fuente con timeout y fallback. |

### `modules/mt5_connector.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `__init__` | ✅ ARREGLADO | Valida `MT5_LOGIN/PASSWORD/SERVER` y lanza `RuntimeError` con mensaje claro si faltan o `MT5_LOGIN` no es numérico. |
| `connect()` | 🟠 | Nunca llama `mt5.shutdown()` si `login` falla pero `initialize` tuvo éxito. Deja sesión zombie. |
| `is_connected()` L22-33 | 🟢 | Correcto y conservador. |
| `get_candles()` L66-78 | 🟡 | Si `rates` viene con menos de `count`, no avisa. Para timeframes altos en mercados poco líquidos, puede dar señales falsas. |
| `get_open_positions()` L92-95 | 🟢 | OK. |
| `get_pending_orders()` L97-124 | 🟡 | Mapea solo 4 tipos pendientes. Si broker añade `_LIMIT_AT_PRICE` o stop-limits, devuelve `TYPE_X` en vez de tipo legible. |

### `modules/trader.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `_execute_market` | ✅ ARREGLADO | Usa `_pip_size(info)` que devuelve `point*10` para 5/3 dígitos y `point*1` para 4/2 dígitos. **USDJPY OK**. |
| `_execute_market` | ✅ ARREGLADO | `_send(req)` envuelve `mt5.order_send`, loguea `last_error()` si devuelve `None`. Todos los chequeos son `if res is None or res.retcode != ...`. |
| `_execute_pending` | 🟡 | Auto-corrige el precio si está fuera de rango usando `MIN_DIST_PIPS` y `REPRICE_PIPS` (constantes de clase). **Falta**: loggear el reajuste como WARNING para que la IA lo sepa. |
| `_execute_close` | 🟠 | Si `mt5.positions_get(ticket=)` retorna lista vacía (ticket ya cerrado), retorna None sin avisar. Trade-monitor podría seguir esperando. |
| `_execute_modify` y `_execute_trailing_stop` | ✅ ARREGLADO | Mismo `_pip_size`. |
| Constantes mágicas | ✅ ARREGLADO | `DEVIATION`, `MAGIC_*`, `TRAILING_PIPS`, `MIN_DIST_PIPS`, `REPRICE_PIPS` como constantes de clase. |

### `modules/ai_analyst.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `__init__` | ✅ ARREGLADO | Valida que `strategy/prompt.md` exista antes de abrirlo (mensaje claro si falta). |
| `__init__` | ✅ ARREGLADO | Ya no instancia `MyfxbookClient`. `analyze()` recibe `myfxbook_sentiment` desde `main.py` (cache de `MarketContext`). |
| `analyze` | ✅ ARREGLADO | `_call_openai_with_retry` con 3 intentos y backoff exponencial 2s → 4s → 8s + jitter. Solo reintenta errores transientes. |
| `analyze` JSON inválido | ✅ ARREGLADO | Ahora se loggea con `logger.error` (con nivel WARNING/ERROR — visible). |
| Sin caché de prompt | 🟡 | Cada llamada manda los ~2KB del system prompt. **Fix**: usar `prompt caching` si OpenAI lo soporta (`prompt_cache_key` en algunos modelos). |

### `modules/notion_logger.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `log_operation` L19-54 | 🟢 | OK. Retorna page_id correctamente. |
| `update_operation` L56-70 | 🟡 | `try/except Exception` traga errores. Si la page no existe, no se sabe. **Fix**: dejar que suba o logear con stack. |
| `get_recent_operations` L73-94 | 🟡 | Manejo defensivo `if props["X"]["Y"] else default` repetido. **Fix**: extraer `_safe_prop(props, key, default)` helper. |
| Sin paginación | 🔵 | Para `limit=10` no aplica. Si pides más de 100, Notion paginiará automáticamente. |

### `modules/pinecone_memory.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `__init__` L34-43 | 🟡 | `print()` en constructor. Side-effect que ensucia tests. |
| `_operation_to_text` L47-65 | 🟢 | Bien estructurado. |
| `log_operation` L69-136 | 🟡 | Duplica el dict de campos en `metadata` y en `operation`. **Fix**: derivar metadata desde operation. |
| `query_similar` L140-170 | 🟢 | OK. |
| `get_operations_by_symbol` L172-180 | 🟡 | Query string artificial ("operación de trading reciente BUY SELL ..."). **Fix**: usar solo el filtro `$eq` + un query mínimo. |
| `update_operation` L202-217 | 🔵 | "Actualizar" en realidad es upsert con mismo ID. Bien para Pinecone serverless. |

### `modules/trade_monitor.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `_save_active_trades` | ✅ ARREGLADO | Write atómico: `tmp` → `flush` → `fsync` → `os.replace`. |
| `_load_active_trades` L12-20 | 🟡 | `except Exception: self.active_trades = {}` traga el error y descarta el archivo corrupto sin alertar. **Fix**: log + backup del archivo corrupto. |
| `check_closed_trades` L37-83 | 🟢 | Buena lógica de "no marcar cerrado hasta que `history_deals_get` devuelva deals". |
| `check_closed_trades` L49 | 🟡 | `mt5.history_deals_get(position=ticket)` puede tardar segundos. Cada ciclo recorre todos los activos. **Fix**: cachear los ya verificados o consultar batch. |

### `modules/capital_guard.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `_save_state` | ✅ ARREGLADO | Write atómico (mismo patrón que `trade_monitor`). |
| `_load_state` L64-79 | 🟡 | Filtro >7 días bien. Pero `pnl_day()` depende de que las ops del día estén en `_operations`. Si el filtro borra una op de hoy por bug de TZ, el cálculo de fase se rompe. |
| `should_trade` L126-149 | 🟢 | Lógica clara y bien comentada. |
| `_consecutive_sl_today` L104-114 | 🟢 | Correcto. |
| `get_phase` L151-169 | 🟡 | Hardcoded `0.20` (objetivo diario) y `0.50` (umbral consolidación). Mover a constantes de clase. |
| `status_text` L171-208 | 🔵 | Función larga pero solo formato. OK. |

### `modules/market_context.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `_mfx_get_session` | ✅ ELIMINADO | Reemplazado por `MyfxbookClient` inyectado en el constructor. |
| `get_myfxbook_sentiment` | ✅ ARREGLADO | Usa `MyfxbookClient.get_sentiment()` y formatea el resultado. Cache key por símbolo. |
| `should_hold_news` | ✅ ELIMINADO | Stub removido junto con su llamada en `main.py`. |
| `get_technical_signal` | 🟢 | Cache de 55s razonable. yfinance fallbacks bien manejados. |
| `_calc_rsi` / `_calc_macd` | 🟢 | Implementaciones correctas (Wilder y EMA12/26/Signal9). |

### `modules/myfxbook_client.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `_login` L45-83 | 🟠 | Email + password viajan como query params (`?email=...&password=...`) → quedan en logs de proxy / access-log. La API de Myfxbook lo exige así, no hay alternativa. **Mitigación**: rotar password si el log de algún intermediario se filtra. |
| `get_sentiment` L110-172 | 🟢 | Lógica de 1 reintento con renovación de sesión bien hecha. |
| `_normalize_symbol` L96-108 | 🟡 | Solo soporta pares de 6 letras. XAUUSD (5+3) o índices fallarían. |

### `backtest/signal_engine.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `evaluate_confluence` L211-344 | 🟢 | Réplica fiel del prompt. Bien estructurado. |
| `detect_pattern` L165-183 | 🟢 | OK. |
| `_is_pin_bar_*` L97-136 | 🟡 | Magic numbers (`0.55`, `0.35`, `0.25`). **Fix**: constantes de módulo (`PIN_LOWER_WICK_MIN = 0.55`...). |
| `simulate_sentiment` L190-200 | 🟢 | Distribución calibrada por par bien parametrizada. |

### `backtest/backtest_runner.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `run_backtest` | 🟡 | Función de 350 líneas. **Fix**: extraer "evaluar trade", "actualizar circuit breakers", "registrar trade" en helpers. |
| Simulación SL/TP intra-vela | 🟢 | Lógica razonable. |
| `_get_phase` | 🟢 | Coincide con `capital_guard` en spirit. |
| Constantes globales | ✅ ARREGLADO | Importadas desde `modules/capital_guard.py` (`RISK_PCT`, `MAX_TRADES_*`, `MAX_*_SL`, `TRADE_HOUR_*`). |

### `backtest/data_loader.py`

| Función / línea | Severidad | Hallazgo |
|---|---|---|
| `download_data` L37-130 | 🟢 | Manejo de chunks de 55 días bien. |
| L53 `datetime.utcnow()` | 🟡 | Deprecated en Python 3.12+. **Fix**: `datetime.now(timezone.utc)`. |

### `scripts/check_env.py` y `scripts/debug_myfxbook.py`

| Función | Severidad | Hallazgo |
|---|---|---|
| `check_env.py` | 🟢 | Tras los cambios recientes cubre MT5/Myfxbook/Notion/Pinecone. |
| Sin test de OpenAI | 🟡 | Solo valida key presente. **Fix**: añadir un `chat.completions.create` mínimo (~5 tokens). |

---

## 4. Mejoras priorizadas (lo que pediría a Opus 4.7)

### P0 — ✅ COMPLETADO

1. ✅ **Atomic writes** en `trade_monitor.py` y `capital_guard.py` (`os.replace` tras `fsync`).
2. ✅ **Fix pip-factor en `trader.py`** vía `Trader._pip_size(info)`.
3. ✅ **Validación de env vars MT5** en `mt5_connector.__init__`.
4. ✅ **Retry/backoff en OpenAI** (`AIAnalyst._call_openai_with_retry`, 3 intentos, 2s → 4s → 8s + jitter).
5. ✅ **`res is None` check** en `Trader._send()` (wrapper de `mt5.order_send`).

### P1 — ✅ COMPLETADO

6. ✅ **Duplicación Myfxbook eliminada**: `MarketContext` usa `MyfxbookClient` y `AIAnalyst` recibe `myfxbook_sentiment` ya leído desde `main.py`. Una sola llamada por ciclo.
7. ✅ **Constantes de gestión centralizadas** en `modules/capital_guard.py` (nivel módulo). `backtest/backtest_runner.py` las importa.
8. ✅ **Stub `should_hold_news()` eliminado**: ya no existe método ni llamada en `main.py`. Si se reactiva un calendario de noticias, hay que volver a cablearlo explícitamente (ver CLAUDE.md §6).
9. ✅ **Logging estructurado** con `modules/logging_config.py`. `TerseFormatter` deja INFO sin prefijo (preserva emojis del bot) y WARNING+ con `[LEVEL módulo] HH:MM:SS`. Vars: `LOG_LEVEL`, `LOG_FILE` (rotación 5MB×5). Cero `print()` en `modules/`. Scripts CLI mantienen `print()` por ser herramientas de línea.

### Auditoría profunda — ✅ COMPLETADO (Críticos C1-C7 + Altos H1-H8)

| ID | Hallazgo | Solución |
|----|----------|----------|
| **C1** | `mt5_connector.connect()` dejaba sesión zombie tras login fallido | `mt5.shutdown()` en ambos paths de fallo |
| **C2** | `prompt.txt` y `prompt.md` contradictorios | `prompt.txt` eliminado; `prompt.md` es fuente única |
| **C3** | Drift prompt↔código en lote/SL dinámicos | `main.py` calcula `phase_context` y lo inyecta a `AIAnalyst.analyze(...)` |
| **C4** | Credenciales en plaintext sin política | `SECURITY.md` (rotación, 2FA, auditoría) + `.gitignore` reforzado |
| **C5** | `nivel_2_sentimiento`/`nivel_3_tendencia` cruzados | Renombrados a `nivel_2_tendencia` / `nivel_3_sentimiento` (coincide con prompt) |
| **C6** | Sin unit tests de funciones puras | `test_signal_engine.py` (13), `test_capital_guard.py` (12), `test_trader_unit.py` (12) — **37 tests** |
| **C7** | Sentimiento sintético en backtest | Banner `⚠️ SOBREOPTIMISTA` al inicio del backtest |
| **H1** | Ticket fantasma en `_execute_close` | `logger.warning` cuando `positions_get(ticket=)` viene vacío |
| **H2** | Reprice sin log en `_execute_pending` | `logger.warning(precio original → precio reajustado)` |
| **H3** | `should_trade` no chequeaba `MAX_SL_WEEK` | Añadido CB semanal con `_sl_count_last_7_days()` + test |
| **H4** | Sin schema validation del JSON IA | `_validate_decision()` con `VALID_ACTIONS` y degradación a HOLD |
| **H5** | `notion.update_operation` tragaba errores | `logger.exception` con stacktrace completo |
| **H6** | Lookback fijo H1/H4 ≠ "últimos N días" | Documentado en `H1_LOOKBACK`/`H4_LOOKBACK` |
| **H7** | `run_all_tests` solo listaba 2/4 tests | Añadidos los 3 unit tests + los 2 integration faltantes |
| **H8** | Versiones flotantes `>=` | `requirements.txt` y `requirements-backtest.txt` pineadas a `==` |

### Mejoras 🟡 — ✅ COMPLETADO

- `_load_active_trades` ya no traga JSON corrupto: backup a `.corrupt` + log error.
- Hardcoded `0.20`/`0.50` en `get_phase` → `DAILY_TARGET_PCT` / `CONSOLIDATION_PROGRESS`.
- `datetime.utcnow()` reemplazado por `datetime.now(timezone.utc)`.
- Magic numbers en pin bars → `PIN_DOMINANT_WICK_MIN` / `PIN_BODY_MAX` / `PIN_OPPOSITE_WICK_MAX` / `PIN_CLOSE_IN_FAVOR`.
- `LOT_SIZE` removido de `.env.example` (nunca se leía desde código).

### P2 — Calidad de vida

9. **Test de OpenAI** en `check_env.py` (5-token call).
10. **Helper `_safe_prop` en `notion_logger.py`** para los `if props[X][Y] else default` repetidos.
11. **Dividir `backtest_runner.run_backtest`** en helpers (su tamaño actual hace que cualquier cambio sea miedoso).
12. **Añadir `CAPITAL_TRABAJO` a `.env.example`** con comentario.

### P3 — Mejoras estratégicas (requieren backtest antes de mergear)

13. Cache de prompt OpenAI (ahorro 30-50% en tokens del system prompt).
14. Reactivar calendario de noticias con un proveedor robusto (TradingEconomics, Forex Factory scrape, o pagar jblanked) — pero solo si `should_hold_news` se valida con backtest.
15. Considerar trailing dinámico basado en ATR en lugar de pips fijos.

---

## 5. Plantillas de prompts útiles

### Para una corrección puntual
```
En [archivo:línea], la función X hace Y, pero debería hacer Z porque [razón].
Cambia solo esa función. No toques [otros archivos].
Después de aplicar el cambio, corre [comando] y muéstrame la salida.
```

### Para un refactor con preservación de comportamiento
```
Refactor: extraer [bloque X] de [archivo:línea] a una función `_helper()`.
La firma pública de [función pública] no debe cambiar.
Verificación: el output de `python -m backtest.run_backtest --years 1 --sims 100`
debe ser bit-idéntico antes y después (mismo seed=42).
```

### Para añadir un servicio externo
```
Añade soporte para [servicio X].
1. Añade la dependencia en requirements.txt con versión pinned.
2. Crea modules/x_client.py siguiendo el patrón de myfxbook_client.py
   (logging, _ensure_session, retry de 1 intento).
3. Añade el bloque correspondiente en .env.example.
4. Añade una sección de diagnóstico en scripts/check_env.py.
5. NO lo cables todavía a main.py — primero quiero revisar el cliente solo.
```

### Para tocar lógica de riesgo
```
PROHIBIDO mergear sin backtest + Monte Carlo comparado contra commit be7601d.
Corre: python -m backtest.run_backtest --years 2 --sims 1000 --seed 42
Pega aquí la tabla con: WR, PF, ruina%, DD máx, capital final mediano.
Si alguna métrica empeora, revierte el cambio.
```

---

## 6. Checklist antes de cerrar cualquier sesión de cambios

- [ ] `python scripts/check_env.py` pasa todos los OK relevantes
- [ ] Si tocaste `signal_engine.py`, `pair_config.py`, `capital_guard.py`: backtest 2 años + 1000 sims y métricas comparadas
- [ ] Si tocaste `trader.py` o `mt5_connector.py`: probaste con cuenta demo (no solo dry-run)
- [ ] `.env.example` actualizado si añadiste/quitaste env vars
- [ ] `requirements.txt` actualizado si añadiste dependencias (con versión pinned)
- [ ] Commit message en español, formato `categoría: descripción corta` (`fix:`, `feat:`, `refactor:`, `docs:`)
- [ ] No hay `print()` de debug olvidados
- [ ] No hay credenciales en código (todas vía env vars)

---

## 7. Recursos

- [strategy/prompt.md](strategy/prompt.md) — system prompt de la IA. **Editar con cuidado**, cambia el comportamiento del bot.
- [scripts/check_env.py](scripts/check_env.py) — diagnóstico unificado de conexiones.
- [backtest/run_backtest.py](backtest/run_backtest.py) — punto de entrada del backtest.
- Última versión estable conocida: commit `be7601d` (revert v6 → v5 USDJPY).
