# Этапы проекта
```sql
SELECT 
  ID,
  TITLE AS `name`
FROM b_tasks_stages
WHERE ENTITY_TYPE IN ('G', 'A')
  AND TITLE IS NOT NULL AND TITLE != ''
ORDER BY ID
```

`b_tasks_stages` — это **канбан-стадии задач**, а не этапы проектов в понимании Odoo:

**4 типа (ENTITY_TYPE):**

| Тип | Что это | Кол-во | ENTITY_ID указывает на |
|---|---|---|---|
| **G** | Стадии канбана рабочих групп | 546 | ID группы (`b_sonet_group`) |
| **U** | Личные стадии канбана пользователей | 1107 | ID пользователя |
| **P** | Системные периоды проектов (PERIOD1–6) | 1379 | ID группы-проекта |
| **A** | Стадии потоков (Flows) | 9 | ID потока |

Ключевой момент — стадии привязаны к конкретным группам/пользователям, а не глобальные. То есть у группы "Маркетинг" свои 3 стадии, у "Development" свои 4, и т.д. Итого 546 записей по типу G — это по 3–6 стадий на каждую из ~100 групп.

**P** — вообще пустые названия, это внутренние системные периоды для диаграммы Ганта.

Вопрос — **для листа "Етапи проекту"** в Odoo вам нужны:

1. **Уникальные названия стадий** из всех групп (дедупликация, ~44 штуки)?
2. **Все стадии с привязкой к группе** (546 строк)?
3. Или что-то другое — может, в Odoo этапы проектов это другая сущность?

Потому что в Bitrix у проектов нет "этапов проекта" как отдельной сущности — есть только стадии канбана для задач внутри группы. Это ближе к `project.task.type` в Odoo, а не к `project.project.stage`.


# Теги (мітки)
```sql
SELECT 
  MIN(ID) AS ID,
  NAME AS `name`
FROM b_tasks_label
WHERE NAME IS NOT NULL AND NAME != ''
GROUP BY NAME
ORDER BY NAME
```
В Bitrix метки — **не глобальные**. Одна и та же метка "Service Desk" дублируется 131 раз — отдельная запись для каждого пользователя и каждой группы, где она используется. Всего 616 записей, но уникальных имён — **214**.

Структура `b_tasks_label`:

| Поле | Смысл |
|---|---|
| ID | Уникальный ID записи |
| NAME | Название метки |
| USER_ID | Привязка к пользователю (личный канбан) |
| GROUP_ID | Привязка к группе |

То есть если метка "Backlog" используется в 14 группах/у 14 пользователей — в таблице 14 строк с разными ID, но одинаковым NAME.

Для Odoo теги глобальные, поэтому нужна **дедупликация**

Это даст **214 уникальных тегов**, для каждого — минимальный ID из дублей (как "канонический" идентификатор).

Соответственно, в запросе задач (лист 2) тоже нужно маппить TAG_ID через имя, а не напрямую по ID, потому что одна задача может ссылаться на TAG_ID=534 ("Service Desk" в группе 215), а каноническим будет другой ID. Это нужно учесть при импорте — либо маппить по имени, либо сделать таблицу соответствий.

### НО, ЕСТЬ ВАЖНЫЙ МОМЕНТ

В тегах проектов всего **11 тегов**, и это совершенно отдельная сущность от тегов задач.

| Источник | Таблица | Есть ID | Кол-во |
|---|---|---|---|
| Теги задач | `b_tasks_label` | Да (но дублируются) | 616 (214 уникальных) |
| Теги проектів | `b_sonet_group_tag` | **Нет** (только GROUP_ID + NAME) | 11 (9 уникальных) |

Это разные сущности, которые никак не связаны между собой. Но в Odoo `project.tags` — один справочник и для проектов, и для задач.


# Шаблон для проектів
```sql
SELECT 
  CONCAT('project_project_', g.ID) AS `external id проекту`,
  g.NAME AS `name`,
  NULL AS `Етап проекту stage_id`,
  NULL AS `Клієнт partner_id`,
  (SELECT GROUP_CONCAT(gt.NAME SEPARATOR ', ') 
   FROM b_sonet_group_tag gt WHERE gt.GROUP_ID = g.ID) AS `Мітки tag_ids`,
  g.OWNER_ID AS `Керівник проекту user_id`,
  g.PROJECT_DATE_START AS `Початкова дата date_start`,
  g.PROJECT_DATE_FINISH AS `Термін дії date`,
  NULL AS `Розподілений час`,
  NULL AS `Доступність`,
  g.DESCRIPTION AS `Опис`
FROM b_sonet_group g
WHERE g.PROJECT = 'Y'
ORDER BY g.ID
```

По поводу `stage_id` для проектів — в Bitrix у `b_sonet_group` нет прямого поля "текущий этап проекта". Этапы (`b_tasks_stages` с `ENTITY_TYPE = 'P'`) привязаны к проектам, но у самого проекта нет "текущий этап". Это этапы для задач внутри проекта, а не статус самого проекта. Единственный признак — `CLOSED = 'Y'/'N'`.

# Шаблон для задач
```sql
SELECT 
  CONCAT('project_task_', t.ID) AS `external id завдання`,
  t.TITLE AS `name завдання`,
  CASE WHEN t.GROUP_ID > 0 
    THEN CONCAT('project_project_', t.GROUP_ID) 
    ELSE NULL END AS `external id проекту`,
  GROUP_CONCAT(DISTINCT m.USER_ID SEPARATOR ', ') AS `Уповноважені user_ids`,
  (SELECT GROUP_CONCAT(DISTINCT tt.TAG_ID SEPARATOR ', ')
   FROM b_tasks_task_tag tt WHERE tt.TASK_ID = t.ID) AS `Мітки tag_ids`,
  t.DEADLINE AS `Кінцевий термін date_deadline`,
  t.DESCRIPTION AS `Опис`,
  t.STAGE_ID AS `Етап завдання stage_id`,
  CASE WHEN t.PARENT_ID > 0 
    THEN CONCAT('project_task_', t.PARENT_ID) 
    ELSE NULL END AS `Підзадача (parent_id)`
FROM b_tasks t
LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE IN ('R','A')
WHERE (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
GROUP BY t.ID
ORDER BY t.ID
```

**1. TAG_ID — не сматчится с дедуплицированным справочником тегов**

Тег "Service Desk" в `b_tasks_label` — это 131 строк с разными ID. Задачи ссылаются на разные ID (125, 127, 128…). А в справочнике тегов мы берём `MIN(ID)`. Итого TAG_ID задачи **не совпадёт** с каноническим ID из листа 3.

Решение — в запросе задач выводить **имя тега**, а не TAG_ID

**2. STAGE_ID — в основном пустой**

| Ситуация | Кол-во |
|---|---|
| Нет стадии (0 или NULL) | 125 279 |
| Валидная стадия | 10 563 |
| Битая ссылка | 13 |

92% задач не имеют STAGE_ID. Стадии есть только у задач в группах с настроенным канбаном. Это нормально, просто нужно быть готовым, что большинство задач в Odoo не получат этап.


# Зустрічі
```sql
SELECT 
  ce.NAME AS `Тема зустрічі`,
  ce.DATE_FROM AS `Дата початку`,
  ce.DATE_TO AS `Дата кінцева`,
  (SELECT GROUP_CONCAT(DISTINCT child.OWNER_ID SEPARATOR ', ')
   FROM b_calendar_event child
   WHERE child.PARENT_ID = ce.ID 
     AND child.DELETED = 'N'
     AND child.ID != child.PARENT_ID) AS `Учасники`,
  ce.MEETING_HOST AS `Організатор`,
  ce.DESCRIPTION AS `Опис`
FROM b_calendar_event ce
WHERE ce.IS_MEETING = '1' 
  AND ce.DELETED = 'N'
  AND ce.ID = ce.PARENT_ID
ORDER BY ce.DATE_FROM
```
В Bitrix для каждого участника создаётся своя копия события:

- **ID = PARENT_ID** — это "главная" запись (организатор)
- **PARENT_ID = ID другого события** — копии для участников (OWNER_ID = участник)

Например, встреча ID=984 → записи 984, 988, 990, 992, 994 — одна и та же встреча для 5 человек.Вот — **5 821 уникальных встреч** вместо 24 659 строк. Дубли убираются фильтром `ID = PARENT_ID`. Участников собираем из дочерних записей.

Логика: берём только "родительские" записи (`ID = PARENT_ID`), а участников собираем из дочерних копий (`PARENT_ID = ce.ID AND ID != PARENT_ID`).


# Коментарі
```sql
SELECT 
  'project.task' AS `Повязана модель документу`,
  CONCAT('project_task_', t.ID) AS `id сутності`,
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

В таблице `b_forum_message` хранятся три типа записей:

**SERVICE_TYPE = 1** — это системные сообщения

**NEW_TOPIC = 'Y'** — Это техническая запись, первое сообщение в каждом топике форума. 

**Всё остальное** (SERVICE_TYPE IS NULL + NEW_TOPIC = 'N') — это настоящие комментарии, которые люди писали руками в задаче.
