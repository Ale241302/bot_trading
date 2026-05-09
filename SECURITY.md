# SECURITY.md

> Cómo trabajar con secretos en este repo y qué hacer si se filtran.

---

## 1. Qué hay en `.env` (nunca subir)

| Variable | Servicio | Riesgo si se filtra |
|----------|----------|---------------------|
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | MetaTrader 5 / Pepperstone | **Acceso a la cuenta de trading.** Operaciones no autorizadas, retiros si la cuenta es real. |
| `OPENAI_API_KEY` | OpenAI | **Costo financiero.** Atacante consume tu cuota; rate limit puede ser miles de USD/día. |
| `NOTION_TOKEN` | Notion | Lectura/escritura de la DB completa. |
| `PINECONE_API_KEY` | Pinecone | Lectura/escritura/borrado de tu índice vectorial. |
| `MYFXBOOK_EMAIL` / `MYFXBOOK_PASSWORD` | Myfxbook | Acceso a la cuenta. **La API requiere usuario+password en la URL** (limitación del proveedor). |

---

## 2. Reglas absolutas

1. **Nunca commits `.env`**. Está en `.gitignore` (verifica `git ls-files | grep .env`).
2. **Nunca pegues claves en código** ni en mensajes de commit.
3. **Nunca compartas screenshots con `.env` abierto.**
4. **Nunca pongas claves en URLs públicas, paste-bins, Discord/Slack públicos, o issues de GitHub.**
5. **Usa `.env.example` como plantilla** — solo nombres de variables, sin valores reales.
6. Si abres un PR, revisa el diff con `git diff --staged` antes de `git push`.

---

## 3. Qué hacer si una clave se filtra

### Si llegaste a hacer commit de `.env` por accidente:

1. **No esperes.** Los bots scrapeen GitHub en minutos.
2. **Rota inmediatamente** la clave comprometida (pasos abajo).
3. **Reescribe la historia git** para borrar el archivo:
   ```bash
   # Si todavía no hiciste push:
   git reset --soft HEAD~1
   git restore --staged .env
   git commit -m "..."

   # Si YA hiciste push: usa BFG Repo-Cleaner o git filter-repo
   # https://rtyley.github.io/bfg-repo-cleaner/
   ```
4. **Notifica al equipo** si el repo es compartido.

---

## 4. Cómo rotar cada clave

### MT5 / Pepperstone
- Ingresa al portal de Pepperstone.
- Demo: cuenta nueva en 30s desde el panel; cambia `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` en `.env`.
- Real: reset de password vía email + 2FA.

### OpenAI
- https://platform.openai.com/api-keys → revoca la key comprometida → crea nueva.
- **Vigila el dashboard de uso** las próximas 48h por consumo anómalo.

### Notion
- https://www.notion.so/my-integrations → selecciona la integración → "Secrets" → regenera.
- Si hay sospecha de exfil de datos, **revoca el acceso de la integración a la DB** y revísala manualmente.

### Pinecone
- https://app.pinecone.io/ → API Keys → revoca y crea nueva.
- Verifica que no se hayan creado índices nuevos (señal de uso no autorizado).

### Myfxbook
- Cambia password desde la web. **El usuario `MYFXBOOK_EMAIL` no se puede rotar** — si quieres cambiar el email, crea cuenta nueva.

---

## 5. Buenas prácticas adicionales

- **Cuenta demo siempre primero**. No conectes a real hasta tener 2 semanas de demo limpia.
- **Limita el alcance de las API keys** donde el proveedor lo permita (Notion: por DB; Pinecone: por proyecto; OpenAI: tier de gasto).
- **Activa 2FA en todas las cuentas** (OpenAI, Notion, Pinecone, broker).
- **Auditoría periódica**: cada 90 días, verifica que las keys usadas son las esperadas y rota las que no se usan.
- **Permisos de archivo en producción**: `chmod 600 .env` (solo dueño puede leer). En Windows: clic derecho → propiedades → seguridad → quita herencia y deja solo a tu usuario.
- **No uses la cuenta personal del proveedor para producción.** Crea cuentas de servicio si vas a operar en serio.

---

## 6. Auditar periódicamente que no haya secretos en el repo

```bash
# Buscar patrones obvios de claves en el historial completo:
git log --all -p | grep -iE "(api[_-]?key|password|secret|token).*=.*['\"][A-Za-z0-9_/+=-]{20,}"

# Tools recomendadas (instalar aparte):
# - https://github.com/trufflesecurity/trufflehog
# - https://github.com/Yelp/detect-secrets
```

---

## 7. Reportar una vulnerabilidad

Si encuentras un problema de seguridad en este código, escríbele directo al mantenedor — **no abras un issue público**. Las vulnerabilidades en código de trading pueden afectar dinero real de varios usuarios.
