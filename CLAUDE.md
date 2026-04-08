# Bitrix Migration — CLAUDE.md

## Что это

Odoo 19 CE addon `bitrix_migration` для одноразовой миграции данных из Bitrix24 (MySQL) в Odoo.
Технологический стек: Python 3.12, Odoo 19, PostgreSQL 16, PyMySQL, Pydantic v2, Paramiko.
Docker: Odoo доступен на `http://localhost:8079`.

## Архитектура

```
addons/bitrix_migration/
├── models/
│   ├── bitrix_migration_run.py   # Главная модель: настройки, запуск, кнопки UI
│   ├── bitrix_mapping.py         # Таблица соответствий Bitrix ID → Odoo ID
│   ├── hr_department.py          # x_bitrix_id на hr.department
│   ├── hr_employee.py            # x_bitrix_id, x_bitrix_telegram на hr.employee
│   ├── mail_message.py           # x_bitrix_id, x_bitrix_author_employee_id
│   ├── project_project.py        # x_bitrix_id на project.project
│   ├── project_task.py           # x_bitrix_id, x_bitrix_created_at, x_bitrix_responsible_employee_ids
│   ├── project_task_type.py      # x_bitrix_id на project.task.type (стадии)
│   └── calendar_event.py         # x_bitrix_id на calendar.event
├── services/
│   ├── extractors/
│   │   └── bitrix_mysql.py       # Читает данные из MySQL Bitrix
│   ├── loaders/
│   │   ├── base.py               # BaseLoader: get_or_create, checkpoint, db introspection
│   │   ├── projects.py
│   │   ├── stages.py
│   │   ├── tags.py
│   │   ├── tasks.py
│   │   ├── tasks_relink.py       # Второй проход: parent_id + cycle detection
│   │   ├── comments.py
│   │   ├── attachments.py        # SFTP → Odoo ir.attachment (compound key, comment linking)
│   │   ├── users.py
│   │   ├── departments.py
│   │   ├── employees.py          # + SFTP avatar sync
│   │   └── meetings.py           # MeetingLoader → calendar.event
│   └── normalizers/
│       ├── dto.py                # Pydantic DTOs: BitrixProject, BitrixTask, BitrixMeeting, ...
│       └── bitrix_markup.py      # BBCode → HTML конвертер для Bitrix markup
└── views/
    ├── bitrix_migration_run_views.xml
    ├── hr_employee_views.xml
    └── project_task_views.xml
```

## Ключевые паттерны

### Маппинг ID
`bitrix.migration.mapping` — единственный источник истины для соответствия Bitrix ID → Odoo ID.
Всегда используй `get_mapping().set_mapping(bitrix_id, entity_type, model_name, odoo_id)` после создания записи.
`entity_type` — строго из списка: `project, task, stage, tag, comment, attachment, user, meeting, department, employee`.

### BaseLoader
Все загрузчики наследуют `BaseLoader`. Ключевые методы:
- `get_or_create(model, domain, vals, bitrix_id, entity_type)` — идемпотентное создание
- `commit_checkpoint(count, last_bitrix_id)` — коммит + сохранение прогресса в `ir.config_parameter`
- `get_checkpoint()` — чтение last processed id для resume
- `db_table_exists(table)` / `db_column_exists(table, col)` — introspection с кешем
- `find_employee_by_bitrix_id(bitrix_user_id, employee_map)` — lookup hr.employee
- `log_once(key, message)` — дедупликация предупреждений

### Экстрактор
`BitrixMySQLExtractor` принимает `date_from` для фильтрации. SQL-запросы — шаблоны с `{task_where_clause}` / `{project_where_clause}`. Колонка даты определяется автоматически через `_get_existing_mysql_column()`.

### Режимы запуска (`mode`)
| Режим | Что делает |
|---|---|
| `dry_run` | Только считает, ничего не пишет |
| `pilot` | 3–5 проектов из `pilot_project_ids` |
| `full` | Полная миграция |
| `projects_only` | Только проекты |
| `relink` | Только tasks_relink (parent_id) |
| `comments` | Только комментарии |
| `single_task` | Одна задача по `single_task_bitrix_id` |
| `hr` | Отделы + сотрудники |
| `departments_only` | Только отделы |
| `employees_only` | Только сотрудники |
| `fix_roles` | Пересинхронизация ролей у уже импортированных задач |
| `meetings` | Только зустрічі (calendar.event) |

### Создание пользователей
После импорта сотрудников: кнопки на форме `BitrixMigrationRun`:
1. **Create Employee Users** — пакетное создание `res.users` (group_portal или group_user)
2. **Create Test Employee User** — один сотрудник из `test_employee_id`
3. **Send Password Reset** — рассылка писем сброса пароля
4. **Purge Imported Data** — удалить всё импортированное (с подтверждением)

### DTO (dto.py)
Pydantic v2. Все поля валидируются через `field_validator`. Ноль / пустая строка / 'NULL' → `None` через `_clean_str()` и `_to_int_or_none()`. PHP-сериализованные массивы (`a:N:{...}`) парсит `parse_php_int_array()`.

## Кастомные поля Odoo (добавлены модулем)

| Модель | Поле | Тип | Назначение |
|---|---|---|---|
| `project.project` | `x_bitrix_id` | Integer | Bitrix ID проекта |
| `project.task` | `x_bitrix_id` | Integer | Bitrix ID задачи |
| `project.task` | `x_bitrix_created_at` | Datetime | Оригинальная дата создания |
| `project.task` | `x_bitrix_responsible_employee_ids` | Many2many hr.employee (computed) | Відповідальний (Bitrix TYPE='R') |
| `project.task` | `x_bitrix_accomplice_employee_ids` | Many2many hr.employee (computed) | Співвиконавці (Bitrix TYPE='A') |
| `project.task` | `x_bitrix_auditor_employee_ids` | Many2many hr.employee (computed) | Наглядачі (Bitrix TYPE='U') |
| `project.task` | `x_bitrix_originator_employee_id` | Many2one hr.employee (computed) | Постановник (Bitrix TYPE='O') |
| `project.task` | `x_bitrix_creator_employee_id` | Many2one hr.employee | Автор (CREATED_BY) |
| `project.task.type` | `x_bitrix_id` | Integer | Bitrix ID стадии |
| `hr.employee` | `x_bitrix_id` | Integer | Bitrix user ID |
| `hr.employee` | `x_bitrix_telegram` | Char | Telegram-логин |
| `hr.department` | `x_bitrix_id` | Integer | Bitrix dept ID |
| `mail.message` | `x_bitrix_id` | Integer | Bitrix comment ID |
| `mail.message` | `x_bitrix_message_id` | Integer | Bitrix forum_message_id (для прив'язки вкладень) |
| `mail.message` | `x_bitrix_author_employee_id` | Many2one hr.employee | Автор-сотрудник |
| `calendar.event` | `x_bitrix_id` | Integer | Bitrix meeting ID |

## Маппинг ролей Bitrix → Odoo

| Bitrix TYPE | Bitrix роль | Odoo поле | role в link-таблице | Попадает в user_ids? |
|---|---|---|---|---|
| `R` | Відповідальний | `x_bitrix_responsible_employee_ids` | `responsible` | Да |
| `A` | Співвиконавець | `x_bitrix_accomplice_employee_ids` | `accomplice` | Да |
| `U` | Наглядач | `x_bitrix_auditor_employee_ids` | `auditor` | Нет |
| `O` | Постановник | `x_bitrix_originator_employee_id` | `originator` | Нет |
| `CREATED_BY` | Автор/Creator | `x_bitrix_creator_employee_id` | — (прямое Many2one) | Нет |

Все роли хранятся в `bitrix.task.employee.link` (task_id, employee_id, role) с UNIQUE constraint.
В Odoo `user_ids` содержит **только** відповідального + співвиконавців. Наглядачі, постановник и автор туда не попадают.

## Нормализация Bitrix markup

Модуль `services/normalizers/bitrix_markup.py` конвертирует BBCode-подобную разметку Bitrix в HTML для Odoo.
Применяется к: описаниям задач, описаниям проектов, телам комментариев, описаниям зустрічей.

Основные преобразования:
- `[USER=ID]Name[/USER]` → `<strong>Resolved Name</strong>` (через маппинг hr.employee.x_bitrix_id → name)
- `[B]`, `[I]`, `[U]`, `[S]` → `<strong>`, `<em>`, `<u>`, `<s>`
- `[URL=href]text[/URL]` → `<a href="href">text</a>`
- `[LIST][*]item[/LIST]` → `<ul><li>item</li></ul>`
- `[CODE]...[/CODE]` → `<pre><code>...</code></pre>`
- `[DISK FILE ID=N]` → удаляется (файлы обрабатываются через attachments)
- Переносы строк → `<br/>` (кроме блоков `<pre>`)

`build_employee_name_map(env)` строит словарь `{str(bitrix_user_id): name}` из hr.employee для резолва `[USER=]` тегов.

## Вкладення (attachments)

Ключ ідемпотентності: `{entity_type}:{entity_id}:{file_path}` (compound key).
Вкладення коментарів прив'язуються до `mail.message` через `x_bitrix_message_id = forum_message_id`.
Якщо відповідний `mail.message` не знайдено — fallback на `project.task`.

## Аватарки співробітників

`EmployeeLoader.sync_avatars()` завантажує фото через SFTP з `b_user.PERSONAL_PHOTO → b_file`.
Політика: фото встановлюється тільки якщо `image_1920` порожнє (безпечний rerun).

## Архівування закритих проєктів

Проєкти з `x_bitrix_closed=True` автоматично отримують `active=False` в Odoo (архівування).
Це відбувається в `ProjectLoader` після створення/оновлення запису.

## Важные нюансы

- Все записи создаются через `sudo()` с контекстом `mail_create_nolog=True, mail_create_nosubscribe=True, tracking_disable=True` — иначе Odoo создаёт лишние сообщения.
- `_append_log()` делает `env.cr.commit()` после каждой записи — это нормально для длинных миграций.
- `action_reset()` вызывает `_clear_checkpoints()` перед сбросом состояния.
- Стадии в Bitrix могут иметь ключ `'ID'` или `'id'` — экстрактор нормализует через `AS id`.
- Cycle detection в `tasks_relink.py` — `_has_parent_cycle()` обходит `parent_map` до нахождения цикла или корня.
- `get_partner_from_employee()`: приоритет `work_contact_id` → `user_id.partner_id` → `address_home_id`.

## Среда разработки

```bash
# Запуск
docker compose up -d

# Логи Odoo
docker compose logs -f odoo

# Перезапуск после изменений в Python
docker compose restart odoo

# Odoo shell
docker compose exec odoo odoo shell -d odoo
```

Odoo UI: http://localhost:8079
Модуль обновляется через Settings → Apps → Upgrade или `-u bitrix_migration`.

## Тесты

```bash
# Запуск тестов модуля
docker compose exec odoo odoo --test-enable -d odoo -u bitrix_migration --stop-after-init
```

Тесты находятся в `tests/`:
- `test_role_mapping.py` — маппинг ролей Bitrix → Odoo (Этап 1)
- `test_markup_normalizer.py` — BBCode → HTML конвертация (Этап 2)
- `test_attachments.py` — ідемпотентність вкладень, прив'язка до коментарів (Этап 2)
- `test_meetings.py` — створення calendar.event з даними зустрічей (Этап 2)
