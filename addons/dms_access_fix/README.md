# DMS Access Fix

Bridge-модуль поверх платного **odoo_document_management_cloud_sync**
(WebbyCrown, v19.0.0.1). Закрывает дыру в правах доступа.

## Проблема

В оригинальном модуле:

- `data/access_rules.xml` и `post_init_hook` создают ACL для группы `base.group_user`
  («Role / User» = любой Internal User Odoo) с полным CRUD на все 12 моделей
  `document.*`. Из-за `implied_ids: group_document_user → base.group_user`
  ограничить доступ только для Document User одними рулс невозможно — все ACL
  через цепочку implied всё равно прорастают.
- `ir.rule` для `document.folder` / `document.file` объявлены с
  `perm_read = perm_write = perm_create = perm_unlink = True` и одним и тем же
  широким domain — нет разделения «read vs write».
- В форме папки поля `user_ids` / `group_ids` доступны для редактирования
  каждому кто видит папку.
- Группа `group_document_manager` не имеет `implied_ids`, то есть Manager
  без явной добавки в Internal User теряет интерфейс Odoo.

Итог: расшаривший доступ через `user_ids`/`group_ids` пользователь может
удалить чужую папку, переименовать её или поменять список доступов.

## Что делает фикс

1. **Override 12 ACL** для `group_document_user` (через XML IDs оригинала)
   и 2 ACL для `group_document_model_user` — переводит их на правильную
   группу и правильные `perm_*`.
2. **Добавляет 4 недостающих ACL для Manager** на `document.activity`,
   `document.comment`, `document.file.lock`, `document.file.filter`.
3. **Two-tier `ir.rule`** на `document.folder` / `document.file` /
   `document.share` (и для Model User): READ/CREATE — broad (всё что я вижу),
   WRITE/UNLINK — только owner / creator.
4. **Per-user правила** на favourites, file_lock, file_filter, comment.
5. **`implied_ids`** для `group_document_manager` → `group_document_user`.
6. **`_inherit='document.folder'`** + Python-guard в `write()` против правки
   `user_ids` / `group_ids` не-owner'ом — отдельная защита, помимо ACL.
7. **Inherit-view**: поля `user_ids` / `group_ids` `readonly="not can_manage_access"`.
8. **`post_init_hook`** одноразово удаляет ACL `base.group_user` и stale broad
   `ir.rule` оригинала.

## Установка

Модуль помечен `auto_install: True` и `depends: ['odoo_document_management_cloud_sync']`.
Достаточно положить директорию рядом с `odoo_document_management_cloud_sync/`
в `addons/`, сделать `update apps list` — Odoo подхватит и поставит
автоматически.

Вручную:

```bash
docker exec stage-odoo-odoo-1 odoo \
  --db_host=... --db_user=... --db_password=... \
  --no-http --stop-after-init \
  -i dms_access_fix -d <db>
```

## Поведение после установки

| Кто | Что может |
|---|---|
| Document Manager | всё на всех папках / файлах / share |
| Owner папки/файла | всё на своих папках и их содержимом |
| Document User в `user_ids` или через `group_ids` папки | смотреть, создавать новые файлы внутри, **не** удалять/переименовывать чужие, **не** менять `user_ids`/`group_ids` |
| Internal User без Document-группы | вообще ничего из Documents |

## Сценарии деплоя

### Чистая установка на проде

1. Поставить оригинальный `odoo_document_management_cloud_sync` от вендора.
2. Положить `dms_access_fix/` рядом, обновить список модулей.
3. Модуль установится автоматически. `post_init_hook` вычистит ACL дыру.

### Обновление оригинала от вендора

После того как вендор выпустит новую версию оригинала и её установят:

```bash
docker exec <container> odoo --stop-after-init -u dms_access_fix -d <db>
```

`upgrade` нашего модуля заново применит overrides, но **не вызовет** `post_init_hook`.
Если новая версия оригинала вернула ACL для `base.group_user` —
сделать `uninstall + install` нашего модуля, либо вручную:

```sql
DELETE FROM ir_model_access
WHERE group_id = (SELECT res_id FROM ir_model_data WHERE module='base' AND name='group_user')
  AND model_id IN (SELECT id FROM ir_model WHERE model LIKE 'document.%');
```

## Ограничения

- Не трогает контроллеры с `sudo()` для публичных share-ссылок
  (`/document/share/<token>`, `/document/download/<token>`,
  `/document/preview/<token>`) — там `sudo()` корректен для anonymous access.
- Не вмешивается в OAuth callback'и.
- JS-кнопки удаления в дереве/модалках остаются видимыми — при попытке
  удалить backend бросит AccessError, Odoo покажет стандартный toast.
- Не поддерживает фичу «дать пользователю write-доступ к чужой папке без
  передачи owner'а». Если такая модель нужна — добавить отдельное поле
  `editor_ids` и доработать ir.rule.
