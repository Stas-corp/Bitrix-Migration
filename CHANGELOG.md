# CHANGELOG

## 2026-05-13 / 2026-05-14 — Фикс доступов в Odoo Document Management

Задача: закрыть дыру в правах платного модуля `odoo_document_management_cloud_sync` (WebbyCrown, v19.0.0.1) — там любой Internal User Odoo получал полный CRUD на все папки и файлы. Конечная цель — расшаривание folder через `user_ids`/`group_ids` должно давать получателю **только read**; полный контроль остаётся у owner и Document Manager.

Работа шла в несколько раундов; ниже хронология и что осталось в коде.

---

### Round 1: первая попытка — правки прямо в платном модуле (на dev для отладки)

Без bridge-модуля, чтобы быстро убедиться что подход работает. Изменения локально, **в репозиторий не пушим** (директория модуля в `.gitignore`).

| Файл | Изменение | Причина |
|---|---|---|
| `addons/odoo_document_management_cloud_sync/__manifest__.py` | `version: 19.0.0.1 → 19.0.0.2`, убрана ссылка на `data/access_rules.xml` | Поднять версию для триггера migration; access_rules.xml перезаписывал ACL на `base.group_user` |
| `addons/odoo_document_management_cloud_sync/data/access_rules.xml` | **удалён** | Дублировал XML IDs из security/ с другим group_id (`base.group_user`), фактически открывал доступ всем Internal Users |
| `addons/odoo_document_management_cloud_sync/hooks/post_init_hook.py` | Убран блок (lines 43-82) программного создания ACL для `base.group_user` | Каждая переустановка модуля воссоздавала ту же дыру |
| `addons/odoo_document_management_cloud_sync/migrations/19.0.0.2/pre-migrate.py` (новый) | SQL cleanup: удалить все ACL `base.group_user` на `document.*` + zombie ACL предыдущих версий + stale broad `ir.rule` | ACL были помечены `noupdate=1`, upgrade сам по себе их не чистит |
| `addons/odoo_document_management_cloud_sync/security/document_security.xml` | Полностью переписан: `implied_ids` для Manager, отдельные ACL для Manager/User/Model User на 12 моделей, two-tier `ir.rule` (READ/CREATE broad + WRITE/UNLINK owner-only) | Two-tier — единственный способ дать broad read и узкий write одновременно |
| `addons/odoo_document_management_cloud_sync/models/document_folder.py` | Добавлен `can_manage_access` computed, ACL_FIELDS guard в `write()` | Защита от правки `user_ids`/`group_ids` не-owner'ом на уровне Python; computed для UI readonly |
| `addons/odoo_document_management_cloud_sync/views/document_management_views.xml` | `readonly="not can_manage_access"` на полях `user_ids`/`group_ids` | UX: получатель share не должен иметь возможность кликнуть «изменить» |

Все эти правки остались в **локальной копии** на dev-стенде. На прод они **не идут** — для прода сделан отдельный bridge-модуль (см. ниже).

**Проверено**: 9 smoke-сценариев в `odoo shell` — read shared OK, unlink/rename/edit-ACL → AccessError, create own + create file in shared OK, manager unlink any OK.

---

### Round 2: переупаковка в bridge-модуль `dms_access_fix`

Причина: платный модуль в `.gitignore`, на проде придёт чистый оригинал → дыра вернётся. Нужен переносимый модуль-надстройка, который через `depends + auto_install` подцепится к оригиналу и закроет дыру через override его XML IDs.

Создан новый модуль **`addons/dms_access_fix/`**:

| Файл | Что делает |
|---|---|
| `__manifest__.py` | `depends=['odoo_document_management_cloud_sync']`, `auto_install=True`, `post_init_hook='post_init'` |
| `__init__.py` | `from . import models; from .hooks.post_init import post_init` |
| `README.md` | Описание проблемы, что фиксит, сценарии установки/upgrade оригинала на проде |
| `models/__init__.py` | `from . import document_folder` (на этом этапе ещё без `document_file`) |
| `models/document_folder.py` | `_inherit='document.folder'`, `can_manage_access` computed (с `@api.depends_context('uid')`), ACL guard в `write()` |
| `security/document_access_fix.xml` | 42 records: implied_ids fix, override 14 ACL и 5 ir.rule оригинала через `odoo_document_management_cloud_sync.<xmlid>`, + 22 новых записи (Manager ACL на activity/comment/file_lock/file_filter; new two-tier ir.rule) |
| `views/document_folder_views.xml` | Inherit view с `readonly="not can_manage_access"` на `user_ids`/`group_ids` |
| `hooks/__init__.py` + `hooks/post_init.py` | Одноразовый cleanup: DELETE ACL `base.group_user` на `document.*`, DELETE stale broad `ir.rule`, DELETE orphaned ir_model_data |

**Особенность реализации `can_manage_access`**: добавлен декоратор `@api.depends_context('uid')` после того как smoke-тест показал, что без него computed-значение кешируется для одного пользователя и переиспользуется для других (admin owner → True кешировалось и для test_user, что было неверно).

---

### Round 3: фикс XML IDs (Cannot update missing record)

После того как пользователь поставил **чистый оригинал** через UI Apps, install `dms_access_fix` упал с ошибкой:

```
Cannot update missing record 'odoo_document_management_cloud_sync.access_document_activity_manager'
```

Причина: Odoo 17+ запрещает создавать запись с XML ID чужого модуля. Префикс `module.` можно использовать **только** для обновления существующих записей. На dev-стенде `Round 1` ранее создал эти записи руками — поэтому override работал. На чистом оригинале их нет.

**Файл**: `addons/dms_access_fix/security/document_access_fix.xml`

Из 42 записей **22 переименованы**: убран префикс `odoo_document_management_cloud_sync.`, теперь они создаются в namespace `dms_access_fix.*`. Это:
- 4 Manager ACL (activity, comment, file_lock, file_filter) — оригинал не имел manager-уровня для них
- 1 Manager `ir.rule` для share
- 13 user-level `ir.rule` (folder/file read+create, write+unlink, share read/create/write, comment, favorites, lock, filter)
- 4 Model User two-tier `ir.rule`

Оставшиеся **20 записей** (12 User ACL + 2 Model User ACL + group_document_manager + 3 Manager rules + 2 model_config rules) сохранили префикс — для них override корректен.

**Подтверждение в БД**: после install — 20 записей в namespace оригинала, 22 в namespace `dms_access_fix`, дублей нет, ни одного ACL на `Role / User`.

---

### Round 4: фикс compute-полей (Documents Dashboard не показывал файлы)

После того как ACL/ir.rule заработали, обнаружился второй баг: в **Documents Dashboard** под shared-user'ом папка видна, но файлы в ней не отображаются.

**Корень**: метод `get_files()` платного модуля (`models/document_file.py:534`) вызывает `files._compute_is_locked()` и `_compute_locked_info()`. Эти methods делают `record.field = value` на non-stored computed полях. `__set__` для non-stored поля всё равно идёт через `write()`, а `write()` делает `check_access('write')` — для не-owner это **AccessError**, весь endpoint падает, JS получает пустой ответ.

Та же проблема у `_compute_is_favorite` на `document.file` и `document.folder`.

**Решение**: писать значения **прямо в `env.cache`** через `self.env.cache.set(record, field, value)`, минуя `__set__/write()/check_access`. Это стандартный приём для non-stored computed полей в Odoo 17+.

| Файл | Изменение |
|---|---|
| `addons/dms_access_fix/models/document_file.py` (новый) | `_inherit='document.file'`, override `_compute_is_locked`, `_compute_locked_info`, `_compute_is_favorite` через `env.cache.set` (+ `.sudo()` на чтение `document.file.lock` / `document.file.favorite`) |
| `addons/dms_access_fix/models/document_folder.py` | Добавлен override `_compute_is_favorite` (same pattern) |
| `addons/dms_access_fix/models/__init__.py` | Добавлен `from . import document_file` |

**Проверено**:
```python
env['document.file'].with_user(doc_test_smoke).get_files(1)
# До: AccessError в _compute_is_locked
# После: [{'id': 2, 'name': 'MigrationPlan.md', 'is_locked': False, 'is_favorite': False, ...}]
```

UI: Documents Dashboard под shared-user'ом теперь видит файл в чужой расшаренной папке.

---

### Memory (для будущих сессий Claude Code)

| Файл | Назначение |
|---|---|
| `~/.claude/projects/-Users-ss-Documents-PyPrj-Bitrix-Migration/memory/feedback_stage_odoo_workflow.md` | Правки addons делаются в `Bitrix-Migration/addons/`, тестируются после `rsync` в `stage-odoo/addons/` и через контейнер `stage-odoo-odoo-1` / БД `stage-odoo-odoodb-1`. Локальный `bitrix_migration_odoo` не использовать. |
| `~/.claude/projects/.../memory/MEMORY.md` | Индекс memory-файлов |

---

## Итоговое состояние

### Что в git попадёт (новое)

- `addons/dms_access_fix/` — bridge-модуль, fix доступов и compute-полей.
- `CHANGELOG.md` — этот файл.

### Что в `.gitignore` (не пушится)

- `addons/odoo_document_management_cloud_sync/` — платный модуль (включая локальные Round 1 правки на dev-стенде).

### Workflow на проде

1. Поставить оригинальный `odoo_document_management_cloud_sync` (как обычно).
2. Положить рядом `dms_access_fix/` (этот репозиторий) → обновить список модулей → `auto_install` подхватит и установит.
3. `post_init_hook` одноразово вычистит ACL `base.group_user` и stale broad `ir.rule`; XML overrides переписывают остальное.
4. После любого upgrade оригинала: `odoo -u dms_access_fix -d <db>` — overrides применятся заново. Если оригинал что-то восстановил в `base.group_user` ACL — `uninstall + install` нашего модуля для повторного запуска `post_init_hook`.

### Текущая модель доступа (после установки fix)

| Кто | Что может |
|---|---|
| Document Manager | Всё на всех папках / файлах / share / cloud_sync |
| Owner (`user_id`) папки/файла | Полный CRUD на своих записях |
| Document User в `user_ids` или через `group_ids` папки | Read папки и файлов внутри; создавать новые файлы в этой папке; **не удалять/переименовывать** чужие; **не менять** `user_ids`/`group_ids` |
| Internal User без Document-группы | Доступа к Documents нет |

### Верификация (на `stage-odoo-odoo-1` под `doc_test_smoke`, uid=231)

- 11/11 smoke-тестов через `odoo shell` (read/unlink/rename/edit-ACL/create own/create in shared/manager unlink/computed can_manage_access per-user).
- `get_files(1)` под shared user возвращает корректный файл (Round 4 verification).
- БД: 27 ACL на `document.*`, **ни одного на `Role / User`**; 23 `ir.rule` в правильной two-tier форме.
- Симулированная дыра (`INSERT ACL base.group_user`) вычищается `post_init_hook` при reinstall fix-модуля.
