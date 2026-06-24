from faststream.rabbit import RabbitBroker

from app.config import get_settings


def create_broker() -> RabbitBroker:
    settings = get_settings()
    return RabbitBroker(
        settings.rabbitmq_url,
        timeout=10.0,
    )
