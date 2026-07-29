from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.urls import reverse
from django_ckeditor_5.fields import CKEditor5Field
import math


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    meta_title = models.CharField(max_length=70, blank=True)
    meta_description = models.TextField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("blog:category", kwargs={"slug": self.slug})


class Tag(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(max_length=60, unique=True)
    meta_title = models.CharField(max_length=70, blank=True)
    meta_description = models.TextField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("blog:tag", kwargs={"slug": self.slug})


class BlogPost(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("scheduled", "Scheduled"),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    excerpt = models.TextField(
        blank=True, help_text="Short summary for listings & meta description fallback"
    )
    content = CKEditor5Field(config_name="blog")
    featured_image = models.ImageField(
        upload_to="blog/featured/", blank=True, null=True
    )
    alt_text = models.CharField(
        max_length=150, blank=True, help_text="Alt text for featured image"
    )
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True
    )
    tags = models.ManyToManyField(Tag, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    is_featured = models.BooleanField(
        default=False, help_text="Show in featured hero slot"
    )
    is_pinned = models.BooleanField(
        default=False, help_text="Sticky at top of listing"
    )
    allow_comments = models.BooleanField(default=True)

    meta_title = models.CharField(max_length=70, blank=True)
    meta_description = models.TextField(max_length=160, blank=True)
    og_image = models.ImageField(
        upload_to="blog/og/",
        blank=True,
        null=True,
        help_text="Overrides featured_image for social sharing",
    )
    canonical_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="Only if this post exists on another domain",
    )

    view_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-published_at"]
        indexes = [
            models.Index(fields=["status", "published_at"]),
            models.Index(fields=["slug"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title)
            slug = base_slug
            counter = 1
            while BlogPost.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("blog:detail", kwargs={"slug": self.slug})

    def get_meta_title(self):
        return self.meta_title or self.title

    def get_meta_description(self):
        if self.meta_description:
            return self.meta_description
        if self.excerpt:
            return self.excerpt[:160]
        from django.utils.html import strip_tags

        plain = strip_tags(self.content)[:160]
        return plain

    def get_og_image_url(self):
        if self.og_image:
            return self.og_image.url
        if self.featured_image:
            return self.featured_image.url
        return ""

    def reading_time_minutes(self):
        from django.utils.html import strip_tags

        plain = strip_tags(self.content)
        word_count = len(plain.split())
        return max(1, math.ceil(word_count / 200))


class Subscriber(models.Model):
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=50, default="blog")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email
