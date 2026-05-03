# Webhook Service

FastAPI-сервис, который принимает GitHub App webhooks и запускает deployments для подходящих project environments.

## Назначение

- Принимает GitHub webhook events.
- Проверяет `X-Hub-Signature-256` через настроенный webhook secret.
- Обрабатывает `push` events.
- Сопоставляет repository URL и branch с окружением через `api-gateway`.
- Создает internal deployment requests через `api-gateway`.
- Игнорирует webhook events для репозиториев или веток, которые не зарегистрированы в системе.

## HTTP API

- `GET /healthz` - проверка состояния сервиса.
- `POST /api/v1/webhooks/github` - endpoint для GitHub App webhook.

GitHub App webhook URL:

```text
https://webhook.xn--d1acmhpe.tech/api/v1/webhooks/github
```

## Конфигурация

Настройки читаются из переменных окружения с префиксом `WEBHOOK_`. Для вложенных полей используется разделитель `__`.

Основные переменные:

- `WEBHOOK_SERVER__PORT`
- `WEBHOOK_GATEWAY__BASE_URL`
- `WEBHOOK_AUTH__JWT_SECRET`
- `WEBHOOK_GITHUB__WEBHOOK_SECRET`

## Настройки GitHub App

Рекомендуемые webhook settings:

- Payload URL: `https://webhook.xn--d1acmhpe.tech/api/v1/webhooks/github`
- Content type: `application/json`
- Secret: то же значение, что и `WEBHOOK_GITHUB__WEBHOOK_SECRET`.
- Events: `Push`.

Сервису не нужны installation lifecycle events. Он реагирует только на repository push events.

## Локальный запуск

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --host 0.0.0.0 --port 8003
```

Запуск тестов:

```bash
pytest -q
```

## Деплой

Helm chart находится в `charts/webhook-service`. Сервис должен быть доступен снаружи через ingress, чтобы GitHub мог отправлять webhooks.
