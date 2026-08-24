# Contexto de sesión — Hooklab

Documento para retomar el proyecto sin releer toda la conversación anterior.
**Última actualización: 2026-08-24.**

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

**Último commit: `HL-6`.** Rama `main` sincronizada, CI en verde.

| Commit | Contenido |
|---|---|
| HL-1 | Entorno local con Docker, esqueleto FastAPI, `/health` y `/ready` |
| HL-2 | Este documento de contexto |
| HL-3 | Primeras pruebas y CI en GitHub Actions |
| HL-4 | Badge de CI |
| HL-5 | Modelo de datos `endpoints` y `requests`, con Alembic |
| HL-6 | API de creación y consulta de endpoints; código pasado a inglés |

### Funciona y está verificado

- Postgres 18.6 y Redis 8 en Docker, con healthcheck.
- Configuración validada al arrancar; conexiones asíncronas a ambos.
- `/health` y `/ready`, probados **también en fallo**: con Redis caído, `/health` sigue en 200 y
  `/ready` da 503 diciendo qué dependencia falló; al volver Redis se recupera solo.
- `POST /api/endpoints` y `GET /api/endpoints/{view_token}`.
- Migración aplicada, con reversión probada en una ida y vuelta completa.
- 13 pruebas contra servicios reales, con aislamiento por `TRUNCATE` entre cada una.
- CI: matriz Python 3.12 y 3.14, con Postgres y Redis como servicios, corriendo `ruff`,
  `ruff format --check`, `mypy`, migraciones y `pytest`.

### Falta

La ingesta (lo siguiente), tiempo real con SSE, firmas, reenvío, frontend y despliegue.

---

## 3. Cómo levantar el entorno

```bash
cd ~/learning/python/proyecto
docker compose up -d --wait          # Postgres + Redis

cd backend
.venv/bin/alembic upgrade head       # por si hay migraciones nuevas
.venv/bin/uvicorn app.main:app --port 8010 --reload

curl http://localhost:8010/ready     # {"status":"ready","checks":{...}}
```

Antes de cada commit, correr **exactamente lo que corre el CI**:

```bash
cd backend
.venv/bin/ruff check app/ tests/
.venv/bin/ruff format --check app/ tests/
.venv/bin/mypy app/ tests/
.venv/bin/pytest -q
```

---

## 4. Particularidades de ESTA máquina

Cosas que ya costaron tiempo una vez. No hay que volver a tropezar con ellas.

| Detalle | Qué saber |
|---|---|
| **Puerto 8010, no 8000** | El 8000 lo ocupa otro proyecto del usuario (`~/learning/python/fastapi`, `users:app`). No matarlo. |
| **Volumen de Postgres 18** | Se monta en `/var/lib/postgresql`, **no** en `/var/lib/postgresql/data`. La 18 cambió la convención; con la ruta vieja el contenedor no arranca. Casi todos los tutoriales están desactualizados. |
| **mypy + pydantic-settings** | `Settings()` sin argumentos da error `call-arg`. Resuelto con un `type: ignore` estrecho y comentado en `config.py`. **No** dar valores por defecto a las variables obligatorias: que falten debe romper el arranque. |
| **ruff B008 + FastAPI** | `Depends()` en valores por defecto dispara B008. No se silencia: se usan alias `Annotated` en `app/api/deps.py`, que es el estilo moderno de FastAPI. |
| **Python 3.14** | Todas las dependencias tienen wheels. psycopg3 elegido sobre asyncpg por disponibilidad. |
| **git** | Identidad global `ivanqenk` / `ivanqenk@gmail.com`. Llave SSH ed25519 en `~/.ssh/id_ed25519`, registrada en GitHub. |

---

## 5. Decisiones cerradas — no reabrir

- **Código en inglés** (clases, funciones, variables, columnas, docstrings, comentarios).
  La conversación con el usuario sigue en **español mexicano**.
- **Commits prefijados `HL-N`.** Para saber cuál sigue, mirar `git log --oneline` y continuar la
  secuencia — no fiarse de un número escrito aquí, que se desactualiza solo.
- **Redis Streams, no pub/sub**, para el fan-out del SSE.
- **El `bigserial` de `requests` es el `Last-Event-ID`.** Sin tabla de cursores.
- **Dos tokens separados**: `ingest_token` público, `view_token` secreto.
- **Tres esquemas por recurso** (Create / Created / Public). `Created` es la única respuesta que
  lleva `view_token`; como `Public` no tiene el campo, filtrarlo es imposible por construcción.
- **404 y no 403** ante un token inválido: un 403 confirmaría que el token existe.
- **Cuerpo guardado crudo en `bytea`.** El HMAC de las firmas se calcula sobre los bytes exactos.
- **Anónimo primero**, endpoints reclamables. OAuth2 llega después.
- **Control de acceso por capacidad** (tokens), no RLS.
- **Reenvío confiable y verificación de firmas van en el núcleo**, no en una v2 hipotética.
- **Despliegue en VPS con Docker Compose y Caddy**, ~6 USD/mes. El SSE de larga duración descarta
  las plataformas serverless.

### Ideas descartadas — no volver a proponerlas

- **Acredia** (cumplimiento de contratistas en México): dependencia regulatoria, incumbentes que lo
  venden empaquetado, y responsabilidad legal si el software se equivoca.
- **App de Shopify**: buen negocio, mala pieza de portafolio — casi todo pegamento de plataforma y
  no se puede demostrar sin una tienda.

---

## 6. Lo siguiente — la ruta de ingesta

Es el corazón de la fase 1. Captura cualquier petición que llegue a `/in/{ingest_token}`.

1. **Ruta catch-all**: `/in/{token}/{path:path}`, con todos los métodos
   (`GET POST PUT PATCH DELETE HEAD OPTIONS`).
2. **Cuerpo crudo con límite de 1 MB**, y el detalle que importa: **el límite se aplica mientras se
   lee el flujo**, abortando al superarlo. Si se lee entero y luego se mide, un cuerpo de 500 MB
   tumba el proceso — que es exactamente lo que busca un atacante.
3. Guardar cabeceras, query, IP de origen, content-type y tiempos.
4. `body_json` solo cuando el content-type lo justifica y el parseo no falla.
5. Responder rápido: los proveedores tienen timeouts cortos y reintentan si tardas.
6. Incrementar `request_count` del endpoint.

Luego **5d**: listar y ver las peticiones capturadas. Al terminar eso, el proyecto ya es útil con
`curl`, sin frontend.

Después, el orden del plan: tiempo real (Streams + SSE) → firmas → entrega confiable → frontend →
cuentas.

### Duda pendiente

**¿El README y los documentos de `docs/` pasan también a inglés?** La instrucción de escribir en
inglés se interpretó como código y docstrings. Para un repo de portafolio, un README en inglés
amplía mucho quién puede leerlo, pero está sin decidir.

---

## 7. Dónde está cada cosa

| Documento | Contenido |
|---|---|
| `~/.claude/plans/espera-sigamos-analizando-tiene-merry-karp.md` | **El plan completo**: arquitectura con sus 7 decisiones, modelo de amenazas, modelo de datos en SQL, fases y plan de verificación. Referencia principal. |
| `docs/prompt-maestro.md` | Metodología: fases, compuertas de aprobación, definición de terminado. |
| `README.md` | Cara pública del proyecto y puesta en marcha. |
| Este archivo | Estado y contexto para retomar. |

**Al retomar:** leer este archivo, levantar el entorno (sección 3), confirmar que `/ready`
responde, y arrancar con la sección 6.
