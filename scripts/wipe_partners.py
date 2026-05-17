"""
Удаление всех контактов res.partner кроме системных.

DESTRUCTIVE. Использовать только в dev/stage. После запуска нужно перезапустить
миграцию сотрудников и сделать relink в bitrix_migration.

Защищены от удаления:
  - партнёры, привязанные к res.users.partner_id (admin, OdooBot, Public, etc.)
  - партнёры, привязанные к res.company.partner_id (главная компания)
  - партнёры с external_id в ir.model.data (системные/seed данные)

Запуск (для stage-odoo контейнера):

    docker exec -i stage-odoo-odoo-1 bash -c \
      "odoo shell -c /etc/odoo/odoo.conf -d odoo \
       --db_host=odoodb --db_user=odoo --db_password=myodoo \
       --no-http --http-port=8071 --gevent-port=8072" \
      < scripts/wipe_partners.py
"""

keep = set()
keep |= set(env['res.users'].sudo().with_context(active_test=False).search([]).mapped('partner_id').ids)
keep |= set(env['res.company'].sudo().search([]).mapped('partner_id').ids)
env.cr.execute("SELECT res_id FROM ir_model_data WHERE model='res.partner'")
keep |= {r[0] for r in env.cr.fetchall() if r[0]}

to_del = env['res.partner'].sudo().with_context(active_test=False).search([('id', 'not in', list(keep))])
ids = to_del.ids
print(f'Protected: {sorted(keep)}')
print(f'Deleting {len(ids)} partners')

# Снимаем FK-зависимости отдельными транзакциями: ошибка на одном шаге
# не должна откатывать предыдущие успешные шаги.
steps = [
    ('DELETE FROM calendar_event_res_partner_rel WHERE res_partner_id = ANY(%s)', (ids,)),
    ('DELETE FROM mail_followers WHERE partner_id = ANY(%s)',                     (ids,)),
    ('UPDATE mail_message SET author_id = NULL WHERE author_id = ANY(%s)',        (ids,)),
    # RESTRICT FK — обязаны убрать ДО unlink()
    ('DELETE FROM calendar_filters WHERE partner_id = ANY(%s)',                   (ids,)),
    ('DELETE FROM mail_push_device WHERE partner_id = ANY(%s)',                   (ids,)),
    ('DELETE FROM mail_scheduled_message WHERE author_id = ANY(%s)',              (ids,)),
    ('DELETE FROM payment_token WHERE partner_id = ANY(%s)',                      (ids,)),
    ('DELETE FROM payment_transaction WHERE partner_id = ANY(%s)',                (ids,)),
    ('DELETE FROM project_collaborator WHERE partner_id = ANY(%s)',               (ids,)),
    ('DELETE FROM snailmail_letter WHERE partner_id = ANY(%s)',                   (ids,)),
    ('DELETE FROM account_bank_statement_line WHERE partner_id = ANY(%s)',        (ids,)),
    ('DELETE FROM account_payment WHERE partner_id = ANY(%s)',                    (ids,)),
    ('DELETE FROM account_payment_register WHERE partner_id = ANY(%s)',           (ids,)),
    ('DELETE FROM account_move_line WHERE partner_id = ANY(%s)',                  (ids,)),
    ('DELETE FROM account_move WHERE partner_id = ANY(%s) OR commercial_partner_id = ANY(%s)', (ids, ids)),
    # hr_work_location.address_id NOT NULL — удаляем строки целиком
    ('DELETE FROM hr_work_location WHERE address_id = ANY(%s)',                   (ids,)),
]
for sql, params in steps:
    try:
        env.cr.execute(sql, params)
        if env.cr.rowcount:
            print(f'  ok rows={env.cr.rowcount}: {sql[:55]}...')
        env.cr.commit()
    except Exception as e:
        env.cr.rollback()
        print(f'  SKIP {sql[:55]}: {str(e)[:200]}')

try:
    to_del.unlink()
    env.cr.commit()
    print('Unlinked OK')
except Exception as e:
    env.cr.rollback()
    print('FAIL:', type(e).__name__, str(e)[:500])

env.cr.execute("SELECT COUNT(*) FROM res_partner")
print('Remaining partners:', env.cr.fetchone()[0])
