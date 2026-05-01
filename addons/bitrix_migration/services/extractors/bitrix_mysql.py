import logging
import re
from datetime import date, datetime

import pymysql
from pymysql.cursors import DictCursor

_logger = logging.getLogger(__name__)

# ENTITY_TYPE for calendar disk attachments must be discovered on production.
# Run the diagnostic query from the rollout plan and replace this value.
MEETING_ATTACHMENT_ENTITY_TYPE = 'Bitrix\\Disk\\Uf\\CalendarEventConnector'


class BitrixMySQLExtractor:
    """MySQL connector for extracting data from Bitrix24 database."""

    # ── Projects ──────────────────────────────────────────────────────
    SQL_PROJECTS_TEMPLATE = """
        SELECT
            g.ID AS external_id,
            g.NAME AS name,
            CASE WHEN g.PROJECT = 'Y' THEN 'project' ELSE 'workgroup' END AS type,
            CASE WHEN g.CLOSED = 'Y' THEN 1 ELSE 0 END AS closed,
            g.OWNER_ID AS owner_bitrix_id,
            (SELECT GROUP_CONCAT(gt.NAME SEPARATOR ', ')
             FROM b_sonet_group_tag gt WHERE gt.GROUP_ID = g.ID) AS tags,
            u.ID AS user_id,
            g.PROJECT_DATE_START AS date_start,
            g.PROJECT_DATE_FINISH AS date_end,
            g.DESCRIPTION AS description
        FROM b_sonet_group g
        LEFT JOIN b_user u ON u.ID = g.OWNER_ID
        WHERE ({project_where_clause})
           OR g.ID IN (
               SELECT DISTINCT t.GROUP_ID FROM b_tasks t
               WHERE t.GROUP_ID > 0
                 AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
                 AND {task_where_clause}
           )
        ORDER BY g.ID
    """

    SQL_PROJECT_BY_ID = """
        SELECT
            g.ID AS external_id,
            g.NAME AS name,
            CASE WHEN g.PROJECT = 'Y' THEN 'project' ELSE 'workgroup' END AS type,
            CASE WHEN g.CLOSED = 'Y' THEN 1 ELSE 0 END AS closed,
            g.OWNER_ID AS owner_bitrix_id,
            (SELECT GROUP_CONCAT(gt.NAME SEPARATOR ', ')
             FROM b_sonet_group_tag gt WHERE gt.GROUP_ID = g.ID) AS tags,
            u.ID AS user_id,
            g.PROJECT_DATE_START AS date_start,
            g.PROJECT_DATE_FINISH AS date_end,
            g.DESCRIPTION AS description
        FROM b_sonet_group g
        LEFT JOIN b_user u ON u.ID = g.OWNER_ID
        WHERE g.ID = %s
    """

    # ── Tasks ─────────────────────────────────────────────────────────
    SQL_TASKS_TEMPLATE = """
        SELECT
            t.ID AS external_id,
            t.TITLE AS name,
            CASE WHEN t.GROUP_ID > 0 THEN t.GROUP_ID ELSE NULL END AS project_external_id,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'R' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS responsible_user_ids,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'A' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS accomplice_user_ids,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'U' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS auditor_user_ids,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'O' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS originator_user_ids,
            (SELECT GROUP_CONCAT(DISTINCT tl.NAME SEPARATOR ', ')
             FROM b_tasks_task_tag tt
             JOIN b_tasks_label tl ON tl.ID = tt.TAG_ID
             WHERE tt.TASK_ID = t.ID) AS tags,
            t.DEADLINE AS date_deadline,
            {task_created_expr},
            t.DESCRIPTION AS description,
            t.STAGE_ID AS stage_id,
            CASE WHEN t.PARENT_ID > 0 THEN t.PARENT_ID ELSE NULL END AS parent_id,
            t.CREATED_BY AS creator_bitrix_id,
            t.STATUS AS status_code
        FROM b_tasks t
        LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE IN ('R', 'A', 'U', 'O')
        WHERE (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
          AND {task_where_clause}
        GROUP BY t.ID
        ORDER BY t.ID
    """

    SQL_COUNT_PROJECTS_TEMPLATE = """
        SELECT COUNT(*) AS cnt
        FROM b_sonet_group g
        WHERE ({project_where_clause})
           OR g.ID IN (
               SELECT DISTINCT t.GROUP_ID FROM b_tasks t
               WHERE t.GROUP_ID > 0
                 AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
                 AND {task_where_clause}
           )
    """

    SQL_COUNT_TASKS_TEMPLATE = """
        SELECT COUNT(*) AS cnt
        FROM b_tasks t
        WHERE (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
          AND {task_where_clause}
    """

    SQL_SINGLE_TASK_TEMPLATE = """
        SELECT
            t.ID AS external_id,
            t.TITLE AS name,
            CASE WHEN t.GROUP_ID > 0 THEN t.GROUP_ID ELSE NULL END AS project_external_id,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'R' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS responsible_user_ids,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'A' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS accomplice_user_ids,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'U' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS auditor_user_ids,
            GROUP_CONCAT(DISTINCT CASE WHEN m.TYPE = 'O' THEN m.USER_ID END ORDER BY m.USER_ID SEPARATOR ', ') AS originator_user_ids,
            (SELECT GROUP_CONCAT(DISTINCT tl.NAME SEPARATOR ', ')
             FROM b_tasks_task_tag tt
             JOIN b_tasks_label tl ON tl.ID = tt.TAG_ID
             WHERE tt.TASK_ID = t.ID) AS tags,
            t.DEADLINE AS date_deadline,
            {task_created_expr},
            t.DESCRIPTION AS description,
            t.STAGE_ID AS stage_id,
            CASE WHEN t.PARENT_ID > 0 THEN t.PARENT_ID ELSE NULL END AS parent_id,
            t.CREATED_BY AS creator_bitrix_id,
            t.STATUS AS status_code
        FROM b_tasks t
        LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE IN ('R', 'A', 'U', 'O')
        WHERE t.ID = %s
        GROUP BY t.ID
    """

    # ── Tags ──────────────────────────────────────────────────────────
    SQL_TAGS = """
        SELECT merged.ID AS id, merged.name, merged.source
        FROM (
            SELECT
                MIN(tl.ID) AS ID,
                tl.NAME AS name,
                'task' AS source
            FROM b_tasks_label tl
            WHERE tl.NAME IS NOT NULL
              AND tl.NAME != ''
            GROUP BY tl.NAME

            UNION ALL

            SELECT
                900000 + (@project_tag_rownum := @project_tag_rownum + 1) AS ID,
                pt.NAME AS name,
                'project' AS source
            FROM (
                SELECT DISTINCT gt.NAME
                FROM b_sonet_group_tag gt
                JOIN b_sonet_group g ON g.ID = gt.GROUP_ID
                WHERE g.PROJECT = 'Y'
                  AND gt.NAME IS NOT NULL
                  AND gt.NAME != ''
                  AND gt.NAME NOT IN (
                      SELECT tl2.NAME
                      FROM b_tasks_label tl2
                      WHERE tl2.NAME IS NOT NULL
                        AND tl2.NAME != ''
                  )
                ORDER BY gt.NAME
            ) pt
            CROSS JOIN (SELECT @project_tag_rownum := 0) vars
        ) merged
        ORDER BY merged.name
    """

    # ── Stages ────────────────────────────────────────────────────────
    SQL_STAGES = """
        SELECT
            s.ID AS id,
            s.TITLE AS name,
            s.ENTITY_TYPE AS entity_type,
            s.ENTITY_ID AS entity_id
        FROM b_tasks_stages s
        WHERE s.ENTITY_TYPE = 'G'
          AND s.TITLE IS NOT NULL AND s.TITLE != ''
        ORDER BY s.ID
    """

    SQL_STAGES_WITH_PROJECTS = """
        SELECT
            g.ID AS project_external_id,
            g.NAME AS project_name,
            s.ID AS stage_id,
            s.TITLE AS stage_name
        FROM b_sonet_group g
        LEFT JOIN b_tasks_stages s
            ON s.ENTITY_TYPE = 'G'
           AND s.ENTITY_ID = g.ID
           AND s.TITLE IS NOT NULL
           AND s.TITLE != ''
        ORDER BY g.ID, s.TITLE
    """

    # ── Comments (real only) ──────────────────────────────────────────
    SQL_COMMENTS_TEMPLATE = """
        SELECT
            'project.task' AS document_model,
            t.ID AS entity_id,
            fm.ID AS message_id,
            'comment' AS type,
            fm.POST_MESSAGE AS body,
            fm.POST_DATE AS date,
            fm.AUTHOR_ID AS author_bitrix_id
        FROM b_forum_message fm
        STRAIGHT_JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE fm.FORUM_ID = 11
          AND fm.SERVICE_TYPE IS NULL
          AND fm.NEW_TOPIC = 'N'
          AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
          AND {task_where_clause}
        ORDER BY fm.POST_DATE
    """

    SQL_COMMENTS_FOR_TASK = """
        SELECT
            'project.task' AS document_model,
            t.ID AS entity_id,
            fm.ID AS message_id,
            CASE
                WHEN fm.SERVICE_TYPE = 1 THEN 'system'
                WHEN fm.NEW_TOPIC = 'Y' THEN 'auto'
                ELSE 'comment'
            END AS type,
            fm.POST_MESSAGE AS body,
            fm.POST_DATE AS date,
            fm.AUTHOR_ID AS author_bitrix_id
        FROM b_forum_message fm
        STRAIGHT_JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE fm.FORUM_ID = 11
          AND fm.SERVICE_TYPE IS NULL
          AND fm.NEW_TOPIC = 'N'
          AND t.ID = %s
          AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
        ORDER BY fm.POST_DATE
    """

    # ── Users ─────────────────────────────────────────────────────────
    SQL_USERS = """
        SELECT ID, LOGIN, EMAIL, NAME, LAST_NAME, ACTIVE
        FROM b_user
        ORDER BY ID
    """

    # ── Task members ──────────────────────────────────────────────────
    SQL_TASK_MEMBERS = """
        SELECT TASK_ID, USER_ID, TYPE
        FROM b_tasks_member
        WHERE TYPE IN ('R', 'A', 'U', 'O')
        ORDER BY TASK_ID
    """
    SQL_TASK_STATUS_BY_IDS_TEMPLATE = """
        SELECT t.ID AS task_external_id, t.STATUS AS status_code
        FROM b_tasks t
        WHERE t.ID IN ({placeholders})
    """

    # ── Task Attachments ──────────────────────────────────────────────
    SQL_TASK_ATTACHMENTS_TEMPLATE = """
        SELECT
            ao.ENTITY_ID AS task_external_id,
            do.NAME AS file_name,
            do.SIZE AS file_size,
            bf.CONTENT_TYPE AS content_type,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS file_path,
            ao.CREATE_TIME AS attached_at
        FROM b_disk_attached_object ao
        JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
        JOIN b_file bf ON bf.ID = do.FILE_ID
        JOIN b_tasks t ON t.ID = ao.ENTITY_ID
        WHERE ao.ENTITY_TYPE = 'Bitrix\\\\Tasks\\\\Integration\\\\Disk\\\\Connector\\\\Task'
          AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
          AND {task_where_clause}
        ORDER BY ao.CREATE_TIME
    """

    SQL_TASK_ATTACHMENTS_FOR_TASK = """
        SELECT
            ao.ENTITY_ID AS task_external_id,
            do.NAME AS file_name,
            do.SIZE AS file_size,
            bf.CONTENT_TYPE AS content_type,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS file_path,
            ao.CREATE_TIME AS attached_at
        FROM b_disk_attached_object ao
        JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
        JOIN b_file bf ON bf.ID = do.FILE_ID
        WHERE ao.ENTITY_TYPE = 'Bitrix\\\\Tasks\\\\Integration\\\\Disk\\\\Connector\\\\Task'
          AND ao.ENTITY_ID = %s
        ORDER BY ao.CREATE_TIME
    """

    # ── Comment Attachments ───────────────────────────────────────────
    SQL_COMMENT_ATTACHMENTS_TEMPLATE = """
        SELECT
            t.ID AS task_external_id,
            ao.ENTITY_ID AS forum_message_id,
            do.NAME AS file_name,
            do.SIZE AS file_size,
            bf.CONTENT_TYPE AS content_type,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS file_path,
            ao.CREATE_TIME AS attached_at
        FROM b_disk_attached_object ao
        JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
        JOIN b_file bf ON bf.ID = do.FILE_ID
        JOIN b_forum_message fm ON fm.ID = ao.ENTITY_ID AND fm.FORUM_ID = 11
        JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE ao.ENTITY_TYPE = 'Bitrix\\\\Disk\\\\Uf\\\\ForumMessageConnector'
          AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
          AND {task_where_clause}
        ORDER BY ao.CREATE_TIME
    """

    SQL_COMMENT_ATTACHMENTS_FOR_TASK = """
        SELECT
            t.ID AS task_external_id,
            ao.ENTITY_ID AS forum_message_id,
            do.NAME AS file_name,
            do.SIZE AS file_size,
            bf.CONTENT_TYPE AS content_type,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS file_path,
            ao.CREATE_TIME AS attached_at
        FROM b_disk_attached_object ao
        JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
        JOIN b_file bf ON bf.ID = do.FILE_ID
        JOIN b_forum_message fm ON fm.ID = ao.ENTITY_ID AND fm.FORUM_ID = 11
        JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE ao.ENTITY_TYPE = 'Bitrix\\\\Disk\\\\Uf\\\\ForumMessageConnector'
          AND t.ID = %s
          AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
        ORDER BY ao.CREATE_TIME
    """

    # ── Meetings ──────────────────────────────────────────────────────
    SQL_MEETINGS_TEMPLATE = """
        SELECT
            ce.ID AS external_id,
            ce.NAME AS name,
            ce.DATE_FROM AS date_start,
            ce.DATE_TO AS date_end,
            (SELECT GROUP_CONCAT(DISTINCT child.OWNER_ID SEPARATOR ', ')
             FROM b_calendar_event child
             WHERE child.PARENT_ID = ce.ID
               AND child.DELETED = 'N'
               AND child.ID != child.PARENT_ID) AS participant_bitrix_ids,
            ce.MEETING_HOST AS organizer_bitrix_id,
            ce.DESCRIPTION AS description,
            {forum_topic_expr}
        FROM b_calendar_event ce
        WHERE ce.IS_MEETING = '1'
          AND ce.DELETED = 'N'
          AND ce.ID = ce.PARENT_ID
        ORDER BY ce.DATE_FROM
    """

    SQL_MEETING_FORUM_COMMENTS = """
        SELECT
            'calendar.event' AS document_model,
            ce.ID AS entity_id,
            fm.ID AS message_id,
            'comment' AS type,
            fm.POST_MESSAGE AS body,
            fm.POST_DATE AS date,
            fm.AUTHOR_ID AS author_bitrix_id
        FROM b_forum_message fm
        STRAIGHT_JOIN b_calendar_event ce ON ce.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE fm.SERVICE_TYPE IS NULL
          AND fm.NEW_TOPIC = 'N'
          AND ce.IS_MEETING = '1'
          AND ce.DELETED = 'N'
          AND ce.ID = ce.PARENT_ID
        ORDER BY fm.POST_DATE
    """

    SQL_MEETING_SONET_COMMENTS = """
        SELECT
            'calendar.event' AS document_model,
            ce.ID AS entity_id,
            slc.ID AS message_id,
            'comment' AS type,
            COALESCE(NULLIF(slc.MESSAGE, ''), slc.TEXT_MESSAGE) AS body,
            slc.LOG_DATE AS date,
            slc.USER_ID AS author_bitrix_id
        FROM b_sonet_log sl
        JOIN b_sonet_log_comment slc ON slc.LOG_ID = sl.ID
        JOIN b_calendar_event ce ON ce.ID = sl.SOURCE_ID
        WHERE sl.MODULE_ID = 'calendar'
          AND ce.IS_MEETING = '1'
          AND ce.DELETED = 'N'
          AND ce.ID = ce.PARENT_ID
        ORDER BY slc.LOG_DATE
    """

    SQL_MEETING_ATTACHMENTS = """
        SELECT
            ce.ID AS meeting_external_id,
            do.NAME AS file_name,
            do.SIZE AS file_size,
            bf.CONTENT_TYPE AS content_type,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS file_path,
            ao.CREATE_TIME AS attached_at
        FROM b_disk_attached_object ao
        JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
        JOIN b_file bf ON bf.ID = do.FILE_ID
        JOIN b_calendar_event ce ON ce.ID = ao.ENTITY_ID
                                AND ce.IS_MEETING = '1'
                                AND ce.DELETED = 'N'
                                AND ce.ID = ce.PARENT_ID
        WHERE ao.ENTITY_TYPE = %s
        ORDER BY ao.CREATE_TIME
    """

    SQL_MEETING_FORUM_COMMENT_ATTACHMENTS = """
        SELECT
            ce.ID AS meeting_external_id,
            ao.ENTITY_ID AS forum_message_id,
            do.NAME AS file_name,
            do.SIZE AS file_size,
            bf.CONTENT_TYPE AS content_type,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS file_path,
            ao.CREATE_TIME AS attached_at
        FROM b_disk_attached_object ao
        JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
        JOIN b_file bf ON bf.ID = do.FILE_ID
        JOIN b_forum_message fm ON fm.ID = ao.ENTITY_ID
        JOIN b_calendar_event ce ON ce.FORUM_TOPIC_ID = fm.TOPIC_ID
                                AND ce.IS_MEETING = '1'
                                AND ce.DELETED = 'N'
                                AND ce.ID = ce.PARENT_ID
        WHERE ao.ENTITY_TYPE = 'Bitrix\\\\Disk\\\\Uf\\\\ForumMessageConnector'
        ORDER BY ao.CREATE_TIME
    """

    SQL_MEETING_SONET_COMMENT_ATTACHMENTS = """
        SELECT
            ce.ID AS meeting_external_id,
            ao.ENTITY_ID AS forum_message_id,
            do.NAME AS file_name,
            do.SIZE AS file_size,
            bf.CONTENT_TYPE AS content_type,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS file_path,
            ao.CREATE_TIME AS attached_at
        FROM b_disk_attached_object ao
        JOIN b_disk_object do ON do.ID = ao.OBJECT_ID
        JOIN b_file bf ON bf.ID = do.FILE_ID
        JOIN b_sonet_log_comment slc ON slc.ID = ao.ENTITY_ID
        JOIN b_sonet_log sl ON sl.ID = slc.LOG_ID
        JOIN b_calendar_event ce ON ce.ID = sl.SOURCE_ID
                                AND ce.IS_MEETING = '1'
                                AND ce.DELETED = 'N'
                                AND ce.ID = ce.PARENT_ID
        WHERE ao.ENTITY_TYPE IN (
            'Bitrix\\\\Disk\\\\Uf\\\\BlogPostCommentConnector',
            'Bitrix\\\\Disk\\\\Uf\\\\SonetCommentConnector'
        )
        ORDER BY ao.CREATE_TIME
    """

    # ── Departments ───────────────────────────────────────────────────
    SQL_DEPARTMENTS = """
        SELECT
            s.ID                    AS dept_id,
            s.NAME                  AS dept_name,
            s.IBLOCK_SECTION_ID     AS parent_dept_id,
            us.UF_HEAD              AS head_user_id,
            s.DEPTH_LEVEL
        FROM b_iblock_section s
        LEFT JOIN b_uts_iblock_1_section us ON us.VALUE_ID = s.ID
        WHERE s.IBLOCK_ID = 1
        ORDER BY s.DEPTH_LEVEL, s.LEFT_MARGIN
    """

    # ── Employees with departments ────────────────────────────────────
    SQL_EMPLOYEES = """
        SELECT
            u.ID                                    AS user_id,
            u.LOGIN                                 AS login,
            CONCAT(u.NAME, ' ', u.LAST_NAME)        AS full_name,
            u.EMAIL                                 AS email,
            u.ACTIVE                                AS active,
            uu.UF_DEPARTMENT                        AS raw_dept,
            u.WORK_PHONE                            AS work_phone,
            u.PERSONAL_MOBILE                       AS mobile_phone,
            u.PERSONAL_PHONE                        AS personal_phone
        FROM b_user u
        JOIN b_uts_user uu ON uu.VALUE_ID = u.ID
        WHERE u.ACTIVE = 'Y'
          AND uu.UF_DEPARTMENT IS NOT NULL
          AND uu.UF_DEPARTMENT != ''
        ORDER BY u.ID
    """

    # ── Employee Avatars ───────────────────────────────────────────────
    # Filtered to the same population as SQL_EMPLOYEES (active + has department).
    SQL_EMPLOYEE_AVATARS = """
        SELECT
            u.ID AS user_id,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS photo_path,
            bf.CONTENT_TYPE AS content_type
        FROM b_user u
        JOIN b_uts_user uu ON uu.VALUE_ID = u.ID
        JOIN b_file bf ON bf.ID = u.PERSONAL_PHOTO
        WHERE u.ACTIVE = 'Y'
          AND uu.UF_DEPARTMENT IS NOT NULL
          AND uu.UF_DEPARTMENT != ''
          AND u.PERSONAL_PHOTO IS NOT NULL
          AND u.PERSONAL_PHOTO > 0
        ORDER BY u.ID
    """

    SQL_EMPLOYEE_AVATARS_AFTER = """
        SELECT
            u.ID AS user_id,
            CONCAT('/upload/', bf.SUBDIR, '/', bf.FILE_NAME) AS photo_path,
            bf.CONTENT_TYPE AS content_type
        FROM b_user u
        JOIN b_uts_user uu ON uu.VALUE_ID = u.ID
        JOIN b_file bf ON bf.ID = u.PERSONAL_PHOTO
        WHERE u.ACTIVE = 'Y'
          AND uu.UF_DEPARTMENT IS NOT NULL
          AND uu.UF_DEPARTMENT != ''
          AND u.PERSONAL_PHOTO IS NOT NULL
          AND u.PERSONAL_PHOTO > 0
          AND u.ID > %s
        ORDER BY u.ID
        LIMIT %s
    """

    SQL_COUNT_EMPLOYEE_AVATARS = """
        SELECT COUNT(*) AS cnt
        FROM b_user u
        JOIN b_uts_user uu ON uu.VALUE_ID = u.ID
        JOIN b_file bf ON bf.ID = u.PERSONAL_PHOTO
        WHERE u.ACTIVE = 'Y'
          AND uu.UF_DEPARTMENT IS NOT NULL
          AND uu.UF_DEPARTMENT != ''
          AND u.PERSONAL_PHOTO IS NOT NULL
          AND u.PERSONAL_PHOTO > 0
    """

    # Telegram хранится в отдельной таблице мессенджеров Битрикс24
    SQL_EMPLOYEE_TELEGRAMS = """
        SELECT USER_ID AS user_id, VALUE AS telegram
        FROM b_user_field_imtype
        WHERE TYPE = 'TELEGRAM'
          AND VALUE IS NOT NULL
          AND VALUE != ''
    """

    SQL_USER_TELEGRAM_FIELDS = """
        SELECT DISTINCT uf.FIELD_NAME AS field_name
        FROM b_user_field uf
        LEFT JOIN b_user_field_lang ul ON ul.USER_FIELD_ID = uf.ID
        WHERE uf.ENTITY_ID = 'USER'
          AND LOWER(COALESCE(ul.EDIT_FORM_LABEL, '')) = 'telegram'
    """

    SQL_COUNT_DEPARTMENTS = "SELECT COUNT(*) AS cnt FROM b_iblock_section WHERE IBLOCK_ID = 1"
    SQL_COUNT_EMPLOYEES = """
        SELECT COUNT(*) AS cnt FROM b_user u
        JOIN b_uts_user uu ON uu.VALUE_ID = u.ID
        WHERE u.ACTIVE = 'Y' AND uu.UF_DEPARTMENT IS NOT NULL AND uu.UF_DEPARTMENT != ''
    """

    # ── Count queries ─────────────────────────────────────────────────
    SQL_COUNT_COMMENTS_TEMPLATE = """
        SELECT COUNT(*) AS cnt FROM b_forum_message fm
        STRAIGHT_JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE fm.FORUM_ID = 11 AND fm.SERVICE_TYPE IS NULL AND fm.NEW_TOPIC = 'N'
          AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
          AND {task_where_clause}
    """
    SQL_COUNT_TAGS = """
        SELECT COUNT(*) AS cnt
        FROM (
            SELECT DISTINCT tl.NAME AS name
            FROM b_tasks_label tl
            WHERE tl.NAME IS NOT NULL
              AND tl.NAME != ''

            UNION

            SELECT DISTINCT gt.NAME AS name
            FROM b_sonet_group_tag gt
            JOIN b_sonet_group g ON g.ID = gt.GROUP_ID
            WHERE g.PROJECT = 'Y'
              AND gt.NAME IS NOT NULL
              AND gt.NAME != ''
        ) tags
    """
    SQL_COUNT_STAGES = "SELECT COUNT(*) AS cnt FROM b_tasks_stages WHERE ENTITY_TYPE = 'G' AND TITLE IS NOT NULL AND TITLE != ''"
    SQL_COUNT_MEETINGS = "SELECT COUNT(*) AS cnt FROM b_calendar_event WHERE IS_MEETING = '1' AND DELETED = 'N' AND ID = PARENT_ID"
    SQL_COUNT_MEETING_COMMENTS = """
        SELECT COUNT(*) AS cnt FROM b_forum_message fm
        STRAIGHT_JOIN b_calendar_event ce ON ce.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE fm.SERVICE_TYPE IS NULL AND fm.NEW_TOPIC = 'N'
          AND ce.IS_MEETING = '1' AND ce.DELETED = 'N' AND ce.ID = ce.PARENT_ID
    """
    SQL_COUNT_MEETING_SONET_COMMENTS = """
        SELECT COUNT(*) AS cnt
        FROM b_sonet_log sl
        JOIN b_sonet_log_comment slc ON slc.LOG_ID = sl.ID
        JOIN b_calendar_event ce ON ce.ID = sl.SOURCE_ID
        WHERE sl.MODULE_ID = 'calendar'
          AND ce.IS_MEETING = '1'
          AND ce.DELETED = 'N'
          AND ce.ID = ce.PARENT_ID
    """

    def __init__(self, host, port, user, password, database, date_from=None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.date_from = self._normalize_date_from(date_from)
        self._conn = None
        self._task_created_expr = None
        self._task_filter_column = None
        self._project_filter_column = None
        self._calendar_forum_topic_column = None

    def _get_connection(self):
        if self._conn is None or not self._conn.open:
            self._conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                cursorclass=DictCursor,
                charset='utf8mb4',
                connect_timeout=30,
                read_timeout=600,
            )
        return self._conn

    def _execute(self, sql, params=None):
        conn = self._get_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def _count(self, sql):
        result = self._execute(sql)
        return result[0]['cnt'] if result else 0

    @staticmethod
    def _normalize_date_from(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d 00:00:00')
        if isinstance(value, date):
            return value.strftime('%Y-%m-%d 00:00:00')
        value = str(value).strip()
        if not value:
            return None
        return f'{value} 00:00:00' if len(value) == 10 else value

    def close(self):
        if self._conn and self._conn.open:
            self._conn.close()
            self._conn = None

    def _get_existing_mysql_column(self, table_name, candidates):
        placeholders = ', '.join(['%s'] * len(candidates))
        sql = f"""
            SELECT COLUMN_NAME AS column_name
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name IN ({placeholders})
        """
        rows = self._execute(sql, (self.database, table_name, *candidates))
        found = {row['column_name'] for row in rows}
        for candidate in candidates:
            if candidate in found:
                return candidate
        return None

    def _get_task_created_expr(self):
        if self._task_created_expr is None:
            column_name = self._get_existing_mysql_column(
                'b_tasks',
                ['CREATED_DATE', 'DATE_START', 'CREATED_AT', 'DATE_CREATE'],
            )
            self._task_created_expr = (
                f't.{column_name} AS date_created' if column_name else 'NULL AS date_created'
            )
        return self._task_created_expr

    def _get_task_filter_column(self):
        if self._task_filter_column is None:
            self._task_filter_column = self._get_existing_mysql_column(
                'b_tasks',
                ['CREATED_DATE', 'DATE_START', 'CREATED_AT', 'DATE_CREATE'],
            )
        return self._task_filter_column

    def _get_project_filter_column(self):
        if self._project_filter_column is None:
            self._project_filter_column = self._get_existing_mysql_column(
                'b_sonet_group',
                ['DATE_CREATE', 'PROJECT_DATE_START', 'DATE_ACTIVITY'],
            )
        return self._project_filter_column

    def _get_calendar_forum_topic_column(self):
        if self._calendar_forum_topic_column is None:
            self._calendar_forum_topic_column = self._get_existing_mysql_column(
                'b_calendar_event',
                ['FORUM_TOPIC_ID'],
            )
        return self._calendar_forum_topic_column

    def _uses_calendar_forum_comments(self):
        return bool(self._get_calendar_forum_topic_column())

    def _get_task_where_clause(self):
        column_name = self._get_task_filter_column()
        if self.date_from and column_name:
            return f't.{column_name} >= %s'
        return '1=1'

    def _get_task_params(self):
        return (self.date_from,) if self.date_from and self._get_task_filter_column() else None

    def _get_project_where_clause(self):
        column_name = self._get_project_filter_column()
        if self.date_from and column_name:
            return f'g.{column_name} >= %s'
        return '1=1'

    def _get_project_params(self):
        return (self.date_from,) if self.date_from and self._get_project_filter_column() else None

    # ── Extract methods ───────────────────────────────────────────────

    def _get_project_combined_params(self):
        proj_params = self._get_project_params()
        task_params = self._get_task_params()
        parts = []
        if proj_params:
            parts.extend(proj_params)
        if task_params:
            parts.extend(task_params)
        return tuple(parts) if parts else None

    def get_projects(self):
        sql = self.SQL_PROJECTS_TEMPLATE.format(
            project_where_clause=self._get_project_where_clause(),
            task_where_clause=self._get_task_where_clause(),
        )
        return self._execute(sql, self._get_project_combined_params())

    def get_project_by_id(self, project_id):
        return self._execute(self.SQL_PROJECT_BY_ID, (project_id,))

    def get_tasks(self):
        sql = self.SQL_TASKS_TEMPLATE.format(
            task_created_expr=self._get_task_created_expr(),
            task_where_clause=self._get_task_where_clause(),
        )
        return self._execute(sql, self._get_task_params())

    def get_single_task(self, task_id):
        sql = self.SQL_SINGLE_TASK_TEMPLATE.format(
            task_created_expr=self._get_task_created_expr(),
        )
        return self._execute(sql, (task_id,))

    def get_tags(self):
        return self._execute(self.SQL_TAGS)

    def get_stages(self):
        return self._execute(self.SQL_STAGES)

    def get_stages_with_projects(self):
        return self._execute(self.SQL_STAGES_WITH_PROJECTS)

    def get_comments(self):
        sql = self.SQL_COMMENTS_TEMPLATE.format(
            task_where_clause=self._get_task_where_clause(),
        )
        return self._execute(sql, self._get_task_params())

    def get_comments_for_task(self, task_id):
        return self._execute(self.SQL_COMMENTS_FOR_TASK, (task_id,))

    def get_users(self):
        return self._execute(self.SQL_USERS)

    def get_task_members(self):
        return self._execute(self.SQL_TASK_MEMBERS)

    def get_task_status_map(self, task_ids, chunk_size=1000):
        if not task_ids:
            return {}

        unique_ids = sorted({int(task_id) for task_id in task_ids if task_id})
        result = {}
        for start in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[start:start + chunk_size]
            placeholders = ', '.join(['%s'] * len(chunk))
            sql = self.SQL_TASK_STATUS_BY_IDS_TEMPLATE.format(placeholders=placeholders)
            rows = self._execute(sql, tuple(chunk))
            for row in rows:
                task_external_id = row.get('task_external_id')
                if task_external_id is None:
                    continue
                try:
                    status_code = int(row.get('status_code')) if row.get('status_code') is not None else None
                except (TypeError, ValueError):
                    status_code = None
                result[str(task_external_id)] = status_code
        return result

    def get_task_attachments(self):
        sql = self.SQL_TASK_ATTACHMENTS_TEMPLATE.format(
            task_where_clause=self._get_task_where_clause(),
        )
        return self._execute(sql, self._get_task_params())

    def get_task_attachments_for_task(self, task_id):
        return self._execute(self.SQL_TASK_ATTACHMENTS_FOR_TASK, (task_id,))

    def get_comment_attachments(self):
        sql = self.SQL_COMMENT_ATTACHMENTS_TEMPLATE.format(
            task_where_clause=self._get_task_where_clause(),
        )
        return self._execute(sql, self._get_task_params())

    def get_comment_attachments_for_task(self, task_id):
        return self._execute(self.SQL_COMMENT_ATTACHMENTS_FOR_TASK, (task_id,))

    def get_meetings(self):
        forum_topic_expr = (
            'ce.FORUM_TOPIC_ID AS forum_topic_id'
            if self._get_calendar_forum_topic_column()
            else 'NULL AS forum_topic_id'
        )
        sql = self.SQL_MEETINGS_TEMPLATE.format(forum_topic_expr=forum_topic_expr)
        return self._execute(sql)

    def get_meeting_comments(self):
        if self._uses_calendar_forum_comments():
            return self._execute(self.SQL_MEETING_FORUM_COMMENTS)
        return self._execute(self.SQL_MEETING_SONET_COMMENTS)

    def get_meeting_attachments(self):
        if MEETING_ATTACHMENT_ENTITY_TYPE.startswith('CHANGEME'):
            raise RuntimeError(
                'MEETING_ATTACHMENT_ENTITY_TYPE not configured. '
                'Run discovery query and patch the constant.'
            )
        return self._execute(
            self.SQL_MEETING_ATTACHMENTS,
            (MEETING_ATTACHMENT_ENTITY_TYPE,),
        )

    def get_meeting_comment_attachments(self):
        if self._uses_calendar_forum_comments():
            return self._execute(self.SQL_MEETING_FORUM_COMMENT_ATTACHMENTS)
        return self._execute(self.SQL_MEETING_SONET_COMMENT_ATTACHMENTS)

    def get_departments(self):
        return self._execute(self.SQL_DEPARTMENTS)

    def get_employees(self):
        return self._execute(self.SQL_EMPLOYEES)

    def get_employee_avatars(self):
        """Returns list of {user_id, photo_path, content_type}."""
        try:
            return self._execute(self.SQL_EMPLOYEE_AVATARS)
        except Exception as e:
            _logger.warning('Could not fetch employee avatars: %s', e)
            return []

    def get_employee_avatars_after(self, last_user_id, limit):
        """Return up to *limit* avatar rows for users with ID > last_user_id."""
        try:
            return self._execute(self.SQL_EMPLOYEE_AVATARS_AFTER, (last_user_id, limit))
        except Exception as e:
            _logger.warning('Could not fetch employee avatars (paginated): %s', e)
            return []

    def count_employee_avatars(self):
        """Total avatar rows matching the HR-import filter."""
        try:
            result = self._execute(self.SQL_COUNT_EMPLOYEE_AVATARS)
            return result[0]['cnt'] if result else 0
        except Exception as e:
            _logger.warning('Could not count employee avatars: %s', e)
            return 0

    def get_employee_telegrams(self):
        """Returns {user_id: telegram} dict.
        Supports both legacy messenger table and custom USER fields named Telegram.
        """
        try:
            rows = self._execute(self.SQL_EMPLOYEE_TELEGRAMS)
            telegrams = {
                str(row['user_id']): row['telegram']
                for row in rows if row.get('telegram')
            }
            if telegrams:
                return telegrams
        except Exception as e:
            _logger.warning('Could not fetch Telegram accounts (table may not exist): %s', e)

        return self._get_employee_telegrams_from_user_fields()

    def _get_employee_telegrams_from_user_fields(self):
        field_names = self._get_user_telegram_field_names()
        if not field_names:
            return {}

        select_expr = ', '.join(
            f'uu.{field_name} AS {field_name.lower()}'
            for field_name in field_names
        )
        sql = f"""
            SELECT
                u.ID AS user_id,
                {select_expr}
            FROM b_user u
            JOIN b_uts_user uu ON uu.VALUE_ID = u.ID
            WHERE u.ACTIVE = 'Y'
        """
        rows = self._execute(sql)
        result = {}
        for row in rows:
            telegram = self._pick_telegram_value(
                row.get(field_name.lower()) for field_name in field_names
            )
            if telegram:
                result[str(row['user_id'])] = telegram
        return result

    def _get_user_telegram_field_names(self):
        try:
            rows = self._execute(self.SQL_USER_TELEGRAM_FIELDS)
        except Exception as e:
            _logger.warning('Could not discover Telegram user fields: %s', e)
            return []

        field_names = []
        for row in rows:
            field_name = row.get('field_name')
            if field_name and re.match(r'^UF_[A-Z0-9_]+$', field_name):
                field_names.append(field_name)
        return field_names

    def _pick_telegram_value(self, raw_values):
        candidates = []
        for raw_value in raw_values:
            for value in self._expand_telegram_values(raw_value):
                if value:
                    candidates.append(value)

        if not candidates:
            return None

        def score(value):
            lower = value.lower()
            if lower.startswith('@') or 't.me/' in lower or 'telegram.me/' in lower:
                return 0
            if any(char.isalpha() for char in value):
                return 1
            return 2

        return sorted(candidates, key=score)[0]

    @staticmethod
    def _expand_telegram_values(raw_value):
        if raw_value is None:
            return []

        if not isinstance(raw_value, str):
            raw_value = str(raw_value)

        raw_value = raw_value.strip()
        if not raw_value or raw_value == 'a:0:{}':
            return []

        if raw_value.startswith('a:'):
            values = [item.strip() for item in re.findall(r's:\d+:"([^"]*)";', raw_value)]
            return [item for item in values if item]

        return [raw_value]

    # ── Count methods ─────────────────────────────────────────────────

    def count_projects(self):
        sql = self.SQL_COUNT_PROJECTS_TEMPLATE.format(
            project_where_clause=self._get_project_where_clause(),
            task_where_clause=self._get_task_where_clause(),
        )
        result = self._execute(sql, self._get_project_combined_params())
        return result[0]['cnt'] if result else 0

    def count_tasks(self):
        sql = self.SQL_COUNT_TASKS_TEMPLATE.format(
            task_where_clause=self._get_task_where_clause(),
        )
        result = self._execute(sql, self._get_task_params())
        return result[0]['cnt'] if result else 0

    def count_comments(self):
        sql = self.SQL_COUNT_COMMENTS_TEMPLATE.format(
            task_where_clause=self._get_task_where_clause(),
        )
        result = self._execute(sql, self._get_task_params())
        return result[0]['cnt'] if result else 0

    def count_tags(self):
        return self._count(self.SQL_COUNT_TAGS)

    def count_stages(self):
        return self._count(self.SQL_COUNT_STAGES)

    def count_meetings(self):
        return self._count(self.SQL_COUNT_MEETINGS)

    def count_meeting_comments(self):
        if self._uses_calendar_forum_comments():
            return self._count(self.SQL_COUNT_MEETING_COMMENTS)
        return self._count(self.SQL_COUNT_MEETING_SONET_COMMENTS)

    def count_departments(self):
        return self._count(self.SQL_COUNT_DEPARTMENTS)

    def count_employees(self):
        return self._count(self.SQL_COUNT_EMPLOYEES)
