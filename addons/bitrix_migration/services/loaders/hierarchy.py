"""Pure helpers for computing employee→manager hierarchy from Bitrix department tree."""


def build_department_tree(depts):
    """Return {dept_id: {'parent_id': int|None, 'uf_head': int|None}}.

    ``depts`` is an iterable of ``BitrixDepartment`` instances (or any object
    exposing ``dept_id``, ``parent_dept_id``, ``head_user_id``).
    """
    tree = {}
    for dept in depts:
        try:
            dept_id = int(dept.dept_id)
        except (TypeError, ValueError):
            continue
        parent = dept.parent_dept_id
        head = dept.head_user_id
        tree[dept_id] = {
            'parent_id': int(parent) if parent else None,
            'uf_head': int(head) if head else None,
        }
    return tree


def compute_employee_parent_id(employee_bitrix_id, employee_dept_ids, dept_tree):
    """Return Bitrix user id of the employee's manager, or None.

    Walks up the department tree starting from the employee's first
    ``UF_DEPARTMENT`` value. Returns the first ``UF_HEAD`` encountered that
    is non-empty and differs from the employee themselves. Returns None if
    the chain reaches the root without a matching head.

    Cycle-safe via a ``visited`` set on department ids.
    """
    if not employee_dept_ids:
        return None
    try:
        emp_bid = int(employee_bitrix_id)
    except (TypeError, ValueError):
        return None

    current = employee_dept_ids[0]
    try:
        current = int(current)
    except (TypeError, ValueError):
        return None

    visited = set()
    while current is not None and current not in visited:
        visited.add(current)
        node = dept_tree.get(current)
        if node is None:
            return None
        head = node.get('uf_head')
        if head and int(head) != emp_bid:
            return int(head)
        current = node.get('parent_id')
    return None
