"""
PermissionChecker (P1-3): Role-based access control for tool execution.
"""
import logging

logger = logging.getLogger(__name__)

ROLE_HIERARCHY = ["public", "customer", "staff", "manager", "owner", "support"]

PERMISSION_MAP = {
    "public": 0,
    "customer": 1,
    "staff": 2,
    "manager": 3,
    "owner": 4,
    "support": 5,
}


class PermissionChecker:

    @staticmethod
    def get_user_role(user) -> str:
        """Determine the user's role based on their profile/plan."""
        try:
            profile = getattr(user, "profile", None)
            if profile:
                plan = profile.plan or "free"
                if plan == "enterprise":
                    return "owner"
                if plan == "pro":
                    return "manager"
                if plan == "basic":
                    return "staff"
            if user.is_superuser:
                return "owner"
            if user.is_staff:
                return "manager"
            return "customer"
        except Exception:
            return "customer"

    @staticmethod
    def can_execute(user, tool_name, tool=None) -> tuple[bool, str]:
        """Check if a user has permission to use a tool.

        Returns:
            (can_execute: bool, reason: str)
        """
        if not tool:
            from .tools import ToolRegistry
            tool = ToolRegistry.get(tool_name)

        if not tool:
            return False, f"Unknown tool: {tool_name}"

        required_role = getattr(tool, "permission", "public")
        user_role = PermissionChecker.get_user_role(user)

        user_level = PERMISSION_MAP.get(user_role, 0)
        required_level = PERMISSION_MAP.get(required_role, 0)

        if user_level >= required_level:
            return True, "granted"

        return False, f"Role '{user_role}' lacks '{required_role}' permission for '{tool_name}'"

    @staticmethod
    def filter_tools(user, tools: list) -> list:
        """Filter a list of tools to only those the user can execute."""
        return [
            t for t in tools
            if PermissionChecker.can_execute(user, t.name, tool=t)[0]
        ]
