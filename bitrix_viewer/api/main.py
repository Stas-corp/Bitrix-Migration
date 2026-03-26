from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import check_connection
from .routers import comments, employees, projects, tasks

app = FastAPI(
    title="Bitrix Viewer API",
    description="Read-only REST API for browsing Bitrix24 data",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(comments.router)
app.include_router(employees.router)


@app.get("/api/health")
def health():
    """Проверяет доступность API и соединение с БД."""
    db = check_connection()
    return {"api": "ok", "db": db}
