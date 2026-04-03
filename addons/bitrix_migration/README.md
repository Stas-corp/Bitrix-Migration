# Bitrix24 Migration (Odoo Module)

Короткая инструкция по настройке и запуску модуля `bitrix_migration`.

## 1) Требования

- Odoo 19 CE.
- Зависимости Odoo-модуля: `project`, `mail`, `calendar`, `hr`, `auth_signup`.
- Python-пакеты: `pymysql`, `pydantic`, `paramiko`.
- Доступ к MySQL базе Bitrix24.
- (Опционально) SFTP-доступ к файловому хранилищу Bitrix для вложений.

## 2) Установка модуля

1. Перезапустите Odoo после добавления кода модуля в `addons`.
2. В Odoo: `Settings -> Apps`.
3. Уберите фильтр `Apps`, найдите `bitrix_migration`, нажмите `Install`.

Если менялись XML/view/manifest:

```bash
odoo -u bitrix_migration -d <db_name> --stop-after-init
```

## 3) Где запускать миграцию

- Меню: `Bitrix Migration`.
- Используется единая запись `Bitrix Migration` (singleton form).

## 4) Настройка перед запуском

Заполните в форме:

- `MySQL Host`, `MySQL Port`, `MySQL User`, `MySQL Password`, `MySQL Database`.
- `Import From Date` (опционально, чтобы ограничить импорт по дате).
- `Fallback Project (no-project tasks)`:
  - если пусто, будет авто-создан проект `Bitrix: Без проекта`.
  - для задач Bitrix без проекта (`GROUP_ID=0/NULL`) используется именно fallback-проект.
- `SFTP Settings` заполняйте только если переносите вложения.

## 5) Рекомендуемый порядок запуска

1. `Dry Run` -> `Run Migration` (проверить объёмы и доступы).
2. `Full Migration` -> `Run Migration`.
3. При необходимости создайте пользователей сотрудников:
   - `Create Employee Users`
   - `Send Password Reset`

## 6) Логика задач без проекта (актуальная)

- Все no-project задачи импортируются в fallback-проект.
- Для fallback-проекта автоматически поддерживаются 6 стадий:
  - `Чекає виконання`
  - `Виконується`
  - `Чекає контролю`
  - `Відкладене`
  - `Завершене`
  - `Скасована`
- Маппинг по `b_tasks.STATUS`:
  - `1,2 -> Чекає виконання`
  - `3 -> Виконується`
  - `4 -> Чекає контролю`
  - `5 -> Завершене`
  - `6 -> Відкладене`
  - `7 -> Скасована`

## 7) One-off для старых personal-проектов

Если ранее использовалась схема personal-проектов:

- Нажмите `Merge Personal Projects`.
- Действие:
  - переносит задачи из `x_bitrix_type='personal'` в fallback-проект;
  - ремапит стадии по `STATUS`;
  - деактивирует personal-проекты.

## 8) Полезные кнопки

- `Run Migration` — запуск выбранного режима.
- `Reset` — сброс checkpoint-ов.
- `Purge Imported Data` — удаление импортированных сущностей (осторожно).

## 9) Быстрая проверка после запуска

- В логе нет блока `ERROR`.
- `Tasks without project` в reconciliation = `0`.
- Для fallback-проекта есть 6 ожидаемых стадий.
