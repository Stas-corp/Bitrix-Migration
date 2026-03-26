from fastapi import APIRouter

from ..database import query

router = APIRouter(prefix="/api/tasks", tags=["comments"])

SQL_COMMENTS_FOR_TASK = """
    SELECT
        fm.ID AS id,
        fm.POST_MESSAGE AS body,
        fm.POST_DATE AS date,
        fm.AUTHOR_ID AS author_id,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS author_name,
        u.LOGIN AS author_login
    FROM b_forum_message fm
    STRAIGHT_JOIN b_tasks t ON t.FORUM_TOPIC_ID = fm.TOPIC_ID
    LEFT JOIN b_user u ON u.ID = fm.AUTHOR_ID
    WHERE fm.FORUM_ID = 11
      AND fm.SERVICE_TYPE IS NULL
      AND fm.NEW_TOPIC = 'N'
      AND t.ID = %s
      AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
    ORDER BY fm.POST_DATE
"""


@router.get("/{task_id}/comments")
def get_task_comments(task_id: int):
    return query(SQL_COMMENTS_FOR_TASK, (task_id,))
