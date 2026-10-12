from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key: SecretStr = Field(min_length=16)
    database_url: SecretStr
    rabbitmq_url: SecretStr
    api_docs_enabled: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    gateway_min_delay: float = Field(default=2, ge=0, le=60)
    gateway_max_delay: float = Field(default=5, ge=0, le=60)
    gateway_success_rate: float = Field(default=0.9, ge=0, le=1)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay: float = Field(default=2, gt=0, le=3600)
    webhook_timeout: float = Field(default=10, gt=0, le=120)
    webhook_allowed_hosts: str = "webhook"
    outbox_poll_interval: float = Field(default=0.5, gt=0, le=60)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    broker_publish_timeout: float = Field(default=5, gt=0, le=60)

    @property
    def allowed_webhook_hosts(self) -> frozenset[str]:
        return frozenset(
            host.strip().rstrip(".").lower()
            for host in self.webhook_allowed_hosts.split(",")
            if host.strip()
        )

    @model_validator(mode="after")
    def validate_delays(self) -> Self:
        if self.gateway_max_delay < self.gateway_min_delay:
            raise ValueError("GATEWAY_MAX_DELAY must be at least GATEWAY_MIN_DELAY")
        if not self.allowed_webhook_hosts or any(
            any(char in host for char in "*/:@?#[]") or any(char.isspace() for char in host)
            for host in self.allowed_webhook_hosts
        ):
            raise ValueError(
                "WEBHOOK_ALLOWED_HOSTS must contain exact hostnames without wildcards"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
