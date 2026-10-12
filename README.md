# Платёжный сервис

Асинхронный сервис на FastAPI, PostgreSQL и RabbitMQ.

## Что реализовано

- Создание платежа и получение его статуса через API.
- Асинхронная обработка платежа с эмуляцией платёжного шлюза.
- Outbox для надёжной публикации событий в RabbitMQ.
- Webhook-уведомления с повторными попытками и DLQ.
- API-ключ для доступа и `Idempotency-Key` для защиты от повторного создания платежа.
- Миграции Alembic, unit- и integration-тесты.

## Запуск проекта

Нужны Docker Engine и Docker Compose v2.

```bash
cp .env.example .env
docker compose --profile demo up --build -d
```

Compose запустит PostgreSQL, RabbitMQ, API, consumer и демонстрационный webhook-приёмник. Откройте Swagger UI: <http://localhost:8000/docs>. Нажмите **Authorize** и введите `API_KEY` из `.env`.

Логи:

```bash
docker compose logs -f api consumer
docker compose --profile demo logs -f webhook
```

Остановить сервисы:

```bash
docker compose down
```

## Запуск тестов

Для локальных unit-тестов установите зависимости и активируйте окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.lock
make test
```

Полный прогон с тестовыми PostgreSQL и RabbitMQ в Docker:

```bash
make test-integration
```

Цель `test-integration` запускает unit- и integration-тесты, затем удаляет временные тестовые контейнеры и данные.

Дополнительные проверки:

```bash
make lint typecheck
```

## Проверка API

Документация также доступна в ReDoc: <http://localhost:8000/redoc>.

Создать платёж можно в Swagger UI (`POST /api/v1/payments`) или через `curl`:

```bash
curl -i http://localhost:8000/api/v1/payments \
  -H 'X-API-Key: local-development-key-change-me' \
  -H 'Idempotency-Key: order-1001' \
  -H 'Content-Type: application/json' \
  -d '{
    "amount": "1500.50",
    "currency": "RUB",
    "description": "Заказ 1001",
    "metadata": {"order_id": 1001},
    "webhook_url": "http://webhook:8080/webhook"
  }'
```

Если вы изменили `API_KEY` в `.env`, подставьте его значение в запрос. Для каждого нового платежа используйте новый `Idempotency-Key`; при повторе того же запроса оставляйте прежний ключ.

Создание вернёт `202 Accepted` и `payment_id`. Обработка асинхронная: через несколько секунд запросите платёж по этому ID:

```bash
curl http://localhost:8000/api/v1/payments/ВСТАВЬТЕ_PAYMENT_ID \
  -H 'X-API-Key: local-development-key-change-me'
```

В ответе проверьте `status` (`succeeded` или `failed`) и `webhook_delivered_at`. Локальный webhook-приёмник пишет полученные уведомления в лог сервиса `webhook`.
