from fastapi import APIRouter, HTTPException, Query

from ..database import query, query_one

router = APIRouter(prefix="/api/employees", tags=["employees"])

SQL_EMPLOYEES = """
    SELECT
        u.ID AS id,
        u.LOGIN AS login,
        u.EMAIL AS email,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS full_name,
        u.ACTIVE AS active
    FROM b_user u
    WHERE u.ACTIVE = 'Y'
      {where_extra}
    ORDER BY u.NAME, u.LAST_NAME
"""

SQL_EMPLOYEE_BY_ID = """
    SELECT
        u.ID AS id,
        u.LOGIN AS login,
        u.EMAIL AS email,
        CONCAT(u.NAME, ' ', IFNULL(u.LAST_NAME, '')) AS full_name,
        u.ACTIVE AS active
    FROM b_user u
    WHERE u.ID = %s
"""

SQL_EMPLOYEE_TASKS = """
    SELECT
        t.ID AS id,
        t.TITLE AS name,
        t.GROUP_ID AS project_id,
        g.NAME AS project_name,
        t.DEADLINE AS date_deadline,
        s.TITLE AS stage_name,
        m.TYPE AS role
    FROM b_tasks_member m
    JOIN b_tasks t ON t.ID = m.TASK_ID
    LEFT JOIN b_sonet_group g ON g.ID = t.GROUP_ID
    LEFT JOIN b_tasks_stages s ON s.ID = t.STAGE_ID
    WHERE m.USER_ID = %s
      AND m.TYPE IN ('R', 'A')
      AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
    ORDER BY t.ID DESC
    LIMIT 100
"""

SQL_EMPLOYEE_STATS = """
    SELECT
        COUNT(DISTINCT m.TASK_ID) AS task_count,
        SUM(CASE WHEN m.TYPE = 'R' THEN 1 ELSE 0 END) AS responsible_count
    FROM b_tasks_member m
    JOIN b_tasks t ON t.ID = m.TASK_ID
    WHERE m.USER_ID = %s
      AND m.TYPE IN ('R', 'A')
      AND (t.ZOMBIE = 'N' OR t.ZOMBIE IS NULL)
"""


@router.get("")
def list_employees(search: str | None = Query(None)):
    conditions = []
    params = []
    if search:
        conditions.append(
            "(u.NAME LIKE %s OR u.LAST_NAME LIKE %s OR u.LOGIN LIKE %s OR u.EMAIL LIKE %s)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])

    where_extra = ("AND " + " AND ".join(conditions)) if conditions else ""
    return query(SQL_EMPLOYEES.format(where_extra=where_extra), params or None)


@router.get("/{employee_id}")
def get_employee(employee_id: int):
    employee = query_one(SQL_EMPLOYEE_BY_ID, (employee_id,))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    tasks = query(SQL_EMPLOYEE_TASKS, (employee_id,))
    stats = query_one(SQL_EMPLOYEE_STATS, (employee_id,))
    return {**employee, "tasks": tasks, "stats": stats}
