# Contexto de sesión — Hooklab

Documento para retomar el proyecto sin releer toda la conversación anterior.
**Última actualización: 2026-08-26.**

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

**Último commit: `HL-13`.** Rama `main` sincronizada, CI en verde.
**Las fases 1 y 2 están completas: Hooklab captura y transmite en vivo, sin frontend todavía.**

| Commit | Contenido |
|---|---|
| HL-1 | Entorno local con Docker, esqueleto FastAPI, `/health` y `/ready` |
| HL-2 | Este documento de contexto |
| HL-3 | Primeras pruebas y CI en GitHub Actions |
| HL-4 | Badge de CI |
| HL-5 | Modelo de datos `endpoints` y `requests`, con Alembic |
| HL-6 | API de creación y consulta de endpoints; código pasado a inglés |
| HL-7, HL-8 | Actualización de este documento |
| HL-9 | Ruta de ingesta: captura cualquier petición en `/in/{ingest_token}` |
| HL-10 | Listado, detalle y descarga del cuerpo de las capturas |
| HL-11, HL-12 | Actualización del contexto; `CLAUDE.md` fuera del repositorio |
| HL-13 | **Fase 2**: feed en vivo con Redis Streams y SSE |

### Funciona y está verificado

- Postgres 18.6 y Redis 8 en Docker, con healthcheck.
- Configuración validada al arrancar; conexiones asíncronas a ambos.
- `/health` y `/ready`, probados **también en fallo**: con Redis caído, `/health` sigue en 200 y
  `/ready` da 503 diciendo qué dependencia falló; al volver Redis se recupera solo.
- **Flujo completo**: crear endpoint → recibir webhooks de cualquier método y content-type →
  listarlos paginados → ver el detalle → descargar el cuerpo crudo.
- Migración aplicada, con reversión probada en una ida y vuelta completa.
- **Feed en vivo**: `GET /api/endpoints/{view_token}/stream` entrega las capturas conforme llegan,
  con reconexión por `Last-Event-ID` y relleno del hueco desde Postgres.
- **La prueba que valida la arquitectura pasa**: `scripts/verify_fanout.py` contra
  `uvicorn --workers 4` — las 20 capturas cruzan de un proceso a otro, verificando por PID que
  realmente cayeron en workers distintos.
- 56 pruebas contra servicios reales, con aislamiento por `TRUNCATE` entre cada una.
- CI: matriz Python 3.12 y 3.14, con Postgres y Redis como servicios, corriendo `ruff`,
  `ruff format --check`, `mypy`, migraciones y `pytest`.
- Verificado también a mano con `curl` contra el servidor real.

### Falta

Firmas, reenvío, frontend y despliegue.

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
.venv/bin/ruff check app/ tests/ scripts/
.venv/bin/ruff format --check app/ tests/ scripts/
.venv/bin/mypy app/ tests/ scripts/
.venv/bin/pytest -q
```

Ver el feed en vivo a mano:

```bash
TOKEN=$(curl -s -X POST localhost:8010/api/endpoints -H 'content-type: application/json' \
  -d '{}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["view_token"], d["ingest_token"])')
curl -N localhost:8010/api/endpoints/${TOKEN%% *}/stream    # en una terminal
curl -X POST localhost:8010/in/${TOKEN##* } -d '{"hola":1}' # en otra
```

La prueba que valida la arquitectura (con el servidor en `--workers 4`):

```bash
.venv/bin/uvicorn app.main:app --port 8010 --workers 4
.venv/bin/python scripts/verify_fanout.py
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
- **Paginación por cursor, no por offset.** Con offset, una captura que llega mientras el usuario
  pagina repite una fila y se salta otra. Se pide una fila de más para saber si hay página
  siguiente, en lugar de un `COUNT` sobre toda la tabla.
- **El cuerpo crudo nunca se sirve con su content-type original**: siempre `octet-stream`,
  `attachment` y `nosniff`. Devolver `text/html` elegido por un desconocido convertiría el dominio
  en alojamiento de malware.
- **El control de acceso vive en una dependencia** (`EndpointDep`), no repetido en cada ruta: así
  no se puede olvidar.
- **El límite de cuerpo se aplica mientras se lee el flujo**, nunca después de cargarlo en memoria.
- **Cuerpo guardado crudo en `bytea`.** El HMAC de las firmas se calcula sobre los bytes exactos.
- **Anónimo primero**, endpoints reclamables. OAuth2 llega después.
- **Control de acceso por capacidad** (tokens), no RLS.
- **Reenvío confiable y verificación de firmas van en el núcleo**, no en una v2 hipotética.
- **Despliegue en VPS con Docker Compose y Caddy**, ~6 USD/mes. El SSE de larga duración descarta
  las plataformas serverless.
- **La posición del Stream se toma ANTES de rellenar desde Postgres.** Al revés, todo lo que llegue
  durante el relleno se pierde sin forma de detectarlo. El costo es algún duplicado, que se filtra.
- **El filtro anti-duplicados es un conjunto de ids, no una marca de agua.** El id se asigna en el
  `flush` y se publica tras el `commit`, así que bajo concurrencia el orden del Stream **no** sigue
  al de los ids. Con marca de agua se descartaba en silencio cerca de la mitad de las capturas, y
  solo bajo carga. Hay prueba de regresión.
- **El SSE no usa `SessionDep`.** Una dependencia con `yield` vive hasta que termina la respuesta, y
  la de un SSE no termina nunca: con el pool por defecto, la pestaña 16 deja sin conexiones a toda
  la app. Usa `DetachedEndpointDep` y abre su propia sesión solo para el relleno.
- **`ready` se manda después del relleno**, así significa "ya estás al día" y no solo "socket
  abierto".

### Ideas descartadas — no volver a proponerlas

- **Acredia** (cumplimiento de contratistas en México): dependencia regulatoria, incumbentes que lo
  venden empaquetado, y responsabilidad legal si el software se equivoca.
- **App de Shopify**: buen negocio, mala pieza de portafolio — casi todo pegamento de plataforma y
  no se puede demostrar sin una tienda.

---

## 6. Lo siguiente — fase 3: verificación de firmas

Con la captura y el tiempo real ya resueltos, sigue el segundo diferenciador.

1. **Interfaz común y un verificador por proveedor**, empezando por **Stripe y GitHub**: entre los
   dos cubren hex contra base64 y la tolerancia temporal de Stripe.
2. **El diferenciador es el diagnóstico, no el booleano.** No basta "firma inválida": hay que decir
   *por qué* — el HMAC no coincide, el timestamp tiene 400 s y cae fuera de la tolerancia, o el
   cuerpo llegó truncado. Ahí es donde la gente pierde horas y nadie lo resuelve hoy.
3. **`hmac.compare_digest`, jamás `==`.** Comparar byte a byte con salida temprana filtra la firma
   correcta por ataque de tiempo.
4. **El `signature_secret` se guarda cifrado** (AES-GCM con clave del entorno) y **nunca** sale por
   la API. Ya está el hueco previsto en el modelo de datos.
5. Vectores de prueba por proveedor, con casos negativos: secreto equivocado, timestamp vencido y
   cuerpo alterado en un byte — cada uno con su diagnóstico esperado.

El cuerpo crudo en `bytea` existe precisamente para esto: el HMAC se calcula sobre los bytes
exactos, y parsear y reserializar rompería toda verificación aunque el secreto fuera correcto.

Después: entrega confiable → frontend → cuentas.

### Trampas ya pagadas que conviene no volver a pisar

- **`ASGITransport` de httpx no hace streaming**: ejecuta la app hasta el final y junta el cuerpo
  entero. Contra un SSE infinito se cuelga para siempre. Por eso `tests/sse.py` habla ASGI directo.
- **pytest-asyncio crea un event loop por prueba**, y el cliente global de Redis conservaba
  conexiones del loop anterior ya cerrado. Se manifiesta como `Event loop is closed` en el *setup*
  de una prueba inocente. Resuelto desconectando el pool al final de cada prueba en `conftest.py`.
- **Matar al maestro de uvicorn no mata a los workers**, y su línea de comando no coincide con
  `pgrep -f 'uvicorn app.main'`. Hay que matarlos por PID o por grupo de procesos.

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
