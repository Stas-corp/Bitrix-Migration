# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Odoo 19 CE addon `bitrix_migration` для одноразовой миграции данных из Bitrix24 (MySQL) в Odoo.
Технологический стек: Python 3.12, Odoo 19, PostgreSQL 16, PyMySQL, Pydantic v2, Paramiko.
Docker: Odoo доступен на `http://localhost:8079`.

В репозитории живут **три** аддона (`addons/`):
- `bitrix_migration` — основной модуль миграции (всё ниже относится к нему).
- `odoo_document_management_cloud_sync` — сторонний платный DMS (File Manager + Cloud Sync). Используется как назначение для режима `disk`. Не редактировать без необходимости.
- `dms_access_fix` — собственный модуль (`auto_install=True`, `post_init_hook`), закрывающий ACL-утечку `base.group_user` в DMS: делает шеринг для Document User read-only. Зависит от `odoo_document_management_cloud_sync`.

## Архитектура

```
addons/bitrix_migration/
├── models/
│   ├── bitrix_migration_run.py        # Главная модель: настройки, запуск, кнопки UI (~2100 строк)
│   ├── bitrix_mapping.py              # Таблица соответствий Bitrix ID → Odoo ID
│   ├── bitrix_task_employee_link.py   # Роли на задачах: task_id, employee_id, role + UNIQUE constraint
│   ├── hr_department.py               # x_bitrix_id на hr.department
│   ├── hr_employee.py                 # x_bitrix_id, x_bitrix_telegram на hr.employee
│   ├── mail_message.py                # x_bitrix_id, x_bitrix_author_employee_id
│   ├── project_project.py             # x_bitrix_id на project.project
│   ├── project_task.py                # x_bitrix_id, роли (computed), x_bitrix_assignee_user_ids
│   ├── project_task_type.py           # x_bitrix_id на project.task.type (стадии)
│   └── calendar_event.py              # x_bitrix_id на calendar.event
├── services/
│   ├── extractors/
│   │   └── bitrix_mysql.py            # Читает данные из MySQL Bitrix
│   ├── loaders/
│   │   ├── base.py                    # BaseLoader: get_or_create, checkpoint, db introspection
│   │   ├── projects.py
│   │   ├── stages.py
│   │   ├── tags.py
│   │   ├── tasks.py                   # Сложная логика ролей, стадий, fallback-проекта
│   │   ├── tasks_relink.py            # Второй проход: parent_id + cycle detection
│   │   ├── comments.py
│   │   ├── attachments.py             # SFTP → Odoo ir.attachment (compound key, comment linking)
│   │   ├── users.py
│   │   ├── departments.py
│   │   ├── employees.py               # + SFTP avatar sync, job_id sync, archive_fired
│   │   ├── hierarchy.py               # Pure helpers: dept tree → employee.parent_id (UF_HEAD)
│   │   ├── meetings.py                # MeetingLoader → calendar.event
│   │   └── disk.py                    # DiskLoader: Bitrix Disk → Odoo Documents (DMS)
│   └── normalizers/
│       ├── dto.py                     # Pydantic DTOs: BitrixProject, BitrixTask, BitrixMeeting, ...
│       └── bitrix_markup.py           # BBCode → HTML конвертер для Bitrix markup
├── data/
│   └── ir_cron_data.xml               # 3 крона: avatar / attachment / full-migration батчи
├── security/
│   └── bitrix_task_employee_security.xml
└── views/
    ├── bitrix_migration_run_views.xml
    ├── hr_employee_views.xml
    └── project_task_views.xml
```

## Среда разработки

```bash
# Запуск
docker compose up -d

# Логи Odoo
docker compose logs -f odoo

# Перезапуск после изменений в Python
docker compose restart odoo

# Применить XML/структурные изменения модуля
docker compose run --rm odoo odoo -d odoo -u bitrix_migration --stop-after-init

# Odoo shell
docker compose exec odoo odoo shell -d odoo
```

Odoo UI: http://localhost:8079

## Тесты

```bash
# Запуск тестов модуля
docker compose exec odoo odoo --test-enable -d odoo -u bitrix_migration --stop-after-init
```

Тесты находятся в `tests/`:
- `test_role_mapping.py` — маппинг ролей Bitrix → Odoo, UNIQUE constraint на responsible
- `test_markup_normalizer.py` — BBCode → HTML конвертация
- `test_attachments.py` — ідемпотентність вкладень, прив'язка до коментарів
- `test_meetings.py` / `test_meeting_attachments.py` / `test_meeting_comments.py` — calendar.event, вкладення і коментарі зустрічей
- `test_departments.py` — ієрархія відділів, прив'язка керівника
- `test_employee_hierarchy.py` — обчислення `parent_id` з dept tree / UF_HEAD (pure helpers з `hierarchy.py`)
- `test_employee_job.py` — синхронізація `job_id` / `job_title` з WORK_POSITION
- `test_fired_employees.py` — уволені створюються `active=True`, архівуються тільки в `archive_fired()`
- `test_disk_filters.py` — фільтри Bitrix Disk
- `test_purge_noise.py` — чистка мусорних аккаунтів

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
- `recompute_task_user_ids(task)` — единый пересчёт `user_ids` из canonical responsible + accomplices

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
| `hr` | Отделы + сотрудники (ВСЕ создаются как `active=True`, включая уволенных) |
| `archive_employees` | Финальный шаг: архивирование уволенных сотрудников + их `res.users` |
| `fix_roles` | Пересинхронизация ролей у уже импортированных задач |
| `fix_attachments` | Repair: перелінковка comment attachments з `project.task` на `mail.message` |
| `fix_descriptions` | Восстановление пустых описаний задач из Bitrix DISK FILE placeholders |
| `fix_hierarchy` | Пересчёт `hr.employee.parent_id` по Bitrix UF_HEAD |
| `fix_job_titles` | Синхронізація `hr.employee.job_id` / `job_title` з `b_user.WORK_POSITION` без повної перезагрузки HR |
| `meetings` | Только зустрічі (calendar.event) |
| `disk` | Bitrix Disk → Odoo Documents |

> `departments_only` / `employees_only` / `purge_noise` убраны из Selection (2026-05-19). Чистка «noise»-аккаунтов выполняется только через кнопки **Purge Noise — Preview/Apply** в Danger Zone; отдельный импорт подразделений или сотрудников запускается комбинированным режимом `hr` (двухпроходный загрузчик уже обрабатывает каждую часть идемпотентно).

### Создание пользователей
После импорта сотрудников — кнопки на форме `BitrixMigrationRun`:
1. **Create Employee Users** — пакетное создание `res.users` (group_portal или group_user)
2. **Create Test Employee User** — один сотрудник из `test_employee_id`
3. **Send Password Reset** — рассылка писем сброса пароля
4. **Purge Imported Data** / **Purge HR Data** — удалить всё импортированное (с подтверждением)

### Канонический порядок миграции

Уволенные сотрудники должны остаться видимыми в `project.task.user_ids` («Виконавці») и `calendar.event.partner_ids`. Для этого они импортируются как **активные** и архивируются только в самом конце:

1. **Mode `hr`** — импорт отделов и сотрудников. `EmployeeLoader.run()` создаёт ВСЕХ (включая `ACTIVE='N'`) как `hr.employee.active=True`. `_build_employee_vals` намеренно не выставляет `active=False` — это контракт.
2. **Кнопка «Create Employee Users»** — создаёт `res.users` для всех импортированных `hr.employee`. На этом шаге все сотрудники ещё активны, поэтому `_ensure_user_for_employee` создаёт активные учётки автоматически.
3. **Mode `full`** — проекты/задачи/комментарии/встречи. Уволенные попадают в `project.task.user_ids` через стандартный механизм, потому что у них уже есть активный `res.users`.
4. **Mode `archive_employees`** — `EmployeeLoader.archive_fired()` ставит `active=False` и на `hr.employee`, и на связанных `res.users` (с защитой системных и общих с активными сотрудниками). Ссылки `task.user_ids.ids` остаются нетронутыми — Many2many продолжает хранить ID архивных users, и виджет Odoo отрисовывает их с пометкой archived.

**Важно:** `Send Password Reset` запускать ПОСЛЕ `archive_employees`, иначе письма со сбросом пароля могут уйти уволенным.

**Idempotency.** Повторный запуск `hr` НЕ реактивирует уже архивированных: `_build_employee_vals` не пишет ключ `active`, поэтому downgrade-guard в `_prepare_update_vals` никогда не получает True→True. Активация требует ручного действия или удаления mapping + повторного `hr` на чистой записи.

### Danger Zone (форма `bitrix.migration.run`)

Деструктивные операции, все с подтверждением и логированием:

| Кнопка | Что делает |
|---|---|
| **Purge Imported Data** | Удаляет мигрированные сущности (проекты, задачи, стадии, комментарии, встречи, вкладення, теги) + mappings + checkpoints. HR не трогается. |
| **Purge HR Data** | Удаляет employees + departments + связанные res.users / res.partner. Защищённые / всё ещё связанные SKIP в лог. |
| **Purge Noise — Preview/Apply** | Двухшаговая чистка мусорных аккаунтов из Bitrix без `UF_DEPARTMENT` (imconnector_*, боты, B24 Network guests). Reassign `mail.message.author_id` на OdooBot. |
| **Purge Orphan Contacts — Preview/Apply** | Удаляет `res.partner` без привязки к `hr.employee` (active/archived). Перед `unlink()` ссылки из `mail.message.author_id` переписываются на OdooBot (`base.partner_root`), `mail.followers` / `calendar.event.partner_ids` / `calendar.attendee` — удаляются. Защищены: компании, `base.partner_root` / `base.public_partner` / `base.main_partner`, internal users (`share=False`), active users, шаблоны (login в `default`, `__system__`, `public`, `portaltemplate`, `portal_template`), родительские контакты (`child_ids`). Active portal-юзеры дополнительно защищены native Odoo-проверкой → SKIP. |

### DTO (dto.py)
Pydantic v2. Все поля валидируются через `field_validator`. Ноль / пустая строка / 'NULL' → `None` через `_clean_str()` и `_to_int_or_none()`. PHP-сериализованные массивы (`a:N:{...}`) парсит `parse_php_int_array()`.

## Кастомные поля Odoo (добавлены модулем)

| Модель | Поле | Тип | Назначение |
|---|---|---|---|
| `project.project` | `x_bitrix_id` | Integer | Bitrix ID проекта |
| `project.task` | `x_bitrix_id` | Integer | Bitrix ID задачи |
| `project.task` | `x_bitrix_created_at` | Datetime | Оригинальная дата создания |
| `project.task` | `x_bitrix_responsible_employee_id` | Many2one hr.employee (computed) | Канонічний відповідальний (Bitrix TYPE='R') |
| `project.task` | `x_bitrix_responsible_employee_ids` | Many2many hr.employee (computed, deprecated) | Deprecated mirror: 0/1 employee |
| `project.task` | `x_bitrix_accomplice_employee_ids` | Many2many hr.employee (computed) | Співвиконавці (Bitrix TYPE='A') |
| `project.task` | `x_bitrix_auditor_employee_ids` | Many2many hr.employee (computed) | Наглядачі (Bitrix TYPE='U') |
| `project.task` | `x_bitrix_originator_employee_id` | Many2one hr.employee (computed) | Постановник (Bitrix TYPE='O') |
| `project.task` | `x_bitrix_creator_employee_id` | Many2one hr.employee | Автор (CREATED_BY) |
| `project.task` | `x_bitrix_assignee_user_ids` | Many2many res.users (stored) | Канонічний набір виконавців (R+A → res.users, вкл. fallback через user_map) |
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
| `R` | Відповідальний | `x_bitrix_responsible_employee_id` (Many2one, canonical) | `responsible` | Да |
| `A` | Співвиконавець | `x_bitrix_accomplice_employee_ids` | `accomplice` | Да |
| `U` | Наглядач | `x_bitrix_auditor_employee_ids` | `auditor` | Нет |
| `O` | Постановник | `x_bitrix_originator_employee_id` | `originator` | Нет |
| `CREATED_BY` | Автор/Creator | `x_bitrix_creator_employee_id` | — (прямое Many2one) | Нет |

Все роли хранятся в `bitrix.task.employee.link` (task_id, employee_id, role) с UNIQUE constraint + partial unique index на `(task_id) WHERE role = 'responsible'`.

`x_bitrix_assignee_user_ids` — канонічний набір виконавців (`res.users`), резолвиться з `R + A` через `hr.employee → user` + fallback через `user_map`. `user_ids` — дзеркало `x_bitrix_assignee_user_ids`.
Наглядачі, постановник і автор **не** потрапляють у `user_ids` / `x_bitrix_assignee_user_ids`.

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

Ключ ідемпотентності:
- Task attachments: `task:{task_external_id}:{file_path}`
- Comment attachments: `comment:{task_external_id}:{forum_message_id}:{file_path}`

Legacy plain `file_path` keys продовжують читатися для backward-safe skip.
Вкладення коментарів прив'язуються до `mail.message` через `x_bitrix_message_id = forum_message_id`.
Якщо відповідний `mail.message` не знайдено — fallback на `project.task`.
Attachment SQL-запити фільтруються тим же `task_where_clause`, що й задачі/коментарі.

## Фонові стадії (cron)

Довгі стадії винесені у cron-воркери (`data/ir_cron_data.xml`, інтервал 1 хв), щоб обійти HTTP-таймаут `limit_time_real`. Усі реентерабельні: стан черги тримається у полях `bitrix.migration.run`, кожен тік обробляє батч у межах бюджету часу і коммітить прогрес.

| Cron | Метод | Стан-поля | Що робить |
|---|---|---|---|
| Avatar Batch | `_cron_process_avatar_batch()` | `avatar_sync_state`, `avatar_last_user_id`, `avatar_*_count` | По 20 аватарів/тік, бюджет 45 с |
| Attachment Batch | `_cron_process_attachment_batch()` | `attachment_sync_state`, `attachment_current_type/index`, `attachment_active_*` | SFTP-вкладення по типах task/comment/meeting/meeting_comment, з докачуванням великих файлів |
| Full Migration | `_cron_process_full_migration()` | `full_sync_state`, `full_sync_started_at` | Повна міграція у фоні (cron-driven), імунна до HTTP-таймауту |

## Аватарки співробітників

`EmployeeLoader.sync_avatars()` ставить у чергу SFTP-завантаження з `b_user.PERSONAL_PHOTO → b_file`; фактичне завантаження виконує cron `_cron_process_avatar_batch()` (див. таблицю вище).
Політика: фото встановлюється тільки якщо `image_1920` порожнє (безпечний rerun).

## Fallback-проект для задач без GROUP_ID

Близько 86% задач Bitrix мають `GROUP_ID=0` (без проекту). `TaskLoader` автоматично створює проект **"Bitrix: Без проекта"** з 6 фіксованими стадіями:
`Чекає виконання → Виконується → Чекає контролю → Відкладене → Завершене → Скасована`

Якщо проект вже існує — повторне створення пропускається (ідемпотентно).

## Reconciliation (пост-міграційний аудит)

`_run_reconciliation()` в `bitrix_migration_run.py` — комплексний звіт після міграції:
- Якість ролей: задачі без відповідального, розбіжності `user_ids` vs `x_bitrix_assignee_user_ids`
- Якість контенту: задачі з необробленою Bitrix-розміткою в описі
- Осиротілі записи: маппінги без відповідних Odoo-об'єктів
- Dept manager sync: відповідність `hr.department.manager_id` до Bitrix `UF_HEAD`

Запускається кнопкою **Run Reconciliation** на формі або вручну з Odoo shell.

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
- Каждый загрузчик коммитит независимо — безопасно продолжать с любого checkpoint после сбоя.
