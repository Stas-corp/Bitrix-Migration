from fastapi import APIRouter, HTTPException, Query

from ..database import query, query_one

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

SQL_TASKS = """
    SELECT
        t.ID AS id,
        t.TITLE AS name,
        t.GROUP_ID AS project_id,
        g.NAME AS project_name,
        t.DEADLINE AS date_deadline,
        t.STAGE_ID AS stage_id,
        s.TITLE AS stage_name,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS responsible_name,
        CASE WHEN t.PARENT_ID > 0 THEN t.PARENT_ID ELSE NULL END AS parent_id
    FROM b_tasks t
    LEFT JOIN b_sonet_group g ON g.ID = t.GROUP_ID
    LEFT JOIN b_tasks_stages s ON s.ID = t.STAGE_ID
    LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE = 'R'
    LEFT JOIN b_user u ON u.ID = m.USER_ID
    WHERE (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
      {where_extra}
    GROUP BY t.ID
    ORDER BY t.ID
    LIMIT %s OFFSET %s
"""

SQL_TASKS_COUNT = """
    SELECT COUNT(DISTINCT t.ID) AS cnt
    FROM b_tasks t
    WHERE (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
      {where_extra}
"""

SQL_TASK_BY_ID = """
    SELECT
        t.ID AS id,
        t.TITLE AS name,
        t.GROUP_ID AS project_id,
        g.NAME AS project_name,
        t.DEADLINE AS date_deadline,
        t.STAGE_ID AS stage_id,
        s.TITLE AS stage_name,
        t.DESCRIPTION AS description,
        CASE WHEN t.PARENT_ID > 0 THEN t.PARENT_ID ELSE NULL END AS parent_id,
        (SELECT GROUP_CONCAT(DISTINCT tl.NAME SEPARATOR ', ')
         FROM b_tasks_task_tag tt
         JOIN b_tasks_label tl ON tl.ID = tt.TAG_ID
         WHERE tt.TASK_ID = t.ID) AS tags
    FROM b_tasks t
    LEFT JOIN b_sonet_group g ON g.ID = t.GROUP_ID
    LEFT JOIN b_tasks_stages s ON s.ID = t.STAGE_ID
    WHERE t.ID = %s
"""

SQL_TASK_MEMBERS = """
    SELECT
        m.USER_ID AS user_id,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS full_name,
        u.EMAIL AS email,
        m.TYPE AS role
    FROM b_tasks_member m
    JOIN b_user u ON u.ID = m.USER_ID
    WHERE m.TASK_ID = %s AND m.TYPE IN ('R', 'A')
    ORDER BY m.TYPE, u.NAME
"""

SQL_SUBTASKS = """
    SELECT
        t.ID AS id,
        t.TITLE AS name,
        t.DEADLINE AS date_deadline,
        s.TITLE AS stage_name,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS responsible_name
    FROM b_tasks t
    LEFT JOIN b_tasks_stages s ON s.ID = t.STAGE_ID
    LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE = 'R'
    LEFT JOIN b_user u ON u.ID = m.USER_ID
    WHERE t.PARENT_ID = %s
      AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
    GROUP BY t.ID
    ORDER BY t.ID
"""


@router.get("")
def list_tasks(
    project_id: int | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    conditions = []
    params = []

    if project_id is not None:
        conditions.append("t.GROUP_ID = %s")
        params.append(project_id)
    if search:
        conditions.append("t.TITLE LIKE %s")
        params.append(f"%{search}%")

    where_extra = ("AND " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * size

    rows = query(
        SQL_TASKS.format(where_extra=where_extra),
        params + [size, offset],
    )
    total_row = query_one(
        SQL_TASKS_COUNT.format(where_extra=where_extra),
        params,
    )
    total = total_row["cnt"] if total_row else 0

    return {"total": total, "page": page, "size": size, "items": rows}


@router.get("/{task_id}")
def get_task(task_id: int):
    task = query_one(SQL_TASK_BY_ID, (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    members = query(SQL_TASK_MEMBERS, (task_id,))
    subtasks = query(SQL_SUBTASKS, (task_id,))
    return {**task, "members": members, "subtasks": subtasks}
