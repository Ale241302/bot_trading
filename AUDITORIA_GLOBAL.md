# Auditoría Global — `bot_trading`

> Informe ejecutivo consolidado tras revisión completa del repositorio. Fecha: 2026-05-09.
> Severidad: 🔴 crítico · 🟠 importante · 🟡 mejora · 🔵 nota

---

## 1. Estado general del proyecto

El bot está en **estado funcional, post-refactor P0+P1**. Las correcciones que `instrucciones.md` marca como ✅ ARREGLADO **están reflejadas fielmente en el código**:

- Atomic writes (`os.replace` post-`fsync`) en `trade_monitor.py` y `capital_guard.py`. ✅
- `Trader._pip_size(info)` ajusta pip-factor por `info.digits` → USDJPY (3 dígitos) calcula SL/TP correctamente. ✅
- Validación de env vars en `MT5Connector.__init__`. ✅
- Retry exponencial OpenAI 3× (2s→4s→8s + jitter), solo errores transientes. ✅
- `MyfxbookClient` único inyectado en `MarketContext`; `AIAnalyst` recibe sentimiento pre-consultado. Una sola llamada/ciclo. ✅
- Constantes de gestión centralizadas en `capital_guard.py` e importadas por `backtest_runner.py`. ✅
- Stub `should_hold_news()` eliminado de código y `main.py`. ✅
- Logging estructurado (`TerseFormatter`, rotación 5MB×5, vars `LOG_LEVEL`/`LOG_FILE`). ✅

**Pero hay deuda técnica viva** que ninguno de los P0/P1 cubrió. La auditoría detecta 9 hallazgos críticos/altos repartidos en 4 áreas.

---

## 2. Hallazgos críticos y altos consolidados

### 🔴 Críticos (atender antes del próximo ciclo en demo/real)

| # | Área | Archivo:línea | Hallazgo | Impacto |
|---|------|----|----|----|
| C1 | MT5 | `modules/mt5_connector.py:59-84` | `connect()` deja **sesión zombie**: si `mt5.initialize()` éxito pero `mt5.login()` falla, no llama `mt5.shutdown()`. | Bucle infinito de reconexiones fallidas tras un único fallo de credencial/red. |
| C2 | Estrategia | `strategy/prompt.txt` (existe junto a `prompt.md`) | **Dos prompts contradictorios** activos. `.md` usa SL=8/TP=16 y máx 1 trade; `.txt` usa SL=15/TP=30 y dos trades simultáneos. | Riesgo alto si alguien edita el archivo equivocado. Tech debt confuso. |
| C3 | IA ↔ Riesgo | `strategy/prompt.md` vs `main.py:127-134` | **Drift prompt↔código sobre lote/SL dinámicos**: el prompt promete lote y SL ajustados por fase; el código no inyecta `phase_text` ni valida que la IA aplique la regla. La IA decide ciegamente. | La fase Escudo (1%) podría no ejecutarse si la IA se olvida del riesgo dinámico. |
| C4 | Seguridad | `.env` (raíz del repo) | **Credenciales en plaintext** en `.env`: MT5 login, OpenAI key, Notion token, Pinecone key, Myfxbook password. | Crítico si el repo es público/se sube por accidente. `.gitignore` lo cubre, pero rotar tras la auditoría es prudente. |
| C5 | Backtest fidelidad | `backtest/signal_engine.py:240-245` | Naming confuso: `nivel_2_sentimiento` en realidad calcula tendencia; el sentimiento real es `nivel_3`. **Rompe mapeo mental con prompt.md**. | Bug latente: el siguiente refactor puede "corregir" en la dirección equivocada. |
| C6 | Tests | `tests/` | **Sin unit tests** de funciones puras críticas: `signal_engine.detect_pattern`, `capital_guard.should_trade`, `_pip_size`, `evaluate_confluence`. | El backtest hace de regresión, pero un cambio sutil puede pasar inadvertido. |
| C7 | Backtest realismo | `backtest/backtest_runner.py` (RNG) + `signal_engine.simulate_sentiment` | El sentimiento Myfxbook se **simula con normal por par**, no se replica histórico real. Monte Carlo compone la simulación sobre datos sintéticos. | Resultados de backtest sobreoptimistas en condiciones de mercado donde el sentimiento real divergiera. |

### 🟠 Importantes (siguiente sprint)

| # | Área | Archivo:línea | Hallazgo |
|---|------|---|---|
| H1 | Trader | `modules/trader.py:_execute_close` | Ticket fantasma: si `positions_get(ticket=)` viene vacío, retorna None silencioso. `TradeMonitor` queda esperando. |
| H2 | Trader | `modules/trader.py:_execute_pending` | Auto-reajuste de precio (`MIN_DIST_PIPS`/`REPRICE_PIPS`) **no se loguea como WARNING**. La IA cree haber ejecutado a precio A pero ejecutó a B. |
| H3 | Riesgo | `modules/capital_guard.py:should_trade` | Falta chequeo de `MAX_SL_WEEK` (5 SL en 7 días). Hoy solo se valida racha consecutiva del día. |
| H4 | IA | `modules/ai_analyst.py` + `main.py` | **Sin validación de schema** sobre el JSON de la IA. Si falta `sl_pips`, `float()` crashea. |
| H5 | Pipe Notion | `modules/notion_logger.py:update_operation` | `try/except Exception` traga errores genéricos sin stack. Difícil debug si Notion devuelve 404. |
| H6 | Backtest H1/H4 | `backtest/backtest_runner.py:334` | Lookback fijo de 100 velas H1/H4: no equivale a "últimos 4 días reales" si hay gaps de fin de semana. Sesgo en tendencia. |
| H7 | run_all_tests | `tests/run_all_tests.py:21-24` | Solo lista 2/4 tests. Faltan `test_myfxbook.py` y `test_notion_pinecone_integration.py`. |
| H8 | Reqs | `requirements.txt` / `requirements-backtest.txt` | OpenAI/Pinecone/yfinance/pandas con `>=` flotante. Reproducibilidad rota entre máquinas. |

---

## 3. Mejoras y notas (resumen)

🟡 **Logging y observabilidad**: `_load_active_trades` traga JSON corrupto sin alertar; `notion_logger.update_operation` también. Hardcoded `0.20`/`0.50` en `capital_guard.get_phase`.
🟡 **Costos OpenAI**: ~$0.35/día evitables con prompt caching (requiere prompt > 1024 tokens; el actual no califica).
🟡 **`datetime.utcnow()`** deprecado en `backtest/data_loader.py:53` (Py 3.12+).
🟡 **Magic numbers** sin nombrar en `signal_engine._is_pin_bar_*` (0.55/0.35/0.25).
🟡 **`run_backtest()` 350+ líneas** — refactor a helpers (`_evaluate_signal`, `_execute_simulated_trade`, `_record_trade`).
🟡 **Pinecone query artificial** en `get_operations_by_symbol`; usar solo filtro `$eq`.
🟡 **`LOT_SIZE`** en `.env.example` documentado pero nunca leído por código.
🔵 **Smoke test OpenAI** (5 tokens) en `check_env.py` ya está; no valida balance ni modelo.

---

## 4. Plan de acción priorizado (orden sugerido)

**Sprint 1 — antes del próximo arranque**

1. **C1 — Sesión zombie MT5**. 1 línea: `mt5.shutdown()` antes del `return False` en `connect()`.
2. **C4 — Rotar credenciales** del `.env` y verificar que el archivo está en `.gitignore` (lo está). Considerar mover a vault o variables del SO.
3. **C2 — Eliminar `strategy/prompt.txt`**. Es ruido peligroso. Solo dejar `prompt.md`.
4. **H1 + H2 — Logs explícitos en Trader** para `_execute_close` (ticket fantasma) y `_execute_pending` (reajuste de precio).
5. **H4 — Validación de schema** del JSON IA en `main.py` con fallback a defaults seguros (HOLD).

**Sprint 2 — alineación arquitectónica**

6. **C3 — Alinear IA con `CapitalGuard`**: pasar `phase_text` a `analyze()`, actualizar prompt para citar `pair_config.py` por par. Validar con backtest 2 años + 1000 sims contra `be7601d`.
7. **H3 — Implementar `MAX_SL_WEEK`** dentro de `should_trade()`.
8. **C5 — Renombrar campos** de confluencia en `signal_engine.py` a `nivel_2_tendencia`, `nivel_3_sentimiento`. Cambio mecánico pero importante.
9. **H6 — Lookback H1/H4 por timedelta** en lugar de N velas fijas.

**Sprint 3 — calidad y reproducibilidad**

10. **C6 — Unit tests** para `signal_engine.detect_pattern`, `capital_guard.should_trade`, `_pip_size`, `_consecutive_sl_today`. Meta: 30 casos pytest.
11. **H7 — Auto-discovery** de tests en `run_all_tests.py` con `glob("tests/test_*.py")`.
12. **H8 — Pinear versiones** en `requirements*.txt` (== en lugar de >=).
13. **C7 — Reemplazar `simulate_sentiment` por histórico real** si Myfxbook expone API histórica; o documentar el sesgo en el reporte HTML.

---

## 5. Métricas a vigilar tras cualquier cambio

Cualquier modificación de `signal_engine.py`, `pair_config.py`, `capital_guard.py`, `trader.py` debe pasar:

```bash
python -m backtest.run_backtest --years 2 --sims 1000 --seed 42
```

Compara contra baseline `be7601d` (commit v5):

| Métrica | Umbral mínimo |
|---|---|
| Win Rate | ≥ 48% |
| Profit Factor | ≥ 1.50 |
| Ruina (% sims) | ≤ 5% |
| Drawdown máximo | ≤ 20% |

Si alguna empeora, revertir el cambio.

---

## 6. Áreas sin hallazgos críticos (cobertura validada)

- **`modules/trader.py::_pip_size`** — funciona correctamente para EURUSD/GBPUSD/USDJPY.
- **`modules/ai_analyst.py::_call_openai_with_retry`** — backoff y diferenciación 4xx/5xx correcta.
- **`modules/trade_monitor.py::check_closed_trades`** — conservador (espera deals antes de marcar cerrado).
- **`modules/logging_config.py`** — limpio, idempotente, rotación bien.
- **`backtest/pair_config.py`** — bien estructurado, cambios v5 justificados.
- **`backtest/monte_carlo.py`** — bootstrap correcto en líneas generales.
- **Coherencia 4 niveles prompt ↔ signal_engine** — los 4 filtros (Noticias, Tendencia H4+H1, Sentimiento >60%, Patrón M15) replican fielmente el prompt.

---

## 7. Conclusión

El proyecto tiene **arquitectura sólida** y los refactors P0+P1 reportados están aplicados. La deuda restante se concentra en cuatro frentes:

1. **Robustez de borde** (sesión zombie MT5, ticket fantasma, JSON sin validar).
2. **Coherencia documentación↔código** (prompt.txt fantasma, prompt.md desalineado con multi-par y fases).
3. **Observabilidad** (excepciones genéricas, reajustes silenciosos).
4. **Reproducibilidad** (versiones flotantes, sin unit tests sobre funciones puras críticas).

Ninguno de los hallazgos justifica detener producción si hoy no estuviera en demo, pero **C1 y C2 deberían cerrarse en la próxima sesión de cambios**. El resto puede ir por sprints, siempre con backtest + Monte Carlo como gate antes de mergear.

---

*Informe generado a partir de revisión paralela de 5 agentes especializados sobre los módulos: trading core, IA & memoria, riesgo & monitoreo, backtest, infraestructura/tests.*
