"""Template context processors — cheap per-user integration status + marketing config."""

from django.conf import settings


def meta_pixel(request):
    return {"meta_pixel_id": settings.META_PIXEL_ID}


def integration_status(request):
    from back.models import Integration

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"integration_status": {}}

    status = {}
    try:
        rows = Integration.objects.filter(user=request.user, is_enabled=True)
        for row in rows:
            status[row.platform] = {
                "connected": bool(row.is_connected),
                "page_name": row.page_name or "",
                "platform": row.get_platform_display(),  # type: ignore[attr-defined]
            }
    except Exception:
        pass
    return {"integration_status": status}