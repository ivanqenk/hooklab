"""Configuración de la aplicación, leída del entorno.

Se lee y valida una sola vez al arrancar. Si falta una variable obligatoria, la
aplicación falla de inmediato con un mensaje claro, en vez de reventar a media
petición con un error incomprensible.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Este archivo vive en backend/app/core/, así que la raíz del proyecto está tres
# niveles arriba. Resolvemos la ruta desde la ubicación del archivo y NO desde el
# directorio de trabajo: así el .env se encuentra igual si arrancas uvicorn desde
# backend/, desde la raíz, o desde donde sea.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Entorno ---
    app_env: str = "development"
    log_level: str = "INFO"

    # --- Obligatorias ---
    # Sin valor por defecto a propósito: si faltan, la app no arranca.
    database_url: str
    redis_url: str
    secret_key: str

    # --- Ingesta ---
    public_ingest_base: str = "http://localhost:8000"
    max_body_bytes: int = 1_048_576  # 1 MB
    anon_retention_hours: int = 72

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración, leyendo el entorno una sola vez por proceso."""
    # mypy considera database_url, redis_url y secret_key argumentos obligatorios
    # del constructor porque no tienen valor por defecto. Sobre la firma estática
    # tiene razón; sobre el comportamiento real no, porque pydantic-settings los
    # rellena desde el entorno al instanciar.
    #
    # Se ignora SOLO este error y SOLO en esta línea. No les ponemos valor por
    # defecto a propósito: que sean obligatorios es justo lo que hace que la
    # aplicación falle al arrancar si falta una variable, en vez de a media petición.
    return Settings()  # type: ignore[call-arg]
