from odoo import fields, models


class BitrixTaskEmployeeLink(models.Model):
    _name = 'bitrix.task.employee.link'
    _description = 'Bitrix Task Employee Link'

    task_id = fields.Many2one(
        'project.task',
        string='Task',
        required=True,
        index=True,
        ondelete='cascade',
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string='Employee',
        required=True,
        index=True,
        ondelete='cascade',
    )
    role = fields.Selection(
        [
            ('responsible', 'Responsible'),
            ('accomplice', 'Accomplice'),
            ('auditor', 'Auditor'),
            ('originator', 'Originator'),
            ('creator', 'Creator'),
            ('participant', 'Participant'),
        ],
        string='Role',
        required=True,
        index=True,
    )

    def init(self):
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS bitrix_task_employee_link_task_employee_role_uniq
            ON bitrix_task_employee_link (task_id, employee_id, role)
            """
        )

        # Deduplicate responsible links: keep lowest id per task_id
        self.env.cr.execute(
            """
            DELETE FROM bitrix_task_employee_link
            WHERE role = 'responsible'
              AND id NOT IN (
                  SELECT MIN(id)
                  FROM bitrix_task_employee_link
                  WHERE role = 'responsible'
                  GROUP BY task_id
              )
            """
        )

        # Partial unique index: at most one responsible per task
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS bitrix_task_employee_link_one_responsible_per_task
            ON bitrix_task_employee_link (task_id) WHERE role = 'responsible'
            """
        )

        # Idempotent migration from legacy m2m relation tables.
        self.env.cr.execute(
            """
            DO $$
            BEGIN
                IF to_regclass('project_task_bitrix_employee_rel') IS NOT NULL THEN
                    INSERT INTO bitrix_task_employee_link (task_id, employee_id, role, create_date, write_date)
                    SELECT rel.task_id, rel.employee_id, 'responsible', NOW(), NOW()
                    FROM project_task_bitrix_employee_rel rel
                    ON CONFLICT (task_id, employee_id, role) DO NOTHING;
                END IF;
            END
            $$;
            """
        )
        self.env.cr.execute(
            """
            DO $$
            BEGIN
                IF to_regclass('project_task_bitrix_participant_rel') IS NOT NULL THEN
                    INSERT INTO bitrix_task_employee_link (task_id, employee_id, role, create_date, write_date)
                    SELECT rel.task_id, rel.employee_id, 'participant', NOW(), NOW()
                    FROM project_task_bitrix_participant_rel rel
                    ON CONFLICT (task_id, employee_id, role) DO NOTHING;
                END IF;
            END
            $$;
            """
        )
