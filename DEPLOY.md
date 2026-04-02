# Deploy

## Первый запуск

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd Bitrix-Migration

# 2. Создать .env (или отредактировать существующий)
# Обязательно задать POSTGRES_PASSWORD
cat .env
# POSTGRES_DB=odoo
# POSTGRES_USER=odoo
# POSTGRES_PASSWORD=<your_password>

# 3. Собрать образ и инициализировать базу данных
docker compose up -d --build db
docker compose --profile init run --rm odoo-init

# 4. Запустить Odoo
docker compose up -d odoo

# 5. Дождаться готовности
docker compose logs -f odoo
# Ждать строку: "odoo.service.server: HTTP service (werkzeug) running on ..."
```

Odoo UI: [http://localhost:8079](http://localhost:8079)

## Установка модуля

1. Settings → Apps → Remove "Apps" filter → Search `bitrix_migration` → Install.

## Обновление модуля после изменений кода

```bash
# Только Python-файлы (без изменений XML/manifest)
docker compose restart odoo

# С изменениями XML, views или manifest
docker compose exec odoo odoo -u bitrix_migration -d odoo --stop-after-init
docker compose restart odoo
```

## Переменные окружения

| Переменная | По умолчанию | Обязательная |
|---|---|---|
| `POSTGRES_DB` | `odoo` | нет |
| `POSTGRES_USER` | `odoo` | нет |
| `POSTGRES_PASSWORD` | — | **да** |