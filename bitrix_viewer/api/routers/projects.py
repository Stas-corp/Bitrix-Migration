from fastapi import APIRouter, HTTPException

from ..database import query, query_one

router = APIRouter(prefix="/api/projects", tags=["projects"])

SQL_PROJECTS = """
    SELECT
        g.ID AS id,
        g.NAME AS name,
        CASE WHEN g.PROJECT = 'Y' THEN 'project' ELSE 'workgroup' END AS type,
        CASE WHEN g.CLOSED = 'Y' THEN 1 ELSE 0 END AS closed,
        g.OWNER_ID AS owner_id,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS owner_name,
        g.PROJECT_DATE_START AS date_start,
        g.PROJECT_DATE_FINISH AS date_end,
        g.DESCRIPTION AS description,
        (SELECT GROUP_CONCAT(gt.NAME SEPARATOR ', ')
         FROM b_sonet_group_tag gt WHERE gt.GROUP_ID = g.ID) AS tags
    FROM b_sonet_group g
    LEFT JOIN b_user u ON u.ID = g.OWNER_ID
    ORDER BY g.ID
"""

SQL_PROJECT_BY_ID = """
    SELECT
        g.ID AS id,
        g.NAME AS name,
        CASE WHEN g.PROJECT = 'Y' THEN 'project' ELSE 'workgroup' END AS type,
        CASE WHEN g.CLOSED = 'Y' THEN 1 ELSE 0 END AS closed,
        g.OWNER_ID AS owner_id,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS owner_name,
        g.PROJECT_DATE_START AS date_start,
        g.PROJECT_DATE_FINISH AS date_end,
        g.DESCRIPTION AS description,
        (SELECT GROUP_CONCAT(gt.NAME SEPARATOR ', ')
         FROM b_sonet_group_tag gt WHERE gt.GROUP_ID = g.ID) AS tags
    FROM b_sonet_group g
    LEFT JOIN b_user u ON u.ID = g.OWNER_ID
    WHERE g.ID = %s
"""

SQL_PROJECT_TASKS = """
    SELECT
        t.ID AS id,
        t.TITLE AS name,
        t.DEADLINE AS date_deadline,
        t.STAGE_ID AS stage_id,
        s.TITLE AS stage_name,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS responsible_name,
        CASE WHEN t.PARENT_ID > 0 THEN t.PARENT_ID ELSE NULL END AS parent_id
    FROM b_tasks t
    LEFT JOIN b_tasks_stages s ON s.ID = t.STAGE_ID
    LEFT JOIN b_tasks_member m ON m.TASK_ID = t.ID AND m.TYPE = 'R'
    LEFT JOIN b_user u ON u.ID = m.USER_ID
    WHERE t.GROUP_ID = %s
      AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
      AND (t.PARENT_ID = 0 OR t.PARENT_ID IS NULL)
    GROUP BY t.ID
    ORDER BY t.ID
    LIMIT 200
"""

SQL_PROJECT_STATS = """
    SELECT
        COUNT(*) AS task_count,
        SUM(CASE WHEN t.PARENT_ID > 0 THEN 1 ELSE 0 END) AS subtask_count
    FROM b_tasks t
    WHERE t.GROUP_ID = %s
      AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
"""


@router.get("")
def list_projects():
    return query(SQL_PROJECTS)


@router.get("/{project_id}")
def get_project(project_id: int):
    project = query_one(SQL_PROJECT_BY_ID, (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    tasks = query(SQL_PROJECT_TASKS, (project_id,))
    stats = query_one(SQL_PROJECT_STATS, (project_id,))
    return {**project, "tasks": tasks, "stats": stats}
