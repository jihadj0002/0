from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .services import get_role, is_staff_member


def staff_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not is_staff_member(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return _wrapped


def crm_role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @staff_required
        def _wrapped(request, *args, **kwargs):
            if get_role(request.user) not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


class CrmStaffGuardMiddleware:
    """Staff (non-owner) are redirected to /crm/; /db/ is owner-only."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.user.is_authenticated:
            return response
        if request.path.startswith("/db/") and is_staff_member(request.user):
            if get_role(request.user) != "owner":
                return redirect("/crm/")
        return response
