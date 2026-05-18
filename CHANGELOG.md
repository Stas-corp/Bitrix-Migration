# CHANGELOG

## 2026-05-18 — Фикс `UserError` при повторном импорте recurring-встреч

После Round 2 от 2026-05-16 (импорт RRULE → Odoo recurrence) пользователь сегодня запустил `mode=full` повторно поверх уже импортированных встреч. В логах `stage-odoo-odoo-1`:

```
[meeting] Extracting Bitrix meetings...
[meeting] Found 604 meetings
ERROR Migration failed
  File ".../bitrix_migration/services/loaders/meetings.py", line 265, in run
    ).write(diff)
  File ".../odoo/addons/calendar/models/calendar_event.py", line 776, in write
    raise UserError(_('Unable to save the recurrence with "This Event"'))
```

Падал на первом же existing-record-with-recurrence — `meeting` останавливался, `meeting_comments`/`attachments`/`reconciliation` дальше не отрабатывали.

### Корневая причина

В `calendar.event.write` (Odoo 19, `calendar_event.py:775-776`) стоит gate:

```python
recurrence_update_setting = values.pop('recurrence_update', None)
update_recurrence = (recurrence_update_setting in ('all_events', 'future_events')
                    and len(self) == 1 and self.recurrence_id)
if any(vals in self._get_recurrent_fields() for vals in values) \
        and not (update_recurrence or values.get('recurrency')):
    raise UserError(_('Unable to save the recurrence with "This Event"'))
```

`_get_recurrent_fields()` = `{byday, until, rrule_type, month_by, event_tz, rrule, interval, count, end_type, mon..sun, day, weekday}` — `recurrency` сама в множество не входит.

В `MeetingLoader.run()` ветка «не created» делала `diff` по `_RECURRENCE_FIELDS`, включая «реккурентные» поля. Сценарий падения для **detached-master** (`recurrency=True`, `recurrence_id=NULL` — результат `calendar.recurrence._detach_events` при `BYDAY` ≠ `start.weekday()`, см. `calendar_recurrence.py:365-369`):

1. `record.recurrency = True` → совпадает с `vals['recurrency']=True` → НЕ в diff.
2. `record.recurrence_id` пуст → `_compute_recurrence` (`calendar_event.py:492-511`) для пустого `recurrence_id` подставляет дефолты (`interval=1`, без BYDAY, `end_type='forever'`, …) → `record.rrule_type / mon / until / interval` ≠ нашим `vals` → попадают в diff.
3. `write(diff)` получает recurrent-поля **без** `recurrency=True` и **без** `recurrence_update` → gate срабатывает.

Тесты в `TestMeetingRecurrenceWrite` проверяли только **create**-путь — re-write путь не был покрыт, регрессия проскочила.

### Изменения

| Файл | Изменение | Причина |
|---|---|---|
| `addons/bitrix_migration/services/loaders/meetings.py:24-34` | Новая константа `_REWRITE_FIELDS = ('name', 'description', 'start', 'stop')` — поля, безопасные для re-sync на повторном импорте | Recurrence-поля интенсивно отделены от «обычных» — миграция одноразовая, RRULE прошедших встреч не меняется |
| `addons/bitrix_migration/services/loaders/meetings.py:249-269` | Цикл diff'а заменён с `_RECURRENCE_FIELDS` на `_REWRITE_FIELDS`. Добавлен страховочный фильтр `diff = {k: v for k, v in diff.items() if k not in calendar.event._get_recurrent_fields()}` | Recurrence-поля никогда не попадут в `write` — даже если кто-то в будущем расширит `_REWRITE_FIELDS` |
| `addons/bitrix_migration/tests/test_meetings.py` (`TestMeetingRecurrenceWrite`) | Новый тест `test_re_import_recurring_meeting_does_not_raise` — создаёт recurring event, симулирует Odoo-detach (`UPDATE calendar_event SET recurrence_id=NULL, active=FALSE`), прогоняет `MeetingLoader.run()` повторно, ассертит `error_count=0`, `skipped_count=1` | Точечно ловит регрессию: detached-master + re-import |

**Ключевое решение по «recurrence-only-at-create»**: правильное обновление активной recurrence-серии через `recurrence_update='all_events'` требует записи на `record.recurrence_id.base_event_id`. Для detached-кейса `record.recurrence_id` пуст, а `base_event_id` — другая запись (один из сгенерированных понедельников). Этот путь брittлен и не нужен: миграция одноразовая, RRULE для прошедших встреч в Bitrix не меняется. Решение — recurrence устанавливается ТОЛЬКО при первом `create`, re-imports перезаписывают только `name/description/start/stop/partner_ids`.

**Ключевое решение по страховочному фильтру**: даже если будущий разработчик расширит `_REWRITE_FIELDS` recurrence-полем по ошибке, явный `diff = {k: v for k, v in diff.items() if k not in recurrence_keys}` не пустит её до `write`. Двойная защита.

### Verification

1. **Unit-тесты**: 21/21 meeting-тестов прошли (`TestMeetingDTO` × 5, `TestMeetingCalendarEvent` × 4, `TestBitrixRRuleConverter` × 9, `TestMeetingSQLFilter` × 1, `TestMeetingRecurrenceWrite` × 2 включая новый regression). 0 failed / 0 errors.
2. **End-to-end на `stage-odoo`**: `action_run(mode='meetings')` через `odoo shell` завершился без `UserError`. В БД 31 503 `calendar.event` с `x_bitrix_id` (близко к 32 119 из Round 2 от 2026-05-16).
3. **Идемпотентность recurrence**: встреча 64158 («оновлення стану задач в ІТ проекті») после re-import — 628 events, `recurrency=True`, `rrule_type='weekly'`, `mon=True`, `until=2038-01-01` — параметры серии не изменились.
4. В логах строка `[meeting] DONE: created=…, updated=…, skipped=…, errors=…` без traceback.

### Что попадает в git

- `addons/bitrix_migration/services/loaders/meetings.py` — `_REWRITE_FIELDS`, переработанная re-write ветка.
- `addons/bitrix_migration/tests/test_meetings.py` — `test_re_import_recurring_meeting_does_not_raise`.
- `CHANGELOG.md` — эта запись.

---

## 2026-05-18 — Импорт уволенных сотрудников + очистка мусорных аккаунтов

Задача из двух связанных частей:

1. На примере уволенного **Дьякова Владислава** (Bitrix ID=200, ACTIVE='N', последний логин 2026‑04‑08): в Bitrix он participant в 8 256 задачах (1090 как R, 530 как A, 1952 как O, 5318 как U, 1952 как creator), но в Odoo `hr.employee` отсутствует — `SQL_EMPLOYEES` фильтровал `WHERE u.ACTIVE='Y'`. Все его роли терялись, `bitrix_task_employee_link.responsible` оставался пустым, поиск задач по нему был невозможен. Масштаб: 380 уволенных в Bitrix, 58 203 задач с responsible-уволенным, 101 914 задач с любой ролью уволенного.
2. После исправления (Round 1) обнаружилось переусердствование: в Odoo попали **1783 hr_employee** + 1791 res_partner вместо ожидаемых ~605. Распределение в Bitrix `b_user`: 782 `imconnector_*` (интеграции Open Channels), 388 «human» без UF_DEPARTMENT (`imopenlines_*` гости + `*@*.bitrix24.ru` Network), 7 ботов/анонимных. Все они попали в `hr.employee` после снятия фильтра.

---

### Round 1: импорт уволенных сотрудников

Корневая причина, найденная по реальному Bitrix MySQL:

- `SQL_EMPLOYEES` фильтровал `WHERE u.ACTIVE='Y' AND uu.UF_DEPARTMENT IS NOT NULL` — уволенные с любым UF_DEPARTMENT не попадали в Odoo.
- `archive_fired()` (`employees.py:145`) пытался архивировать существующих, но т.к. уволенные никогда не создавались — ничего не делал.
- `find_employee_by_bitrix_id` (`base.py:152`) уже использует `active_test=False`, поэтому изменений в TaskLoader не требовалось.
- Подтверждено: Odoo 19 при создании `hr.employee` автоматически создаёт `work_contact_id` (res.partner) — 226/226 импортированных имели partner. Это покрывает все ссылки на контакты в комментариях/встречах/follower'ах.

| Файл | Изменение | Причина |
|---|---|---|
| `addons/bitrix_migration/services/extractors/bitrix_mysql.py:547-572` | `SQL_EMPLOYEES`: убран `ACTIVE='Y'`, `JOIN → LEFT JOIN b_uts_user`, `TRIM(CONCAT_WS(...))` для пустых имён системных аккаунтов | Включить уволенных в стандартный путь импорта; единая точка обработки вместо двух SQL |
| `addons/bitrix_migration/services/normalizers/dto.py:251-300` | `BitrixEmployee.active: bool = True` + `field_validator('active', mode='before')` для `'Y'/'N' → bool` | DTO должен моделировать статус активности; пустые значения → `True` (безопасный дефолт) |
| `addons/bitrix_migration/services/loaders/employees.py:68-149` | В `run()`: пропуск `_resolve_user()` для уволенных, лог `'{n} active / {n} fired'` | У уволенного не должно быть активного res.users assignee; `archive_fired()` остаётся ответственным за линк с res.users |
| `addons/bitrix_migration/services/loaders/employees.py:245-281` | `_build_employee_vals`: ставит `active=False` только для уволенных (для активных полагаемся на Odoo default `True`); `_prepare_update_vals`: защита от re‑activate — `False → True` никогда не применяется автоматически | Защищает админа, вручную заархивировавшего сотрудника, от автоматического восстановления при повторном прогоне |
| `addons/bitrix_migration/models/bitrix_migration_run.py` (`_run_reconciliation`) | Новые счётчики: `Employees (active)`, `Employees (fired)`, `Tasks with fired responsible` (через JOIN `bitrix_task_employee_link` × `hr_employee.active=FALSE`) | Видимость качества миграции после правок |
| `addons/bitrix_migration/tests/test_fired_employees.py` (новый) | 14 тестов: DTO normalization (Y/N/empty/default), `_build_employee_vals`, `_prepare_update_vals` (downgrade vs no re‑activate), полный `run()` через `_NoCommitLoader` override, link на архивного employee | `commit_checkpoint()` запрещён в TransactionCase Odoo 19 — переопределяем no‑op |

**Ключевое решение по `_NoCommitLoader`**: subclass с пустым `commit_checkpoint` — позволил тестировать полный `run()` внутри TransactionCase, минуя запрет на `cr.commit()`. Это паттерн для будущих тестов loader'ов.

**Ключевое решение по re‑activate**: исключить ключ `active` из общего обхода `_prepare_update_vals` и обрабатывать отдельно: записывать только `active=False`, никогда `True`. Иначе ручная архивация в Odoo перетиралась бы при следующем прогоне миграции.

**Проверено**: 14/14 новых тестов прошли. Регрессий нет (12 fail в `test_departments`/`test_employee_avatars`/`test_role_mapping`/`test_meeting_comments` — унаследованный baseline из‑за `cr.commit()` в TransactionCase).

---

### Round 2: возврат UF_DEPARTMENT и очистка мусора

После Round 1 пользователь сообщил: в Odoo 1398 активных + 385 архивных = 1783 hr_employee (а в структуре Telemart — 198 активных + 363 архивных = 561). Анализ Bitrix `b_user` × UF_DEPARTMENT × ACTIVE:

| Bucket | Кол-во | Статус |
|---|---:|---|
| `human` + UF_DEPARTMENT + ACTIVE='Y' | 223 | реальные сотрудники |
| `human` + UF_DEPARTMENT + ACTIVE='N' | 379 | уволенные сотрудники |
| `imconnector_*` (Open Channels) | 782 | **шум** (0 упоминаний в задачах) |
| `human` без UF_DEPARTMENT, ACTIVE='Y' | 388 | **шум** (imopenlines/`*@*.bitrix24.ru` Network) — лишь 12 в задачах |
| `bot_*` / `anonymous*` / `support*` + 4 фантома | 11 | шум |
| **Итого настоящих** | **605** | (225 active + 380 fired) |

Истинный фильтр сотрудников Telemart — наличие `UF_DEPARTMENT`. Это автоматически отсекает 100% мусора, включая будущие новые префиксы.

**Решения пользователя** (через AskUserQuestion):
1. Фильтр: `UF_DEPARTMENT IS NOT NULL AND != ''` (без ACTIVE — уволенные с отделом остаются).
2. Очистка: **жёсткое `unlink()`** через ORM с предварительным dry‑run preview.
3. Со ссылками: перевод `mail.message.author_id` на system_partner (OdooBot `base.partner_root`) и удалить.

| Файл | Изменение | Причина |
|---|---|---|
| `addons/bitrix_migration/services/extractors/bitrix_mysql.py:547-572` | `SQL_EMPLOYEES`: `LEFT JOIN → JOIN b_uts_user` + `WHERE uu.UF_DEPARTMENT IS NOT NULL AND != ''`. То же для `SQL_FIRED_EMPLOYEE_IDS` (симметрия для `archive_fired`) | UF_DEPARTMENT — единственный надёжный признак реального сотрудника компании |
| `addons/bitrix_migration/services/extractors/bitrix_mysql.py:578-589` | Новый `SQL_NOISE_USER_IDS` + метод `get_noise_user_ids()` — возвращает user_id с `UF_DEPARTMENT IS NULL OR ''` | Для `purge_noise_accounts()` — обратный к основному фильтр |
| `addons/bitrix_migration/services/loaders/employees.py:245-401` | Новый метод `purge_noise_accounts(dry_run)`: находит hr.employee по `x_bitrix_id IN noise_ids`; для каждого — `mail.message.author_id` → OdooBot, `x_bitrix_author_employee_id` → False, `project.task.x_bitrix_creator_employee_id` → False, `mail.followers` на bitrix-моделях → unlink, mapping → unlink, hr.employee → unlink (CASCADE удалит `bitrix_task_employee_link`), orphan `work_contact_id` → unlink (если не shared) | Жёсткое удаление с переносом orphan-ссылок на системного актора — компромисс между чистотой БД и сохранением аудит-следа |
| `addons/bitrix_migration/services/loaders/employees.py` | Новые хелперы: `_get_system_author_partner()` → `base.partner_root` (OdooBot); `_partner_is_orphan()` → проверяет, что никто кроме удаляемого employee не ссылается на partner | OdooBot всегда есть, не архивируется, визуально маркирует system-владение |
| `addons/bitrix_migration/models/bitrix_migration_run.py` | Новый `mode='purge_noise'`, диспетчер в `action_run` (строки 263-266), `_run_purge_noise(extractor, dry_run)` | Поддержка через стандартный flow `action_run` для CLI-сценариев |
| `addons/bitrix_migration/models/bitrix_migration_run.py` | Два action‑метода: `action_purge_noise_preview()` (dry_run=True) и `action_purge_noise_apply()` (dry_run=False с confirm); состояние `running` + `extractor.close()` в `finally` | UI: пользователь сначала Preview, потом Apply с подтверждением |
| `addons/bitrix_migration/views/bitrix_migration_run_views.xml` | Новый раздел "Noise Accounts" в Danger Zone с двумя кнопками | Видимость для пользователя |
| `addons/bitrix_migration/tests/test_purge_noise.py` (новый) | 5 тестов: основной purge (employee + orphan partner), dry_run без изменений, partner shared с другим employee — НЕ удаляется, `mail.message.author_id` → OdooBot, `project.task.x_bitrix_creator_employee_id` → False | Покрытие всех edge cases |

**Ключевое решение по полю mapping**: `bitrix.migration.mapping` имеет поле `odoo_id`, не `odoo_record_id` (мой первый вариант). Найдено через тесты (первый прогон дал 5 ERROR с `KeyError: 'odoo_record_id'`).

**Ключевое решение по OdooBot vs новый partner**: переиспользуем `base.partner_root` (OdooBot) вместо нового "Bitrix Migration System Partner". OdooBot — стандартный, всегда есть, не требует генерации, ясно сигнализирует system-владение.

**Ключевое решение по orphan-проверке partner**: перед `unlink` partner проверяем (a) других hr.employee с тем же `work_contact_id` и (b) линкованных res.users. Если есть — partner остаётся (может быть в чужих модулях — CRM, sales).

**Verification на чистом stage-odoo через `odoo shell`**:
```
Found 1178 noise user IDs in Bitrix
Matched 1178 noise hr.employee records in Odoo
Using system partner id=2 (name="OdooBot")
Would purge: 1178 hr.employee, 1178 res.partner;
  reassigned 0 mail.message, untied 0 project.task creators,
  removed 0 mail.followers, removed 1178 mapping rows; errors=0
```

Цифры точно совпадают с ожиданием: 1783 − 1178 = **605** реальных сотрудников после apply. Сам apply пользователь запустит через UI после backup БД.

---

### Round 3: тесты

| Файл | Изменение |
|---|---|
| `addons/bitrix_migration/tests/test_fired_employees.py` | 14 тестов (4 класса): `TestBitrixEmployeeDTO` (5), `TestEmployeeValsBuilder` (4), `TestFiredEmployeeLoader` (4), `TestFiredEmployeeRolesOnTasks` (1) |
| `addons/bitrix_migration/tests/test_purge_noise.py` | 5 тестов в `TestPurgeNoise` (full purge, dry_run, shared partner, mail.message reassign, task creator unset) |
| `addons/bitrix_migration/tests/__init__.py` | Регистрация двух новых модулей |

Прогон в stage-odoo: **131 теста, 12 failures (baseline унаследованный — те же давние тесты с `cr.commit()` в TransactionCase), 0 errors**. Все 19 новых тестов прошли.

---

### Итоговая верификация

1. SQL_EMPLOYEES в Bitrix теперь возвращает **605** строк (225 active + 380 fired) вместо 1783 — фильтр UF_DEPARTMENT возвращён.
2. `action_purge_noise_preview` на stage-odoo: «Would purge: 1178 hr.employee, 1178 res.partner, 1178 mapping rows; errors=0». Цифры идеально совпадают с расчётом.
3. Дьяков Владислав (`x_bitrix_id=200`, ACTIVE='N') после следующей `employees_only` миграции будет `hr.employee` с `active=False`, `work_contact_id` (partner) автоматически создан Odoo.
4. После Apply покойный сотрудник в `hr_employee` будет findable через стандартный фильтр Odoo «Archived» по `x_bitrix_responsible_employee_id` — отдельной кнопки/UI не потребовалось.
5. Идемпотентность: повторный preview/apply — no‑op (нет hr.employee с мусорными x_bitrix_id).

### Что попадает в git

- `addons/bitrix_migration/services/extractors/bitrix_mysql.py` — SQL_EMPLOYEES, SQL_FIRED_EMPLOYEE_IDS, SQL_NOISE_USER_IDS, `get_noise_user_ids()`.
- `addons/bitrix_migration/services/normalizers/dto.py` — `BitrixEmployee.active` + валидатор.
- `addons/bitrix_migration/services/loaders/employees.py` — поддержка `active` в `run`/`_build_employee_vals`/`_prepare_update_vals`, новый `purge_noise_accounts()` + `_get_system_author_partner` / `_partner_is_orphan`.
- `addons/bitrix_migration/models/bitrix_migration_run.py` — `mode='purge_noise'`, `_run_purge_noise`, `action_purge_noise_preview/apply`, расширенный `_run_reconciliation`.
- `addons/bitrix_migration/views/bitrix_migration_run_views.xml` — секция "Noise Accounts" с двумя кнопками.
- `addons/bitrix_migration/tests/test_fired_employees.py` (новый) — 14 тестов.
- `addons/bitrix_migration/tests/test_purge_noise.py` (новый) — 5 тестов.
- `addons/bitrix_migration/tests/__init__.py` — регистрация.

---

## 2026-05-16 — Purge встреч + импорт повторяющихся встреч из Bitrix

Задача состояла из двух связанных частей:
1. Кнопка **Purge Imported Data** в Danger Zone не удаляла данные календаря — после её нажатия проекты/задачи/комментарии чистились, но 14 887 `calendar.event` оставались.
2. Повторяющиеся встречи Bitrix (например, id=64158 «оновлення стану задач в ІТ проекті», еженедельно по понедельникам до 01.01.2038) импортировались в Odoo как одно событие на дату первого вхождения — без recurrence и без последующих экземпляров.

---

### Round 1: фикс Purge для `calendar.event`

Корневая причина, найденная по реальной БД в `stage-odoo-odoo-1`:

- Все 14 887 `calendar.event` имели `x_bitrix_id IS NULL`. Purge искал по `[('x_bitrix_id', '!=', False)]` и ничего не находил.
- На `calendar.event.x_bitrix_id` стоит `copy=False`. Odoo 19 `calendar.recurrence._apply_recurrence()` разворачивает recurrence через `event.copy_data()` — `x_bitrix_id` не попадает в occurrence'ы. Когда базовое событие удалили вручную, FK `ON DELETE SET NULL` + `_select_new_base_event()` сделали новым `base_event_id` одну из occurrence (без `x_bitrix_id`). Маппинги к этому моменту тоже были стёрты — потерян весь след «битриксовости» записей.

| Файл | Изменение | Причина |
|---|---|---|
| `addons/bitrix_migration/models/bitrix_migration_run.py:2531-2538` | Шаг «Removing imported meetings» в `action_purge_data` стал трёхуровневым: сначала `_purge_records_by_mapping('meeting', ...)`, потом `_purge_records_by_domain(...)` по `x_bitrix_id`, потом SQL-зачистка | mapping надёжнее поля (его нельзя сбросить `copy=False`), но если он уже пуст — нужен fallback по полю; а если и поле потеряно — нужен SQL-sweep |
| `addons/bitrix_migration/models/bitrix_migration_run.py:2272-2299` | Новый метод `_purge_orphaned_calendar_recurrences()`: `DELETE FROM calendar_event WHERE recurrence_id IS NOT NULL AND (x_bitrix_id IS NULL OR x_bitrix_id = '')` + `DELETE FROM calendar_recurrence WHERE base_event_id IS NULL OR id NOT IN (SELECT DISTINCT recurrence_id FROM calendar_event)` | Single источник истины для «осиротевших» occurrence'ов и пустых recurrence — рассчитан на восстановление после Round 2 (когда `x_bitrix_id` будет корректно проставлен — sweep ничего не удалит, идемпотентно) |

**Ключевое решение**: оставить `copy=False` на `x_bitrix_id` (важно для нормального duplicate UX) и решать пропагацию в loader'е, а в purge добавить дублирующий SQL-fallback. Это даёт «двойную страховку» при любом сценарии порчи данных.

**Проверено**: `Purge` отработал на 14 887 событий → осталось 2 ручных тестовых, 0 recurrence.

---

### Round 2: импорт повторяющихся встреч (Bitrix RRULE → Odoo recurrence)

В `git stash` лежала WIP-реализация парсера RRULE и тестов. Перенесли, адаптировали под текущий `_MEETING_GUARD` и решили две найденные проблемы.

#### 2a. Сверка по реальному Bitrix MySQL: что меняется между встречами

Для встреч **64158** (`PARENT_ID = ID`, master) и **67961** (`PARENT_ID = 67960`, child-copy участника) обнаружено:

- 67961 — это автоматическая копия мастера 67960 для участника, в импорт идти не должна. Текущий `_MEETING_GUARD` фильтрует её через `ce.ID = ce.PARENT_ID` ✓.
- У 64158 есть `EXDATE='29.12.2025'` (исключение). Stash-конвертер RRULE его игнорировал — на UI Odoo возникла бы лишняя встреча 29.12.2025.
- У 64158 0 child-copies. Это подтвердило, что stash-вариант `_MEETING_GUARD` (требовал наличие хотя бы одной child-copy) был бы неправильным — потеряли бы такие встречи. Текущий `_MEETING_GUARD` (фильтр по `b_calendar_section.EXTERNAL_TYPE NOT IN ('google','google_readonly','icloud','caldav','exchange')`) корректен и сохранён.

#### 2b. Перенос из stash

| Файл | Изменение |
|---|---|
| `addons/bitrix_migration/services/normalizers/dto.py` | `BitrixMeeting` расширен полями `rrule: Optional[str]`, `exdate: Optional[str]`, `section_id: Optional[int]` + соответствующие field_validator'ы |
| `addons/bitrix_migration/services/extractors/bitrix_mysql.py` (SQL_MEETINGS_TEMPLATE) | В SELECT добавлены `ce.RRULE AS rrule`, `ce.EXDATE AS exdate`, `ce.SECTION_ID AS section_id`. `_MEETING_GUARD` **не трогали** |
| `addons/bitrix_migration/services/extractors/bitrix_mysql.py:893-899` | `_get_meeting_where_clause()` теперь возвращает `"(ce.DATE_FROM >= %s OR (ce.RRULE IS NOT NULL AND ce.RRULE != ''))"` | 
| `addons/bitrix_migration/services/loaders/meetings.py` | Добавлены `_FREQ_MAP`, `_BYDAY_FIELD_MAP`, `_RECURRENCE_FIELDS`, функции `_parse_bitrix_until`, `_parse_bitrix_exdate`, `_bitrix_rrule_to_odoo_recurrence`. В `MeetingLoader.run()` — выставление `recurrency=True` + vals из конвертера, diff-update для существующих записей |

**Ключевое решение по EXDATE**: вместо отказа от поддержки реализовать архивацию занятия по дате — `targets.write({'active': False})`. Odoo трактует non-active occurrence как exception, повторные запуски `_apply_recurrence` его не воссоздадут.

**Ключевое решение по where-clause**: recurring-серия в Bitrix хранит `DATE_FROM = первое вхождение`. Если cut-off date позже первого вхождения, серия отфильтровывалась целиком, хотя её последующие occurrence'ы попадают в нужный диапазон. Поэтому recurring-встречи проходят бай-пасс date_from.

#### 2c. Пропагация `x_bitrix_id` на occurrences

В loader'е после `get_or_create` добавлено:

```python
self.env.cr.execute(
    "UPDATE calendar_event SET x_bitrix_id = %s "
    "WHERE recurrence_id = %s AND (x_bitrix_id IS NULL OR x_bitrix_id = '')",
    (bid, rec_id),
)
```

**Ключевое решение по copy=False**: не убирать `copy=False` с поля. При обычном «Duplicate» события в Odoo пользователь не должен случайно получить тот же `x_bitrix_id`. Вместо этого пропагация делается явно прямым SQL — это аккуратно обходит ORM-семантику `copy_data()`.

#### 2d. Detach-кейс (id=63741): Bitrix master во вторник, RRULE BYDAY=MO

После первого запуска осталось 138 occurrence'ов без `x_bitrix_id` в 4 recurrence'ах. Расследование:

- Bitrix-встреча 63741 имеет `DATE_FROM=2025-12-02` (вторник) и `RRULE=FREQ=WEEKLY;BYDAY=MO`. Несоответствие BYDAY и DATE_FROM реально встречается.
- Odoo `_apply_recurrence` сделал базовое событие «detached» (`active=False`, `recurrence_id=NULL`), а в качестве `base_event_id` recurrence выбрал один из сгенерированных понедельников.
- Наш SQL UPDATE через `record.recurrence_id.id` пропустил такие случаи — у detached'нутого record `recurrence_id` пустой.

**Решение**: добавлен метод `_resolve_recurrence_id(record)` — если у `record` нет `recurrence_id`, но он `active=False`, ищет в `calendar.recurrence` запись с тем же `base_event.name` и `create_date >= record.create_date`. Достаточно надёжно, потому что миграция однопоточная и имена в одном пакете уникальны.

| Файл | Изменение |
|---|---|
| `addons/bitrix_migration/services/loaders/meetings.py` | Метод `_resolve_recurrence_id`; единая ветка пропагации использует его вместо `record.recurrence_id.id` |
| `addons/bitrix_migration/services/loaders/meetings.py` | Метод `_drop_recurrence_exceptions(recurrence_id, exdates)`: `Event.search([recurrence_id, start in [exdates]])` → `write({'active': False})` |

---

### Round 3: тесты

| Файл | Изменение |
|---|---|
| `addons/bitrix_migration/tests/test_meetings.py` | Расширен `TestMeetingDTO.test_dto_basic` (rrule/exdate/section_id). Добавлены классы `TestBitrixRRuleConverter` (9 тестов: weekly/daily/monthly, count vs until, BYDAY fallback к DTSTART, trailing `;`, EXDATE парсер, UNTIL варианты `DD.MM.YYYY`/`YYYY-MM-DD`/`YYYYMMDDTHHMMSSZ`), `TestMeetingSQLFilter` (рендер SQL_MEETINGS_TEMPLATE с новыми колонками и recurring-bypass), `TestMeetingRecurrenceWrite` (persist в реальную `calendar.event` с проверкой `rrule_type`, `mon`, `end_type`, `until`) |

12 padений в общем прогоне модуля **не связаны** с этой работой — это давние тесты с `cr.commit()/rollback()` внутри (`test_departments`, `test_employee_avatars`, `test_meeting_comments`, `test_role_mapping`), Odoo 19 теперь это запрещает на уровне runner'а. Наши новые тесты прошли все.

---

### Итоговая верификация (на чистом `stage-odoo-odoo-1`)

1. Purge → 2 ручных события, 0 recurrence.
2. Импорт 1330 мастер-встреч из Bitrix → **32 119 calendar.event** с `x_bitrix_id` (Odoo развернул recurrence-серии до 2038-01-01) + 2 ручных тестовых = 32 121 всего. **0 occurrence'ов без `x_bitrix_id`** (раньше — 138).
3. **64158** (WEEKLY MO, UNTIL=01.01.2038, EXDATE=29.12.2025): 628 events, 627 active по понедельникам, 29.12.2025 archived (active=False) ✓.
4. **67960** (мастер): 613 events по понедельникам. **67961 (child-copy)**: 0 events — корректно отфильтрован extractor'ом ✓.
5. **63741** (DATE_FROM=вторник, RRULE BYDAY=MO — detached-кейс): 6 events с правильным `x_bitrix_id` ✓.
6. Повторный Purge → снова 2 события, 0 recurrence (идемпотентность).

### Что попадает в git

- `addons/bitrix_migration/models/bitrix_migration_run.py` — `action_purge_data` + `_purge_orphaned_calendar_recurrences`.
- `addons/bitrix_migration/services/extractors/bitrix_mysql.py` — RRULE/EXDATE/SECTION_ID в SELECT, recurring bypass в `_get_meeting_where_clause`.
- `addons/bitrix_migration/services/normalizers/dto.py` — поля DTO.
- `addons/bitrix_migration/services/loaders/meetings.py` — конвертер RRULE/EXDATE, пропагация `x_bitrix_id`, обработка detach.
- `addons/bitrix_migration/tests/test_meetings.py` — 3 новых класса тестов.

---

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
