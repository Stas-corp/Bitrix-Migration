"""Bitrix Viewer — Streamlit frontend."""

import os

import httpx
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def api_get(path: str, params: dict | None = None):
    try:
        r = httpx.get(f"{API_BASE}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        st.error(f"API error: {e}")
        return None


# ── Page: Projects ────────────────────────────────────────────────────────────

def page_projects():
    st.title("Проекты")

    col1, col2 = st.columns([3, 1])
    with col2:
        show_closed = st.checkbox("Показать закрытые", value=False)
    with col1:
        filter_type = st.selectbox("Тип", ["Все", "project", "workgroup"])

    projects = api_get("/api/projects")
    if projects is None:
        return

    if not show_closed:
        projects = [p for p in projects if not p.get("closed")]
    if filter_type != "Все":
        projects = [p for p in projects if p.get("type") == filter_type]

    st.caption(f"Найдено: {len(projects)}")

    for p in projects:
        label = f"{'🔒 ' if p.get('closed') else ''}**{p['name']}** — {p.get('type', '')}"
        if p.get("owner_name", "").strip():
            label += f" · {p['owner_name'].strip()}"
        if st.button(label, key=f"proj_{p['id']}"):
            st.session_state.page = "project_detail"
            st.session_state.project_id = p["id"]
            st.rerun()


# ── Page: Project Detail ──────────────────────────────────────────────────────

def page_project_detail():
    project_id = st.session_state.get("project_id")
    if not project_id:
        st.session_state.page = "projects"
        st.rerun()
        return

    if st.button("← Назад к проектам"):
        st.session_state.page = "projects"
        st.rerun()

    data = api_get(f"/api/projects/{project_id}")
    if not data:
        return

    st.title(data["name"])
    cols = st.columns(3)
    cols[0].metric("Тип", data.get("type", "—"))
    cols[1].metric("Статус", "Закрыт" if data.get("closed") else "Открыт")
    cols[2].metric("Задач", data.get("stats", {}).get("task_count", 0))

    if data.get("description"):
        with st.expander("Описание"):
            st.write(data["description"])

    if data.get("tags"):
        st.write("**Теги:**", data["tags"])

    st.divider()
    st.subheader("Задачи верхнего уровня")

    tasks = data.get("tasks", [])
    if not tasks:
        st.info("Нет задач")
        return

    for t in tasks:
        deadline = t.get("date_deadline") or "—"
        stage = t.get("stage_name") or "—"
        responsible = (t.get("responsible_name") or "—").strip()
        label = f"**{t['name']}** · {stage} · {responsible} · {deadline}"
        if st.button(label, key=f"task_{t['id']}"):
            st.session_state.page = "task_detail"
            st.session_state.task_id = t["id"]
            st.session_state.back_page = "project_detail"
            st.rerun()


# ── Page: Tasks ───────────────────────────────────────────────────────────────

def page_tasks():
    st.title("Задачи")

    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        search = st.text_input("Поиск по названию", placeholder="Введите текст...")
    with col2:
        project_id_input = st.number_input("ID проекта", min_value=0, value=0, step=1)
    with col3:
        page_num = st.number_input("Страница", min_value=1, value=1, step=1)

    params = {"page": page_num, "size": 50}
    if search:
        params["search"] = search
    if project_id_input > 0:
        params["project_id"] = project_id_input

    data = api_get("/api/tasks", params=params)
    if data is None:
        return

    total = data.get("total", 0)
    items = data.get("items", [])
    st.caption(f"Найдено: {total} · Страница {page_num}")

    for t in items:
        project = (t.get("project_name") or "—").strip()
        deadline = t.get("date_deadline") or "—"
        stage = t.get("stage_name") or "—"
        responsible = (t.get("responsible_name") or "—").strip()
        label = f"**{t['name']}** · {project} · {stage} · {responsible} · {deadline}"
        if st.button(label, key=f"task_list_{t['id']}"):
            st.session_state.page = "task_detail"
            st.session_state.task_id = t["id"]
            st.session_state.back_page = "tasks"
            st.rerun()


# ── Page: Task Detail ─────────────────────────────────────────────────────────

def page_task_detail():
    task_id = st.session_state.get("task_id")
    back_page = st.session_state.get("back_page", "tasks")

    if not task_id:
        st.session_state.page = "tasks"
        st.rerun()
        return

    back_label = "← Назад к проекту" if back_page == "project_detail" else "← Назад к задачам"
    if st.button(back_label):
        st.session_state.page = back_page
        st.rerun()

    task = api_get(f"/api/tasks/{task_id}")
    if not task:
        return

    st.title(task["name"])

    cols = st.columns(3)
    cols[0].metric("Проект", (task.get("project_name") or "—").strip())
    cols[1].metric("Стадия", task.get("stage_name") or "—")
    cols[2].metric("Дедлайн", task.get("date_deadline") or "—")

    if task.get("tags"):
        st.write("**Теги:**", task["tags"])

    members = task.get("members", [])
    if members:
        st.subheader("Участники")
        for m in members:
            role_label = "Ответственный" if m["role"] == "R" else "Соисполнитель"
            st.write(f"- **{m['full_name'].strip()}** ({role_label}) — {m.get('email', '')}")

    if task.get("description"):
        with st.expander("Описание задачи"):
            st.write(task["description"])

    subtasks = task.get("subtasks", [])
    if subtasks:
        with st.expander(f"Подзадачи ({len(subtasks)})"):
            for s in subtasks:
                responsible = (s.get("responsible_name") or "—").strip()
                stage = s.get("stage_name") or "—"
                label = f"**{s['name']}** · {stage} · {responsible}"
                if st.button(label, key=f"subtask_{s['id']}"):
                    st.session_state.task_id = s["id"]
                    st.session_state.back_page = "task_detail"
                    st.rerun()

    st.divider()
    st.subheader("Комментарии")
    comments = api_get(f"/api/tasks/{task_id}/comments")
    if comments is None:
        return

    if not comments:
        st.info("Комментариев нет")
        return

    for c in comments:
        author = (c.get("author_name") or c.get("author_login") or "Неизвестно").strip()
        date = c.get("date") or ""
        with st.expander(f"{author} — {date}"):
            body = c.get("body") or ""
            st.markdown(body, unsafe_allow_html=True)


# ── Page: Employees ───────────────────────────────────────────────────────────

def page_employees():
    st.title("Сотрудники")

    search = st.text_input("Поиск по имени / логину / email", placeholder="Введите текст...")
    params = {}
    if search:
        params["search"] = search

    employees = api_get("/api/employees", params=params)
    if employees is None:
        return

    st.caption(f"Найдено: {len(employees)}")

    for e in employees:
        full_name = (e.get("full_name") or "").strip() or e.get("login", "")
        label = f"**{full_name}** — {e.get('email', '')} ({e.get('login', '')})"
        if st.button(label, key=f"emp_{e['id']}"):
            st.session_state.page = "employee_detail"
            st.session_state.employee_id = e["id"]
            st.rerun()


# ── Page: Employee Detail ─────────────────────────────────────────────────────

def page_employee_detail():
    employee_id = st.session_state.get("employee_id")
    if not employee_id:
        st.session_state.page = "employees"
        st.rerun()
        return

    if st.button("← Назад к сотрудникам"):
        st.session_state.page = "employees"
        st.rerun()

    data = api_get(f"/api/employees/{employee_id}")
    if not data:
        return

    full_name = (data.get("full_name") or "").strip() or data.get("login", "")
    st.title(full_name)

    cols = st.columns(3)
    cols[0].metric("Логин", data.get("login", "—"))
    cols[1].metric("Email", data.get("email", "—"))
    stats = data.get("stats") or {}
    cols[2].metric("Задач", stats.get("task_count", 0))

    tasks = data.get("tasks", [])
    if not tasks:
        st.info("Задач нет")
        return

    st.subheader(f"Задачи ({len(tasks)})")
    for t in tasks:
        project = (t.get("project_name") or "—").strip()
        stage = t.get("stage_name") or "—"
        role = "Ответственный" if t.get("role") == "R" else "Соисполнитель"
        label = f"**{t['name']}** · {project} · {stage} · {role}"
        if st.button(label, key=f"emp_task_{t['id']}"):
            st.session_state.page = "task_detail"
            st.session_state.task_id = t["id"]
            st.session_state.back_page = "employee_detail"
            st.rerun()


# ── Navigation & entry point ──────────────────────────────────────────────────

PAGES = {
    "projects": ("Проекты", page_projects),
    "project_detail": ("Проекты", page_project_detail),
    "tasks": ("Задачи", page_tasks),
    "task_detail": ("Задачи", page_task_detail),
    "employees": ("Сотрудники", page_employees),
    "employee_detail": ("Сотрудники", page_employee_detail),
}

NAV_ITEMS = [
    ("Проекты", "projects"),
    ("Задачи", "tasks"),
    ("Сотрудники", "employees"),
]

st.set_page_config(page_title="Bitrix Viewer", layout="wide")

if "page" not in st.session_state:
    st.session_state.page = "projects"

with st.sidebar:
    st.title("Bitrix Viewer")
    st.divider()
    for label, key in NAV_ITEMS:
        if st.button(label, use_container_width=True):
            st.session_state.page = key
            st.rerun()
    st.divider()
    st.caption(f"API: {API_BASE}")

current_page = st.session_state.get("page", "projects")
_, page_fn = PAGES.get(current_page, PAGES["projects"])
page_fn()
