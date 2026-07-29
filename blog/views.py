from django.views.generic import ListView, DetailView
from django.views import View
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.db.models import F, Q
from django.utils import timezone
from .models import BlogPost, Category, Tag, Subscriber


class PostListView(ListView):
    model = BlogPost
    template_name = "blog/post_list.html"
    context_object_name = "posts"
    paginate_by = 9

    def get_queryset(self):
        qs = (
            BlogPost.objects.filter(
                status="published", published_at__lte=timezone.now()
            )
            .select_related("category", "author")
            .prefetch_related("tags")
        )
        featured = qs.filter(is_featured=True).first()
        if featured:
            qs = qs.exclude(pk=featured.pk)
        return qs.order_by("-is_pinned", "-published_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        featured = BlogPost.objects.filter(
            status="published", published_at__lte=timezone.now(),
            is_featured=True
        ).first()
        context["featured_post"] = featured
        context["categories"] = Category.objects.all()
        context["recent_posts"] = BlogPost.objects.filter(
            status="published", published_at__lte=timezone.now()
        ).order_by("-published_at")[:5]
        return context


class PostDetailView(DetailView):
    model = BlogPost
    template_name = "blog/post_detail.html"
    context_object_name = "post"

    def get_queryset(self):
        return (
            BlogPost.objects.filter(
                status="published", published_at__lte=timezone.now()
            )
            .select_related("category", "author")
            .prefetch_related("tags")
        )

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        BlogPost.objects.filter(pk=obj.pk).update(view_count=F("view_count") + 1)
        obj.refresh_from_db()
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        post = context["post"]

        related = BlogPost.objects.filter(
            status="published", published_at__lte=timezone.now()
        ).exclude(pk=post.pk)
        if post.category:
            related = related.filter(category=post.category)
        context["related_posts"] = related[:3]

        prev_qs = (
            BlogPost.objects.filter(
                status="published",
                published_at__lte=timezone.now(),
                published_at__lt=post.published_at,
            )
            .order_by("-published_at")
            .first()
        )
        next_qs = (
            BlogPost.objects.filter(
                status="published",
                published_at__lte=timezone.now(),
                published_at__gt=post.published_at,
            )
            .order_by("published_at")
            .first()
        )
        context["prev_post"] = prev_qs
        context["next_post"] = next_qs
        return context


class CategoryListView(ListView):
    model = BlogPost
    template_name = "blog/category_list.html"
    context_object_name = "posts"
    paginate_by = 9

    def get_queryset(self):
        return (
            BlogPost.objects.filter(
                status="published",
                published_at__lte=timezone.now(),
                category__slug=self.kwargs["slug"],
            )
            .select_related("category", "author")
            .order_by("-published_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["category"] = get_object_or_404(Category, slug=self.kwargs["slug"])
        context["categories"] = Category.objects.all()
        return context


class TagListView(ListView):
    model = BlogPost
    template_name = "blog/tag_list.html"
    context_object_name = "posts"
    paginate_by = 9

    def get_queryset(self):
        return (
            BlogPost.objects.filter(
                status="published",
                published_at__lte=timezone.now(),
                tags__slug=self.kwargs["slug"],
            )
            .select_related("category", "author")
            .order_by("-published_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import Tag
        context["tag"] = get_object_or_404(Tag, slug=self.kwargs["slug"])
        context["categories"] = Category.objects.all()
        return context


class SearchView(ListView):
    model = BlogPost
    template_name = "blog/search_results.html"
    context_object_name = "posts"
    paginate_by = 9

    def get_queryset(self):
        query = self.request.GET.get("q", "").strip()
        if not query:
            return BlogPost.objects.none()
        return (
            BlogPost.objects.filter(
                Q(title__icontains=query)
                | Q(excerpt__icontains=query)
                | Q(content__icontains=query),
                status="published",
                published_at__lte=timezone.now(),
            )
            .select_related("category", "author")
            .order_by("-published_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "")
        context["categories"] = Category.objects.all()
        return context


class SubscribeView(View):
    def post(self, request):
        email = request.POST.get("email", "").strip()
        if email:
            Subscriber.objects.get_or_create(email=email)
            messages.success(request, "Thanks for subscribing!")
        else:
            messages.error(request, "Please provide a valid email.")
        return redirect(request.META.get("HTTP_REFERER", "/blog/"))
