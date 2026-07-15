from .admin_groups import router as admin_groups_router
from .agent_detail import router as agent_detail_router
from .agents import router as agents_router

__all__ = ["admin_groups_router", "agent_detail_router", "agents_router"]