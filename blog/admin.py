from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count
from django.urls import path
from django.shortcuts import render
from django.contrib import messages
from .models import BlogPost, Category, Tag, Subscriber


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "post_count", "created_at"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "description")}),
        (
            "SEO",
            {
                "fields": ("meta_title", "meta_description"),
                "classes": ("collapse",),
                "description": "Leave blank to auto-generate",
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(post_count=Count("blogpost"))

    def post_count(self, obj):
        return obj.post_count

    post_count.short_description = "Posts"


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


class IsFeaturedFilter(admin.SimpleListFilter):
    title = "featured"
    parameter_name = "is_featured"

    def lookups(self, request, model_admin):
        return [("yes", "Featured"), ("no", "Not Featured")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(is_featured=True)
        if self.value() == "no":
            return queryset.filter(is_featured=False)


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "status_badge",
        "category",
        "author",
        "published_at",
        "view_count",
        "is_featured",
        "is_pinned",
    ]
    list_filter = ["status", IsFeaturedFilter, "category", "published_at"]
    search_fields = ["title", "excerpt", "content"]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["author", "category", "tags"]
    date_hierarchy = "published_at"
    actions = ["make_published", "make_draft", "make_featured", "unfeature"]
    readonly_fields = ["view_count", "created_at", "updated_at", "thumbnail_preview"]

    fieldsets = (
        (
            "Content",
            {
                "fields": (
                    "title",
                    "slug",
                    "excerpt",
                    "content",
                    "featured_image",
                    "thumbnail_preview",
                    "alt_text",
                )
            },
        ),
        (
            "Categorization",
            {"fields": ("category", "tags")},
        ),
        (
            "SEO & Social",
            {
                "fields": ("meta_title", "meta_description", "og_image", "canonical_url"),
                "classes": ("collapse",),
                "description": "Leave blank to auto-generate from post content",
            },
        ),
        (
            "Publishing",
            {
                "fields": (
                    "author",
                    "status",
                    "published_at",
                    "is_featured",
                    "is_pinned",
                    "allow_comments",
                )
            },
        ),
        (
            "Stats",
            {
                "fields": ("view_count", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)

    def status_badge(self, obj):
        colors = {"draft": "gray", "published": "green", "scheduled": "orange"}
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="color:{};font-weight:600;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    def thumbnail_preview(self, obj):
        if obj.featured_image:
            return format_html(
                '<img src="{}" style="max-height:100px;border-radius:4px;" />',
                obj.featured_image.url,
            )
        return "—"

    thumbnail_preview.short_description = "Preview"

    def make_published(self, request, queryset):
        from django.utils import timezone

        updated = queryset.update(status="published", published_at=timezone.now())
        self.message_user(
            request, f"{updated} post(s) marked as published.", messages.SUCCESS
        )

    make_published.short_description = "Mark selected as published"

    def make_draft(self, request, queryset):
        updated = queryset.update(status="draft")
        self.message_user(request, f"{updated} post(s) marked as draft.")

    make_draft.short_description = "Mark selected as draft"

    def make_featured(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(
            request, f"{updated} post(s) marked as featured.", messages.SUCCESS
        )

    make_featured.short_description = "Mark selected as featured"

    def unfeature(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"{updated} post(s) unfeatured.")

    unfeature.short_description = "Unfeature selected"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "seo-dashboard/",
                self.admin_site.admin_view(self.seo_dashboard_view),
                name="blog_seo_dashboard",
            ),
        ]
        return custom_urls + urls

    def seo_dashboard_view(self, request):
        total = BlogPost.objects.count()
        published = BlogPost.objects.filter(status="published").count()
        drafts = BlogPost.objects.filter(status="draft").count()
        missing_meta_title = BlogPost.objects.filter(meta_title="").count()
        missing_meta_desc = BlogPost.objects.filter(meta_description="").count()
        missing_alt = BlogPost.objects.filter(alt_text="").count()
        no_category = BlogPost.objects.filter(category__isnull=True).count()
        top_viewed = BlogPost.objects.order_by("-view_count")[:10]
        recent = BlogPost.objects.filter(status="published").order_by(
            "-published_at"
        )[:10]

        return render(
            request,
            "blog/admin/seo_dashboard.html",
            {
                "total": total,
                "published": published,
                "drafts": drafts,
                "missing_meta_title": missing_meta_title,
                "missing_meta_desc": missing_meta_desc,
                "missing_alt": missing_alt,
                "no_category": no_category,
                "top_viewed": top_viewed,
                "recent": recent,
                "title": "SEO Dashboard",
            },
        )


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ["email", "is_active", "source", "created_at"]
    list_filter = ["is_active", "source"]
    search_fields = ["email"]
    actions = ["mark_active", "mark_inactive"]

    def mark_active(self, request, queryset):
        queryset.update(is_active=True)
        self.message_user(request, "Marked as active.", messages.SUCCESS)

    mark_active.short_description = "Mark selected as active"

    def mark_inactive(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, "Marked as inactive.")

    mark_inactive.short_description = "Mark selected as inactive"
