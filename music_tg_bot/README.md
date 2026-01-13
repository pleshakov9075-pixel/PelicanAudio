# Music TG Bot

Полностью готовый Telegram-бот с архитектурой **bot + api + worker**, PostgreSQL, Redis, миграциями и интеграциями GenAPI + YooKassa.

## Быстрый старт

1) Скопируйте шаблон окружения:

```bash
cp .env.example .env
```

2) Заполните `.env`:
- `BOT_TOKEN` — токен вашего Telegram-бота.
- `GENAPI_API_KEY` — ключ GenAPI.

3) (Опционально) Добавьте ключи YooKassa позже:
- `YOOKASSA_SHOP_ID`
- `YOOKASSA_SECRET_KEY`
- `YOOKASSA_RETURN_URL`

4) Запуск:

```bash
docker compose up -d --build
```

## Healthcheck

Проверка статуса API:

```bash
curl http://localhost:8000/health
```

## Webhook YooKassa

После появления ключей YooKassa настройте webhook на:

```
POST {BASE_URL}/api/payments/yookassa/webhook
```

Где `BASE_URL` — ваш публичный адрес API.

## Что делает бот

- 3 бесплатные генерации текста в день (включая правки).
- После лимита — текст за 19 ₽.
- Генерация аудио по тарифам из `presets.yaml`.
- Баланс в рублях (целые значения).

## Структура

```
music_tg_bot/
  app/
    core/
    integrations/
    presets/
    bot/
    api/
    worker/
  migrations/
  docker-compose.yml
  Dockerfile
  .env.example
```
