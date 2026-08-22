# Hooklab

**Gateway de webhooks para desarrollo: recibe, verifica y reenvía.**

Cuando integras Stripe, GitHub o cualquier proveedor que mande webhooks, te topas con tres
problemas:

1. Tu máquina no existe en internet, así que el proveedor no puede alcanzar tu `localhost`.
2. La documentación te dice qué *debería* llegar, pero el payload real trae campos que no
   esperabas y cabeceras de firma que hay que validar.
3. Cuando algo truena en producción, tus logs guardaron el stack trace pero no el cuerpo — así
   que no puedes reproducirlo.

Hooklab te da una URL pública donde cada petición **se captura y aparece en vivo**, se
**verifica la firma** del proveedor diciéndote *por qué* falla cuando falla, y se **reenvía** a
tu destino con reintentos y backoff exponencial.

> **Estado: en construcción.** Hoy existe el esqueleto — configuración, conexiones a Postgres y
> Redis, y los endpoints de salud. La ingesta es lo siguiente.

## Requisitos

- Python 3.12 o superior
- Docker y Docker Compose

## Puesta en marcha

```bash
# 1. Configuración
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # pega el resultado en SECRET_KEY

# 2. Dependencias (Postgres + Redis)
docker compose up -d --wait

# 3. Backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --port 8010 --reload
```

Comprobar que quedó bien:

```bash
curl http://localhost:8010/health   # {"status":"ok"}
curl http://localhost:8010/ready    # {"status":"ready","checks":{...}}
```

La documentación interactiva de la API queda en <http://localhost:8010/docs>.

## Calidad

```bash
cd backend
.venv/bin/ruff check app/     # linter
.venv/bin/ruff format app/    # formato
.venv/bin/mypy app/           # tipos
```

## Decisiones técnicas

Las decisiones de fondo, con sus alternativas descartadas, viven en `docs/`. Un resumen de las
que más condicionan el código:

**Redis Streams, no pub/sub.** La conexión SSE del navegador vive en un proceso de uvicorn, pero
el webhook entrante puede caer en otro. Sin un bus compartido el navegador nunca lo ve — y con un
solo worker el bug no aparece, lo que lo hace especialmente traicionero. Streams además resuelve
de forma nativa el hueco de la reconexión, que es justo para lo que existe el header
`Last-Event-ID` de SSE.

**El cuerpo se guarda crudo.** Los webhooks reales mandan XML, `form-encoded` y binario, no solo
JSON. Y sobre todo: el HMAC de una firma se calcula sobre los **bytes exactos** del cuerpo. Si el
framework parsea y vuelve a serializar, la verificación falla aunque el secreto sea correcto —
es la causa número uno de "mi validación de webhooks no sirve".

**Dos tokens distintos.** El de ingesta es público y acaba en logs y capturas de pantalla; el de
visualización es secreto y es el único que permite *leer* el tráfico. Con un solo token,
cualquiera que viera esa URL en una configuración podría leer todos tus payloads.

**Liveness ≠ readiness.** `/health` no consulta dependencias a propósito: si Postgres se cae, el
proceso sigue sano y reiniciarlo no arreglaría nada. `/ready` sí las consulta y devuelve 503, para
que un balanceador deje de enviarle tráfico sin matar la instancia.

## Licencia

Por definir.
