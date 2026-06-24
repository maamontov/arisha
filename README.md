# Arisha Payments

Асинхронный процессинг платежей. FastAPI + Postgres + RabbitMQ.

## Запуск

```bash
cp .env.example .env
make build
make up
make migrate
```

API: http://localhost:8000
Swagger: http://localhost:8000/docs
RabbitMQ UI: http://localhost:15672 (guest/guest)
Webhook receiver: http://localhost:9000

## API

Создать платёж:

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "X-API-Key: dev-secret-key-change-me" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{
    "amount": "100.00",
    "currency": "USD",
    "webhook_url": "http://webhook-receiver:9000/webhook"
  }'
```

Получить платёж:

```bash
curl http://localhost:8000/api/v1/payments/<payment_id> \
  -H "X-API-Key: dev-secret-key-change-me"
```

## Тесты

```bash
make test          # все
make test-unit     # unit
make test-e2e      # e2e (нужен запущенный стек)
make test-cov      # coverage
```

## Логи и состояние

```bash
make logs                              # все сервисы
make logs service=consumer             # один сервис
docker compose ps                      # статус контейнеров
make down                              # остановить
make reset-db                          # снести БД и пересоздать (DESTRUCTIVE)
```

## Что внутри

- `app/api/v1/payments.py` — эндпоинты
- `app/services/payment_service.py` — бизнес-логика, идемпотентность
- `app/messaging/consumer.py` — обработка + retry/DLQ
- `app/messaging/webhook.py` — webhook с экспоненциальным backoff
- `app/messaging/gateway.py` — эмуляция шлюза (2-5с, 90% успех)
- `app/outbox/relay.py` — outbox → RabbitMQ (SKIP LOCKED)
- `app/models/` — SQLAlchemy 2.0 async
- `alembic/versions/0001_initial.py` — миграция
- `tests/test_e2e.py` — сквозные сценарии

## Гарантии доставки

1. POST пишет `payment` + `outbox` в одной транзакции.
2. Relay забирает pending-события (`FOR UPDATE SKIP LOCKED`), публикует в RabbitMQ, помечает `published`.
3. Consumer обрабатывает: эмуляция шлюза → UPDATE статуса → webhook.
4. Ошибка → повтор через `payments.retry` (TTL 1с/2с) с инкрементом `x-attempt`.
5. После 3 попыток → `payments.dlq`.

Платёж и webhook-доставка независимы: статус платежа фиксируется сразу после шлюза, webhook ретраится отдельно.
