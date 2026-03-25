# План реализации: Модуль миграции Bitrix24 → Odoo 19 CE

## Context

Необходимо перенести данные из Bitrix24 в Odoo 19 Community Edition. Источник содержит: 96 проектов, ~135K задач, 214 тегов, 535 стадий, ~418K комментариев, 5.8K встреч, ~126K вложений. Существующий MigrationPlan.md полностью описывает архитектуру, маппинги и порядок загрузки — нужно реализовать кодовую базу по этому ТЗ.

Подход: кастомный Odoo-модуль `bitrix_migration`, запускаемый через `odoo-bin shell`. Staging-слой на Pydantic DTO, batch ORM-загрузка, идемпотентность через `x_bitrix_*` поля + mapping-таблицы.

---

## Структура файлов для создания

```
addons/bitrix_migration/
├── __manifest__.py
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── bitrix_mapping.py          # Persistent mapping tables
│   ├── project_project.py         # x_bitrix_* поля на project.project
│   ├── project_task.py            # x_bitrix_* поля на project.task
│   ├── project_task_type.py       # x_bitrix_* поля на project.task.type
│   └── calendar_event.py          # x_bitrix_* поля на calendar.event
├── services/
│   ├── __init__.py
│   ├── extractors/
│   │   ├── __init__.py
│   │   └── bitrix_mysql.py        # MySQL-коннектор + SQL запросы из README
│   ├── normalizers/
│   │   ├── __init__.py
│   │   └── dto.py                 # Pydantic DTO: BitrixProject, Task, Stage, Tag, Comment, Meeting, Attachment
│   └── loaders/
│       ├── __init__.py
│       ├── base.py                # BaseLoader: batch + commit + checkpoint
│       ├── users.py               # Bitrix user → Odoo user/partner mapping
│       ├── tags.py                # project.tags (дедупликация по name)
│       ├── projects.py            # project.project
│       ├── stages.py              # project.task.type (только G-стадии)
│       ├── tasks.py               # project.task (pass 1: без parent)
│       ├── tasks_relink.py        # pass 2: parent_id links
│       ├── comments.py            # mail.message в chatter задач
│       ├── attachments.py         # ir.attachment (задачи + комментарии)
│       ├── meetings.py            # calendar.event
│       └── meeting_comments.py    # chatter встреч
├── scripts/
│   ├── run_full_migration.py      # Полный прогон в правильном порядке
│   ├── run_dry_run.py             # Dry-run: считает объёмы, не пишет в Odoo
│   └── run_pilot.py              # Пилот: 3-5 проектов
└── data/
    └── security/ir.model.access.csv
```

---

## Шаги реализации

### Шаг 1 — Scaffold модуля
- `__manifest__.py` с depends: `['project', 'mail', 'calendar']`
- `__init__.py` подключающий все models/
- `ir.model.access.csv` с правами на mapping-модели

### Шаг 2 — Mapping models (`models/bitrix_mapping.py`)
Создать модель `bitrix.migration.mapping` с полями:
- `bitrix_id` (Char, required)
- `odoo_model` (Char)
- `odoo_id` (Integer)
- `entity_type` (Selection: project/task/stage/tag/comment/meeting/attachment/user)

Используется для идемпотентного lookup на каждом шаге.

### Шаг 3 — Кастомные поля (models/*.py)
**project.project**: `x_bitrix_id`, `x_bitrix_type` (project/workgroup), `x_bitrix_closed`, `x_bitrix_owner_bitrix_id`
**project.task**: `x_bitrix_id`, `x_bitrix_stage_id`, `x_bitrix_parent_id`
**project.task.type**: `x_bitrix_id`, `x_bitrix_entity_id`
**calendar.event**: `x_bitrix_id`
Для `mail.message` использовать существующее поле или добавить `x_bitrix_message_id`

### Шаг 4 — MySQL Extractor (`services/extractors/bitrix_mysql.py`)
- Коннектор к MySQL Bitrix через `pymysql` или `mysql-connector-python`
- Методы: `get_projects()`, `get_tasks()`, `get_stages()`, `get_tags()`, `get_comments()`, `get_meetings()`, `get_attachments()`
- SQL-запросы берутся из README.md (уже написаны и проверены)

### Шаг 5 — Pydantic DTO (`services/normalizers/dto.py`)
Модели: `BitrixProject`, `BitrixTask`, `BitrixStage`, `BitrixTag`, `BitrixComment`, `BitrixMeeting`, `BitrixAttachment`
- Нормализация дат в `datetime` объекты Python
- Чистка NULL → None
- Валидация обязательных полей

### Шаг 6 — BaseLoader (`services/loaders/base.py`)
```python
class BaseLoader:
    batch_size: int
    def load_batch(self, records): ...
    def get_or_create(self, model, bitrix_id, vals): ...  # идемпотентность
    def commit_checkpoint(self, processed_count): ...
    def log_stats(self): ...
```

### Шаг 7 — Loaders по порядку миграции

**users.py**: Загрузить mapping `Bitrix user_id → res.users.id + res.partner.id` (пользователи уже должны существовать в Odoo или создаются вручную до миграции)

**tags.py**: `project.tags`, `create_or_get` по `name` (case-insensitive)

**projects.py**: `project.project` из `BitrixProject`, сохранить mapping

**stages.py**: `project.task.type` только ENTITY_TYPE='G', привязать к project через `project_ids` M2M

**tasks.py** (pass 1): Создать все задачи без `parent_id`, сохранить mapping

**tasks_relink.py** (pass 2): Для задач с `PARENT_ID != NULL` — обновить `parent_id` через mapping

**comments.py**: Создать `mail.message` в chatter задачи. Использовать `message_post` или прямую запись в ORM. Только `SERVICE_TYPE IS NULL AND NEW_TOPIC = 'N'`

**attachments.py**: Читать файлы по SFTP с Bitrix-сервера, создавать `ir.attachment` с `res_model` + `res_id`

**meetings.py**: `calendar.event` только из записей `ID = PARENT_ID`, участники из дочерних записей

**meeting_comments.py**: chatter `calendar.event` из `b_sonet_log_comment`

### Шаг 8 — Scripts

**run_dry_run.py**: Extractor → DTO → считать объёмы + missing mappings, 0 записей в Odoo

**run_pilot.py**: Загрузить 3-5 проектов с их задачами, комментариями, вложениями

**run_full_migration.py**: Последовательный вызов всех loaders в правильном порядке с checkpoint-resume

---

## Порядок выполнения загрузки

1. users (mapping)
2. tags
3. projects
4. stages
5. tasks pass 1 (без parent)
6. tasks pass 2 (relink parents)
7. comments
8. attachments (задачи)
9. attachments (комментарии)
10. meetings
11. meeting_comments
12. reconciliation report

---

## Критические файлы источника

- [README.md](README.md) — все SQL-запросы к Bitrix MySQL
- [MigrationPlan.md](MigrationPlan.md) — полное ТЗ с маппингами и правилами
- [FileMigration.md](FileMigration.md) — подход к загрузке файлов через SFTP

---

## Верификация

1. **Dry-run**: запустить `run_dry_run.py` — должен вывести counts всех сущностей без ошибок
2. **Pilot**: запустить `run_pilot.py` на staging Odoo — проверить 3-5 проектов вручную в UI
3. **Reconciliation**: сравнить counts Bitrix vs Odoo через `reconciliation_report()`
4. **Acceptance**: все критерии из раздела 14 MigrationPlan.md (99.5% задач с проектом, нет дублей при повторном запуске, chatter без системного мусора)

---

## Зависимости Python

```
pymysql          # MySQL extractor
pydantic>=2      # DTO/normalizers
paramiko         # SFTP для файлов
```

Устанавливаются в venv Odoo или через `pip install` на сервере.
