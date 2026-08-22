"""Cliente Redis compartido."""

from redis.asyncio import Redis

from app.core.config import get_settings

settings = get_settings()

# decode_responses=False a propósito, y es una decisión de fondo, no un detalle.
#
# Los cuerpos de webhook son BYTES: llegan en XML, form-encoded, multipart o
# binario, no solo JSON. Y sobre todo, el HMAC de una firma se calcula sobre los
# bytes EXACTOS del cuerpo. Decodificar a str y volver a codificar puede alterar
# esos bytes, y entonces TODA verificación de firma fallaría aunque el secreto
# sea correcto. Es la causa número uno de "mi verificación de webhooks no sirve".
redis: Redis = Redis.from_url(settings.redis_url, decode_responses=False)
