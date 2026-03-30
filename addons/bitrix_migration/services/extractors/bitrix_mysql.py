import logging

import pymysql
from pymysql.cursors import DictCursor

_logger = logging.getLogger(__name__)


class BitrixMySQLExtractor:
    """MySQL connector for extracting data from Bitrix24 database."""

    # ── Projects ──────────────────────────────────────────────────────
    SQL_PROJECTS = """
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
        ORDER BY g.ID
    """

    # ── Tasks ─────────────────────────────────────────────────────────
    SQL_TASKS = """
        SELECT
            t.ID AS external_id,
            t.TITLE AS name,
            CASE WHEN t.GROUP_ID > 0 THEN t.GROUP_ID ELSE NULL END AS project_external_id,
            GROUP_CONCAT(DISTINCT m.USER_ID SEPARATOR ', ') AS responsible_user_ids,
            (SELECT GROUP_CONCAT(DISTINCT tl.NAME SEPARATOR ', ')
             FROM b_tasks_task_tag tt
             JOIN b_tasks_label tl ON tl.ID = tt.TAG_ID
             WHERE tt.TASK_ID = t.ID) AS tags,
            t.DEADLINE AS date_deadline,
            t.DESCRIPTION AS description,
            t.STAGE_ID AS stage_id,
            CASE WHEN t.PARENT_ID > 0 THEN t.PARENT_ID ELSE NULL END AS parent_id
        FROM b_tasks t
        LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE IN ('R','A')
        WHERE (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
        GROUP BY t.ID
        ORDER BY t.ID
    """

    SQL_SINGLE_TASK = """
        SELECT
            t.ID AS external_id,
            t.TITLE AS name,
            CASE WHEN t.GROUP_ID > 0 THEN t.GROUP_ID ELSE NULL END AS project_external_id,
            GROUP_CONCAT(DISTINCT m.USER_ID SEPARATOR ', ') AS responsible_user_ids,
            (SELECT GROUP_CONCAT(DISTINCT tl.NAME SEPARATOR ', ')
             FROM b_tasks_task_tag tt
             JOIN b_tasks_label tl ON tl.ID = tt.TAG_ID
             WHERE tt.TASK_ID = t.ID) AS tags,
            t.DEADLINE AS date_deadline,
            t.DESCRIPTION AS description,
            t.STAGE_ID AS stage_id,
            CASE WHEN t.PARENT_ID > 0 THEN t.PARENT_ID ELSE NULL END AS parent_id
        FROM b_tasks t
        LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE IN ('R','A')
        WHERE t.ID = %s
        GROUP BY t.ID
    """

    # ── Tags ──────────────────────────────────────────────────────────
    SQL_TAGS = """
        SELECT ID AS id, NAME AS name, 'task' AS source
        FROM (
            SELECT MIN(ID) AS ID, NAME
            FROM b_tasks_label
            WHERE NAME IS NOT NULL AND NAME != ''
            GROUP BY NAME
        ) tl

        UNION ALL

        SELECT
            900000 + (@row := @row + 1) AS id,
            NAME AS name,
            'project' AS source
        FROM (
            SELECT DISTINCT gt.NAME
            FROM b_sonet_group_tag gt
            JOIN b_sonet_group g ON g.ID = gt.GROUP_ID
            WHERE g.PROJECT = 'Y'
              AND gt.NAME NOT IN (SELECT NAME FROM b_tasks_label)
              AND gt.NAME IS NOT NULL AND gt.NAME != ''
            ORDER BY gt.NAME
        ) pt, (SELECT @row := 0) r

        ORDER BY name
    """

    # ── Stages ────────────────────────────────────────────────────────
    SQL_STAGES = """
        SELECT
            s.ID,
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
    SQL_COMMENTS = """
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
        WHERE TYPE IN ('R', 'A')
        ORDER BY TASK_ID
    """

    # ── Task Attachments ──────────────────────────────────────────────
    SQL_TASK_ATTACHMENTS = """
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
    SQL_COMMENT_ATTACHMENTS = """
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
    SQL_MEETINGS = """
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
            ce.DESCRIPTION AS description
        FROM b_calendar_event ce
        WHERE ce.IS_MEETING = '1'
          AND ce.DELETED = 'N'
          AND ce.ID = ce.PARENT_ID
        ORDER BY ce.DATE_FROM
    """

    # ── Count queries ─────────────────────────────────────────────────
    SQL_COUNT_PROJECTS = "SELECT COUNT(*) AS cnt FROM b_sonet_group"
    SQL_COUNT_TASKS = "SELECT COUNT(*) AS cnt FROM b_tasks WHERE (ZOMBIE = 'N' OR ZOMBIE IS NULL)"
    SQL_COUNT_COMMENTS = """
        SELECT COUNT(*) AS cnt FROM b_forum_message fm
        STRAIGHT_JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
        WHERE fm.FORUM_ID = 11 AND fm.SERVICE_TYPE IS NULL AND fm.NEW_TOPIC = 'N'
          AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
    """
    SQL_COUNT_TAGS = "SELECT COUNT(DISTINCT NAME) AS cnt FROM b_tasks_label WHERE NAME IS NOT NULL AND NAME != ''"
    SQL_COUNT_STAGES = "SELECT COUNT(*) AS cnt FROM b_tasks_stages WHERE ENTITY_TYPE = 'G' AND TITLE IS NOT NULL AND TITLE != ''"
    SQL_COUNT_MEETINGS = "SELECT COUNT(*) AS cnt FROM b_calendar_event WHERE IS_MEETING = '1' AND DELETED = 'N' AND ID = PARENT_ID"

    def __init__(self, host, port, user, password, database):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._conn = None

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

    def close(self):
        if self._conn and self._conn.open:
            self._conn.close()
            self._conn = None

    # ── Extract methods ───────────────────────────────────────────────

    def get_projects(self):
        return self._execute(self.SQL_PROJECTS)

    def get_tasks(self):
        return self._execute(self.SQL_TASKS)

    def get_single_task(self, task_id):
        return self._execute(self.SQL_SINGLE_TASK, (task_id,))

    def get_tags(self):
        return self._execute(self.SQL_TAGS)

    def get_stages(self):
        return self._execute(self.SQL_STAGES)

    def get_stages_with_projects(self):
        return self._execute(self.SQL_STAGES_WITH_PROJECTS)

    def get_comments(self):
        return self._execute(self.SQL_COMMENTS)

    def get_comments_for_task(self, task_id):
        return self._execute(self.SQL_COMMENTS_FOR_TASK, (task_id,))

    def get_users(self):
        return self._execute(self.SQL_USERS)

    def get_task_members(self):
        return self._execute(self.SQL_TASK_MEMBERS)

    def get_task_attachments(self):
        return self._execute(self.SQL_TASK_ATTACHMENTS)

    def get_task_attachments_for_task(self, task_id):
        return self._execute(self.SQL_TASK_ATTACHMENTS_FOR_TASK, (task_id,))

    def get_comment_attachments(self):
        return self._execute(self.SQL_COMMENT_ATTACHMENTS)

    def get_comment_attachments_for_task(self, task_id):
        return self._execute(self.SQL_COMMENT_ATTACHMENTS_FOR_TASK, (task_id,))

    def get_meetings(self):
        return self._execute(self.SQL_MEETINGS)

    # ── Count methods ─────────────────────────────────────────────────

    def count_projects(self):
        return self._count(self.SQL_COUNT_PROJECTS)

    def count_tasks(self):
        return self._count(self.SQL_COUNT_TASKS)

    def count_comments(self):
        return self._count(self.SQL_COUNT_COMMENTS)

    def count_tags(self):
        return self._count(self.SQL_COUNT_TAGS)

    def count_stages(self):
        return self._count(self.SQL_COUNT_STAGES)

    def count_meetings(self):
        return self._count(self.SQL_COUNT_MEETINGS)
