# Contexto de sesión — Hooklab

Documento para retomar el proyecto sin releer toda la conversación anterior.
**Última actualización: 2026-08-21.**

---

## 1. Qué es Hooklab

Gateway de webhooks para desarrollo. Generas una URL pública y cada petición que llega ahí:

1. se **captura** y aparece en vivo en el navegador (SSE),
2. se **verifica la firma** del proveedor, diciendo *por qué* falla si falla,
3. se **reenvía** a tu destino con reintentos, backoff exponencial e idempotencia.

**Objetivos, en orden:** aprender el stack → tener un repo de portafolio → quizá algo de dinero.

**Repositorio:** <https://github.com/ivanqenk/hooklab> · **Dominio elegido:** `hooklab.dev`
(verificado libre, aún sin registrar — solo hace falta al desplegar).

---

## 2. Dónde estamos exactamente

**Último commit: `ea33efe` — HL-1.** Rama `main`, sincronizada con el remoto.

### Funciona y está verificado

- Postgres 18.6 y Redis 8 en Docker, ambos con healthcheck.
- Configuración leída y validada del entorno al arrancar.
- Conexión asíncrona a Postgres (SQLAlchemy 2 + psycopg3) y a Redis (modo bytes).
- `/health` y `/ready`, probados **también en fallo**: con Redis caído, `/health` sigue en 200 y
  `/ready` devuelve 503 identificando qué dependencia falló; al volver Redis se recupera solo.
- `ruff` y `mypy` en verde.

### Falta todo lo demás

CI, despliegue, y la aplicación en sí: ingesta, SSE, firmas, reenvío, frontend.

---

## 3. Cómo levantar el entorno

```bash
cd ~/learning/python/proyecto

docker compose up -d --wait          # Postgres + Redis

cd backend
.venv/bin/uvicorn app.main:app --port 8010 --reload

# comprobar
curl http://localhost:8010/ready     # {"status":"ready","checks":{...}}
```

Calidad: `.venv/bin/ruff check app/` · `.venv/bin/mypy app/`

---

## 4. Particularidades de ESTA máquina

Cosas que ya costaron tiempo una vez. No hay que volver a tropezar con ellas.

| Detalle | Qué saber |
|---|---|
| **Puerto 8010, no 8000** | El 8000 lo ocupa otro proyecto del usuario (`~/learning/python/fastapi`, `users:app`). No matarlo. |
| **Volumen de Postgres 18** | Se monta en `/var/lib/postgresql`, **no** en `/var/lib/postgresql/data`. La versión 18 cambió la convención; con la ruta vieja el contenedor no arranca. Casi todos los tutoriales están desactualizados. |
| **mypy + pydantic-settings** | `Settings()` sin argumentos da error `call-arg`. Resuelto con un `type: ignore` estrecho y comentado en `config.py`. **No** dar valores por defecto a las variables obligatorias: que falten debe romper el arranque. |
| **Python 3.14** | Todas las dependencias tienen wheels. Se eligió psycopg3 sobre asyncpg por disponibilidad. |
| **Docker en WSL** | Ya funciona sin `sudo`. |
| **git** | Identidad global: `ivanqenk` / `ivanqenk@gmail.com`. Llave SSH ed25519 registrada en GitHub. |

---

## 5. Decisiones cerradas — no reabrir

Se analizaron a fondo y están justificadas en el plan. Reabrirlas cuesta tiempo sin aportar nada.

- **Redis Streams, no pub/sub**, para el fan-out del SSE. Streams resuelve nativamente el hueco de
  reconexión vía `Last-Event-ID`.
- **El `bigserial` de `requests` es el `Last-Event-ID`.** Sin tabla de cursores.
- **Dos tokens separados**: `ingest_token` público, `view_token` secreto.
- **Cuerpo guardado crudo en `bytea`.** Los webhooks mandan XML y binario, y el HMAC se calcula
  sobre los bytes exactos.
- **Anónimo primero**, endpoints reclamables. OAuth2 llega después.
- **Control de acceso por capacidad** (tokens), no RLS. Workspaces con RLS mucho más adelante.
- **Reenvío confiable y verificación de firmas van en el núcleo**, no en una v2 hipotética: ahí
  está la ingeniería difícil y el único camino a monetizar.
- **Despliegue en VPS con Docker Compose y Caddy**, ~6 USD/mes. El SSE de larga duración descarta
  las plataformas serverless.
- **Commits prefijados `HL-N`.** El siguiente es HL-2.

### Ideas descartadas — no volver a proponerlas

- **Acredia** (cumplimiento de contratistas en México): dependencia regulatoria, incumbentes que
  lo venden empaquetado, y responsabilidad legal si el software se equivoca.
- **App de Shopify**: buen negocio, mala pieza de portafolio — casi todo pegamento de plataforma
  y no se puede demostrar sin una tienda.

---

## 6. Lo siguiente

**Decisión pendiente**, planteada y sin responder:

- **Opción A — HL-2 = CI.** Workflow de GitHub Actions con Postgres y Redis como servicios,
  corriendo `ruff`, `mypy` y `pytest`. Cierra la fase 5. Todavía no hay pruebas, pero montar la
  tubería ahora hace que crezca con el código.
- **Opción B — HL-2 = ingesta.** Ir directo a la parte divertida: crear endpoint, capturar
  peticiones, listarlas. Al terminarla el proyecto ya es útil con `curl`.

Después de eso, el orden del plan es: tiempo real (Streams + SSE) → firmas → entrega confiable →
frontend → cuentas.

---

## 7. Dónde está cada cosa

| Documento | Contenido |
|---|---|
| `~/.claude/plans/espera-sigamos-analizando-tiene-merry-karp.md` | **El plan completo**: arquitectura con sus 7 decisiones, modelo de amenazas, modelo de datos en SQL, fases y plan de verificación. Es la referencia principal. |
| `docs/prompt-maestro.md` | Metodología de trabajo: fases, compuertas de aprobación, definición de terminado. |
| `README.md` | Cara pública del proyecto y puesta en marcha. |
| Este archivo | Estado y contexto para retomar. |

**Al retomar:** leer este archivo, levantar el entorno (sección 3), confirmar que `/ready`
responde, y elegir entre las opciones de la sección 6.
