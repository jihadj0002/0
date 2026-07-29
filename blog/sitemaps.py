from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone
from .models import BlogPost, Category


class BlogPostSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return BlogPost.objects.filter(
            status="published", published_at__lte=timezone.now()
        ).select_related("category")

    def lastmod(self, obj):
        return obj.updated_at or obj.published_at

    def location(self, obj):
        return obj.get_absolute_url()


class CategorySitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return Category.objects.all()

    def location(self, obj):
        return reverse("blog:category", kwargs={"slug": obj.slug})


class StaticSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.3

    def items(self):
        return ["front:home", "front:pricing", "front:p_policy", "front:terms"]

    def location(self, item):
        return reverse(item)
