from .admin_teams import router as admin_teams_router
from .admin_library import router as admin_library_router
from .admin_memory import router as admin_memory_router
from .agent_detail import router as agent_detail_router
from .agent_permissions import router as agent_permissions_router
from .agent_routines import router as agent_routines_router
from .agents import router as agents_router
from .jobs import router as jobs_router
from .tickets import router as tickets_router
from .workflows import router as workflows_router

__all__ = [
    "admin_teams_router",
    "admin_library_router",
    "admin_memory_router",
    "agent_detail_router",
    "agent_permissions_router",
    "agent_routines_router",
    "agents_router",
    "jobs_router",
    "tickets_router",
    "workflows_router",
]
