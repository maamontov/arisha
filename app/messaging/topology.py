import aio_pika
from aio_pika.abc import AbstractRobustConnection
from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

PAYMENTS_EXCHANGE = RabbitExchange(
    "payments",
    type=ExchangeType.TOPIC,
    durable=True,
)
PAYMENTS_RETRY_EXCHANGE = RabbitExchange(
    "payments.retry",
    type=ExchangeType.DIRECT,
    durable=True,
)
PAYMENTS_DLX = RabbitExchange(
    "payments.dlx",
    type=ExchangeType.TOPIC,
    durable=True,
)

PAYMENTS_NEW_QUEUE = RabbitQueue(
    "payments.new",
    durable=True,
    arguments={
        "x-dead-letter-exchange": "payments.dlx",
        "x-dead-letter-routing-key": "payment.failed",
    },
)
PAYMENTS_RETRY_QUEUE = RabbitQueue(
    "payments.retry",
    durable=True,
    arguments={
        "x-dead-letter-exchange": "payments",
    },
)
PAYMENTS_DLQ_QUEUE = RabbitQueue("payments.dlq", durable=True)

PAYMENTS_ROUTING_KEY = "payment.created"
PAYMENTS_FAILED_ROUTING_KEY = "payment.failed"


async def declare_topology(connection: AbstractRobustConnection) -> None:
    channel = await connection.channel()
    try:
        payments_exchange = await channel.declare_exchange(
            "payments",
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        retry_exchange = await channel.declare_exchange(
            "payments.retry",
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        dlx_exchange = await channel.declare_exchange(
            "payments.dlx",
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        new_queue = await channel.declare_queue(
            "payments.new",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "payments.dlx",
                "x-dead-letter-routing-key": "payment.failed",
            },
        )
        await new_queue.bind(payments_exchange, routing_key="payment.created")

        retry_queue = await channel.declare_queue(
            "payments.retry",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "payments",
            },
        )
        await retry_queue.bind(retry_exchange, routing_key="payment.created")

        dlq_queue = await channel.declare_queue(
            "payments.dlq",
            durable=True,
        )
        await dlq_queue.bind(dlx_exchange, routing_key="payment.failed")
    finally:
        await channel.close()
