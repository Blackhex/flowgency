from .admin_groups import router as admin_groups_router
from .admin_library import router as admin_library_router
from .admin_memory import router as admin_memory_router
from .agent_detail import router as agent_detail_router
from .agents import router as agents_router
from .jobs import router as jobs_router

__all__ = [
    "admin_groups_router",
    "admin_library_router",
    "admin_memory_router",
    "agent_detail_router",
    "agents_router",
    "jobs_router",
]
