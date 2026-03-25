# Миграция Bitrix24 → Odoo: Актуальные SQL-скрипты


## Сводка по листам шаблона

| Лист | Записей | Источник |
|---|---|---|
| Шаблон для проектів | 96 (94 project + 2 scrum) | `b_sonet_group` |
| Шаблон для задач | ~135 855 (115K корневых + 20K подзадач) | `b_tasks` |
| Теги (мітки) | 214 уникальных (+ до 9 из проектов) | `b_tasks_label` + `b_sonet_group_tag` |
| Етапи проекту | 535 (546 G + 9 A, минус пустые) | `b_tasks_stages` |
| Коментарі | ~874K всех / ~418K реальных | `b_forum_message` |
| Зустрічі | 5 821 уникальных | `b_calendar_event` |


---

# 1. Шаблон для проектів

```sql
SELECT 
  g.ID AS `external_id`,
  g.NAME AS `name`,
  CASE WHEN g.CLOSED = 'Y' THEN 'Завершено' ELSE 'В роботі' END AS `stage_id`,
  CASE WHEN g.PROJECT = 'Y' THEN 'Проект' ELSE 'Робоча група' END AS `type`,
  NULL AS `partner_id`,
  (SELECT GROUP_CONCAT(gt.NAME SEPARATOR ', ') 
   FROM b_sonet_group_tag gt WHERE gt.GROUP_ID = g.ID) AS `tags`,
  u.ID AS `user_id`,
  g.PROJECT_DATE_START AS `date_start`,
  g.PROJECT_DATE_FINISH AS `date_end`,
  NULL AS `Розподілений час`,
  NULL AS `Доступність`,
  g.DESCRIPTION AS `description`
FROM b_sonet_group g
LEFT JOIN b_user u ON u.ID = g.OWNER_ID
ORDER BY g.ID
```

### Нюансы

- **`stage_id` = NULL** — в Bitrix у `b_sonet_group` нет прямого поля "текущий этап проекта". Этапы (`b_tasks_stages` с `ENTITY_TYPE = 'P'`) — это системные периоды для диаграммы Ганта, а не статус проекта. Единственный признак завершённости — `CLOSED = 'Y'/'N'`.
- **`partner_id` = NULL** — нет стандартного поля "клиент". Таблица `b_uts_sonet_group` содержит только `UF_SG_DEPT` (привязка к отделу). Если клиент привязан через CRM — нужно уточнять через сделки или кастомные UF-поля.
- **`Розподілений час` / `Доступність`** = NULL — Odoo-специфичные атрибуты, отсутствуют в Bitrix.
- **Мітки** — выводятся как текст (имена). В `b_sonet_group_tag` нет числового ID тега — только `GROUP_ID` + `NAME`. Всего 11 тегов у проектов (9 уникальных имён). Это **отдельная сущность** от тегов задач (`b_tasks_label`), но в Odoo `project.tags` — один справочник.


---

# 2. Шаблон для задач (з підзадачами)

```sql
SELECT 
  t.ID AS `external_id`,
  t.TITLE AS `name`,
  CASE WHEN t.GROUP_ID > 0 
    THEN t.GROUP_ID
    ELSE NULL END AS `project_external_id`,
  GROUP_CONCAT(DISTINCT m.USER_ID SEPARATOR ', ') AS `responsible_user_ids`,
  (SELECT GROUP_CONCAT(DISTINCT tl.NAME SEPARATOR ', ')
   FROM b_tasks_task_tag tt 
   JOIN b_tasks_label tl ON tl.ID = tt.TAG_ID
   WHERE tt.TASK_ID = t.ID) AS `tags`,
  t.DEADLINE AS `date_deadline`,
  t.DESCRIPTION AS `description`,
  t.STAGE_ID AS `stage_id`,
  CASE WHEN t.PARENT_ID > 0 
    THEN t.PARENT_ID
    ELSE NULL END AS `parent_id`
FROM b_tasks t
LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE IN ('R','A')
WHERE (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
GROUP BY t.ID
ORDER BY t.ID
```

### Нюансы

**1. Мітки — выводятся по ИМЕНИ, а не по TAG_ID**

Тег "Service Desk" в `b_tasks_label` — это 131 строка с разными ID (отдельная запись для каждого пользователя/группы). Задачи ссылаются на разные ID (125, 127, 128…), а в дедуплицированном справочнике тегов (лист 3) мы берём `MIN(ID)`. Итого TAG_ID задачи **не совпадёт** с каноническим ID. Поэтому в запросе выводим `tl.NAME` через JOIN `b_tasks_label`, а маппинг с Odoo делаем по имени.

**2. Уповноважені — ответственный + соисполнители**

| TYPE | Роль |
|---|---|
| R | Відповідальний |
| O | Постановник |
| A | Співвиконавець |
| U | Спостерігач |

`TYPE IN ('R','A')` = ответственный + соисполнители. TYPE='R' полностью дублирует `t.RESPONSIBLE_ID` (проверено — 100% совпадение).

**3. Підзадачі — включены**

Фильтр по `PARENT_ID` убран — подзадачи попадают в выгрузку. Колонка `Підзадача (parent_id)` содержит ссылку на родительскую задачу в формате `project_task_XXX`. Для корневых задач — NULL. Итого ~135 855 строк (115K корневых + 20K подзадач).

**4. Задачи без проекта (GROUP_ID = 0)**

Из 136 004 задач **117 058 (86%) не привязаны к группе/проекту** в Bitrix. В Odoo `project.task` требует обязательного `project_id`.

**Решение:** задачи с `GROUP_ID = 0` или `NULL` импортируются в проект **"Основний"** (id=1). В SQL это уже учтено — `project_external_id = NULL` для таких задач, при импорте нужно подставить `1` вместо NULL.

---

**5. STAGE_ID — в основном пустой**

| Ситуация |
|---|
| Нет стадии (0 или NULL) 
| Валидная стадия |
| Битая ссылка |

Стадии есть только у задач в группах с настроенным канбаном. Большинство задач в Odoo не получат этап.


---

# 3. Теги (мітки) — дедуплікований довідник

### Только теги задач (214 записей)
```sql
SELECT 
  MIN(ID) AS ID,
  NAME AS `name`
FROM b_tasks_label
WHERE NAME IS NOT NULL AND NAME != ''
GROUP BY NAME
ORDER BY NAME
```

### Объединённый справочник (задачи + проекты)
```sql
SELECT ID, NAME AS `name`, 'task' AS `source`
FROM (
  SELECT MIN(ID) AS ID, NAME
  FROM b_tasks_label
  WHERE NAME IS NOT NULL AND NAME != ''
  GROUP BY NAME
) tl

UNION ALL

SELECT 
  900000 + ROW_NUMBER() OVER (ORDER BY NAME) AS ID,
  NAME AS `name`,
  'project' AS `source`
FROM (
  SELECT DISTINCT NAME
  FROM b_sonet_group_tag gt
  JOIN b_sonet_group g ON g.ID = gt.GROUP_ID
  WHERE g.PROJECT = 'Y'
    AND NAME NOT IN (SELECT NAME FROM b_tasks_label)
) pt

ORDER BY `name`
```

### Нюансы

В Bitrix метки — **не глобальные**. Одна и та же метка дублируется для каждого пользователя и каждой группы. Всего 616 записей в `b_tasks_label`, но уникальных имён — **214**.

| Поле | Смысл |
|---|---|
| ID | Уникальный ID записи |
| NAME | Название метки |
| USER_ID | Привязка к пользователю (личный канбан) |
| GROUP_ID | Привязка к группе |

`MIN(ID)` — канонический ID для каждого уникального тега.

**Два отдельных источника тегов:**

| Источник | Таблица | Есть ID |
|---|---|---|---|
| Теги задач | `b_tasks_label` | Да (но дублируются) |
| Теги проектів | `b_sonet_group_tag` | **Нет** (только GROUP_ID + NAME) |

Эти сущности **никак не связаны** между собой. Объединённый запрос добавляет теги проектов с синтетическими ID (900001+), исключая те, которые уже есть среди тегов задач. Колонка `source` — для ориентира, можно убрать при импорте.


---

# 4. Етапи проекту

```sql
SELECT 
  ID,
  TITLE AS `name`
FROM b_tasks_stages
WHERE ENTITY_TYPE IN ('G', 'A')
  AND TITLE IS NOT NULL AND TITLE != ''
ORDER BY ID
```

### Нюансы

`b_tasks_stages` — это **канбан-стадии задач**, а не этапы проектов в понимании Odoo.

**4 типа (ENTITY_TYPE):**

| Тип | Что это | ENTITY_ID указывает на |
|---|---|---|
| **G** | Стадии канбана рабочих групп | ID группы (`b_sonet_group`) |
| **U** | Личные стадии канбана пользователей | ID пользователя |
| **P** | Системные периоды проектов (PERIOD1–6) | ID группы-проекта |
| **A** | Стадии потоков (Flows) | ID потока |

- Стадии **привязаны к конкретным группам/пользователям**, а не глобальные. У группы "Маркетинг" свои 3 стадии, у "Development" свои 4 и т.д.
- **P** исключён — пустые названия, внутренние системные периоды для диаграммы Ганта.
- **U** исключён — личные стадии канбана пользователей.
- Итого **535 записей** после фильтрации.
- В Bitrix у проектов нет "этапов проекта" как отдельной сущности. Это ближе к `project.task.type` в Odoo, а не к `project.project.stage`.

**Открытый вопрос:** нужны ли уникальные названия стадий (дедупликация, ~44 штуки) или все стадии с привязкой к группе (535 строк)?

## UPD

```sql
SELECT
  g.ID AS `external_id`,
  g.NAME AS `name`,
  CASE 
    WHEN g.PROJECT = 'Y' THEN 'Проект'
    ELSE 'Робоча група'
  END AS `entity_type`,
  s.ID AS `stage_external_id`,
  s.TITLE AS `stage_name`,
  CASE 
    WHEN g.CLOSED = 'Y' THEN 'Завершено'
    ELSE 'В роботі'
  END AS `status`,
  u.ID AS `user_id`,
  g.PROJECT_DATE_START AS `date_start`,
  g.PROJECT_DATE_FINISH AS `date_end`,
  g.DESCRIPTION AS `description`
FROM b_sonet_group g
LEFT JOIN b_user u 
  ON u.ID = g.OWNER_ID
LEFT JOIN b_tasks_stages s
  ON s.ENTITY_TYPE = 'G'
 AND s.ENTITY_ID = g.ID
 AND s.TITLE IS NOT NULL
 AND s.TITLE <> ''
ORDER BY g.ID, s.TITLE;
```

## Описание

Скрипт выбирает из `b_sonet_group` список сущностей Bitrix24, включая проекты и рабочие группы, затем подтягивает владельца из `b_user` и связанные этапы канбана из `b_tasks_stages`. [web:16][web:36][web:47]  
В результат выводятся идентификатор, название, тип сущности, статус, пользователь-владелец, даты проекта и название этапа канбана. [web:16][web:36]

## Замечания

- Связь этапов с проектами и рабочими группами выполняется по условию `s.ENTITY_TYPE = 'G'` и `s.ENTITY_ID = g.ID`. [web:16]
- Поле `g.PROJECT` определяет тип сущности: проект или рабочая группа. [web:36][web:47]
- Одна сущность может повторяться в нескольких строках, если у нее несколько этапов канбана. [web:16]
- Для рабочих групп поля `PROJECT_DATE_START` и `PROJECT_DATE_FINISH` могут быть не заполнены, так как проектные даты относятся к проектной логике. [web:36][web:50]


---

# 5. Коментарі

### Все комментарии (~874K записей)
```sql
SELECT 
  'project.task' AS `document_entities`,
  CONCAT('project_task_', t.ID) AS `entities_id`,
  CASE 
    WHEN fm.SERVICE_TYPE = 1 THEN 'Системне повідомлення'
    WHEN fm.NEW_TOPIC = 'Y' THEN 'Автоповідомлення'
    ELSE 'Коментар'
  END AS `Тип`,
  'Примітка' AS `Підтип`,
  fm.POST_MESSAGE AS `Тіло тексту`,
  fm.POST_DATE AS `Дата`,
  fm.AUTHOR_ID AS `Автор`
FROM b_forum_message fm
STRAIGHT_JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
WHERE fm.FORUM_ID = 11
  AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
ORDER BY fm.POST_DATE
```

### Только реальные комментарии (~418K записей)
```sql
SELECT 
  'project.task' AS `Повязана модель документу`,
  CONCAT('project_task_', t.ID) AS `id сутності`,
  'Коментар' AS `Тип`,
  'Примітка' AS `Підтип`,
  fm.POST_MESSAGE AS `Тіло тексту`,
  fm.POST_DATE AS `Дата`,
  fm.AUTHOR_ID AS `Автор`
FROM b_forum_message fm
STRAIGHT_JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
WHERE fm.FORUM_ID = 11
  AND fm.SERVICE_TYPE IS NULL
  AND fm.NEW_TOPIC = 'N'
  AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
ORDER BY fm.POST_DATE
```

### Нюансы

В `b_forum_message` (форум задач, FORUM_ID=11) хранятся три типа записей:

| Тип |  Что это |
|---|---|
| SERVICE_TYPE IS NULL + NEW_TOPIC = 'N' | Настоящие комментарии (написаны людьми) |
| SERVICE_TYPE IS NULL + NEW_TOPIC = 'Y' | Автосообщения при создании задачи ("TASK_123") |
| SERVICE_TYPE = 1  | Системные логи (смена статуса, дедлайна и т.д.) |

- **`FORUM_ID = 11`** — обязательно для производительности (индекс есть). Без фильтра JOIN на 895K × 135K — таймаут.
- **`STRAIGHT_JOIN`** — подсказка MySQL, что нужно идти от `b_forum_message` (меньше строк после фильтров) к `b_tasks`.
- **874K строк** — не выгрузятся через интерфейс Metabase. Сохранить как Question → Download → CSV.

## UPD
```sql
-- ============================================================
-- Комментарии из ЗАДАЧ (через форум)
-- ============================================================
SELECT 
  'project.task'                                AS `document_entities`,
  t.ID						                    AS `entities_id`,
  fm.ID                                         AS `message_id`,
  CASE 
    WHEN fm.SERVICE_TYPE = 1 THEN 'Системне повідомлення'
    WHEN fm.NEW_TOPIC = 'Y'  THEN 'Автоповідомлення'
    ELSE 'Коментар'
  END                                           AS `type`,
  'Примітка'                                    AS `subtype`,
  fm.POST_MESSAGE                               AS `text`,
  fm.POST_DATE                                  AS `data`,
  fm.AUTHOR_ID                                  AS `user_id`
FROM b_forum_message fm
STRAIGHT_JOIN b_tasks t 
  ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
WHERE fm.FORUM_ID = 11
  AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)

UNION ALL

-- ============================================================
-- Комментарии из ВСТРЕЧ (через соцсеть)
-- ============================================================
SELECT 
  'calendar.event'                              AS `Повязана модель документу`,
  cal_log.SOURCE_ID                             AS `id сутності`,
  slc.ID                                        AS `message_id`,
  'Коментар'                                    AS `Тип`,
  'Примітка'                                    AS `Підтип`,
  slc.MESSAGE                                   AS `Тіло тексту`,
  slc.LOG_DATE                                  AS `Дата`,
  slc.USER_ID                                   AS `Автор`
FROM b_sonet_log_comment slc
JOIN (
  SELECT ID, SOURCE_ID
  FROM b_sonet_log
  WHERE EVENT_ID = 'calendar'
) cal_log ON cal_log.ID = slc.LOG_ID
```
---

# 6. Зустрічі (дедупліковані)

```sql
SELECT 
ce.ID as 'external_id',
  ce.NAME AS `name`,
  ce.DATE_FROM AS `date_start`,
  ce.DATE_TO AS `date_end`,
  (SELECT GROUP_CONCAT(DISTINCT child.OWNER_ID SEPARATOR ', ')
   FROM b_calendar_event child
   WHERE child.PARENT_ID = ce.ID 
     AND child.DELETED = 'N'
     AND child.ID != child.PARENT_ID) AS `bitrix_user_id_participants`,
  ce.MEETING_HOST AS `bitrix_user_id_organizer`,
  ce.DESCRIPTION AS `description`
FROM b_calendar_event ce
WHERE ce.IS_MEETING = '1' 
  AND ce.DELETED = 'N'
  AND ce.ID = ce.PARENT_ID
ORDER BY ce.DATE_FROM
```

### Нюансы

В Bitrix для каждого участника создаётся своя копия события:

- **ID = PARENT_ID** — "главная" запись (организатор)
- **ID ≠ PARENT_ID** — копии для участников

Например, встреча ID=984 → записи 984, 988, 990, 992, 994 — одна и та же встреча для 5 человек.

Фильтр `ID = PARENT_ID` убирает дубли. Участников собираем из дочерних записей (`PARENT_ID = ce.ID AND ID != PARENT_ID`).


---

# 7. Файлы (вложения)

Все вложения проходят через модуль "Диск": `b_disk_attached_object` → `b_disk_object` → `b_file`

Физический путь файла на сервере: `/home/bitrix/www/upload/{SUBDIR}/{FILE_NAME}`

| ENTITY_TYPE | Что это |  ENTITY_ID → |
|---|---|---|
| `ForumMessageConnector` | Файлы в комментариях | `b_forum_message.ID` |
| `Connector\Task` | Файлы в задачах | `b_tasks.ID` |
| `Connector\Task\Result` | Файлы в результатах задач | result ID |
| `CalendarEventConnector` | Файлы во встречах | `b_calendar_event.ID` |


### Файлы в комментариях задач
```sql
SELECT 
  CONCAT('project_task_', t.ID) AS `task_external_id`,
  ao.ENTITY_ID AS `forum_message_id`,
  do.NAME AS `file_name`,
  do.SIZE AS `file_size`,
  bf.CONTENT_TYPE,
  CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS `file_path`,
  ao.CREATE_TIME AS `attached_at`
FROM b_disk_attached_object ao
JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
JOIN b_file bf ON bf.ID = do.FILE_ID
JOIN b_forum_message fm ON fm.ID = ao.ENTITY_ID AND fm.FORUM_ID = 11
JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
WHERE ao.ENTITY_TYPE = 'Bitrix\\Disk\\Uf\\ForumMessageConnector'
  AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
ORDER BY ao.CREATE_TIME
```

### Файлы в задачах
```sql
SELECT 
  CONCAT('project_task_', ao.ENTITY_ID) AS `task_external_id`,
  do.NAME AS `file_name`,
  do.SIZE AS `file_size`,
  bf.CONTENT_TYPE,
  CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS `file_path`,
  ao.CREATE_TIME AS `attached_at`
FROM b_disk_attached_object ao
JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
JOIN b_file bf ON bf.ID = do.FILE_ID
WHERE ao.ENTITY_TYPE = 'Bitrix\\Tasks\\Integration\\Disk\\Connector\\Task'
ORDER BY ao.CREATE_TIME
```

### Файлы во встречах
```sql
SELECT 
  ao.ENTITY_ID AS `calendar_event_id`,
  do.NAME AS `file_name`,
  do.SIZE AS `file_size`,
  bf.CONTENT_TYPE,
  CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS `file_path`,
  ao.CREATE_TIME AS `attached_at`
FROM b_disk_attached_object ao
JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
JOIN b_file bf ON bf.ID = do.FILE_ID
WHERE ao.ENTITY_TYPE = 'Bitrix\\Disk\\Uf\\CalendarEventConnector'
ORDER BY ao.CREATE_TIME
```

### Нюансы

- `file_path` — **относительный путь** на сервере Bitrix. Полный: `/home/bitrix/www/upload/{SUBDIR}/{FILE_NAME}` (зависит от инсталляции).