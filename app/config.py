from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "arisha-payments"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    environment: Literal["dev", "staging", "prod", "test"] = "dev"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_key: str = Field(default="dev-secret-key-change-me")

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "payments"
    postgres_password: str = "payments"
    postgres_db: str = "payments"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_vhost: str = "/"
    rabbitmq_management_port: int = 15672

    payments_exchange: str = "payments"
    payments_retry_exchange: str = "payments.retry"
    payments_dlx: str = "payments.dlx"
    payments_new_queue: str = "payments.new"
    payments_retry_queue: str = "payments.retry"
    payments_dlq: str = "payments.dlq"
    payments_routing_key: str = "payment.created"
    payments_failed_routing_key: str = "payment.failed"

    outbox_poll_interval_ms: int = 200
    outbox_batch_size: int = 50
    outbox_max_attempts: int = 5

    consumer_prefetch: int = 10
    webhook_timeout_s: float = 10.0
    webhook_max_attempts: int = 3
    webhook_retry_base_delay_s: float = 1.0
    payments_max_attempts: int = 3
    payments_retry_base_delay_s: float = 1.0

    gateway_min_delay_s: float = 2.0
    gateway_max_delay_s: float = 5.0
    gateway_success_rate: float = 0.9

    webhook_receiver_host: str = "0.0.0.0"
    webhook_receiver_port: int = 9000

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def rabbitmq_url(self) -> str:
        vhost = (
            self.rabbitmq_vhost
            if self.rabbitmq_vhost.startswith("/")
            else f"/{self.rabbitmq_vhost}"
        )
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}{vhost}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
