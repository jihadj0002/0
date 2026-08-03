from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include, re_path
from django.contrib.sitemaps.views import sitemap, index as sitemap_index
from django.http import HttpResponse
from blog.sitemaps import BlogPostSitemap, CategorySitemap, TagSitemap, StaticSitemap

all_sitemaps = {
    "blog": BlogPostSitemap,
    "categories": CategorySitemap,
    "tags": TagSitemap,
    "pages": StaticSitemap,
}


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /db/",
        "Disallow: /api/",
        "",
        f"Sitemap: {request.scheme}://{request.get_host()}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


urlpatterns = [
    path("admin", admin.site.urls),
    path("", include("front.urls")),
    path("db", include("back.urls")),
    path("api/", include("api.urls")),
    path("crm/", include("crm.urls")),
    path("crm/hiring/", include("hiring.admin_urls")),
    path("careers/", include("hiring.urls")),
    path("msg/", include("msg.urls")),
    path("blog/", include("blog.urls")),
    path("ckeditor5/", include("django_ckeditor_5.urls")),
    re_path(r"^sitemap-(?P<section>.+)\.xml$", sitemap, {"sitemaps": all_sitemaps}, name="sitemap-section"),
    path("sitemap.xml", sitemap_index, {"sitemaps": all_sitemaps, "sitemap_url_name": "sitemap-section"}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots_txt"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
else:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

