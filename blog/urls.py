from django.urls import path
from . import views
from .feeds import BlogRssFeed, BlogAtomFeed

app_name = "blog"

urlpatterns = [
    path("", views.PostListView.as_view(), name="list"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("subscribe/", views.SubscribeView.as_view(), name="subscribe"),
    path("feed/rss/", BlogRssFeed(), name="rss_feed"),
    path("feed/atom/", BlogAtomFeed(), name="atom_feed"),
    path("category/<slug:slug>/", views.CategoryListView.as_view(), name="category"),
    path("tag/<slug:slug>/", views.TagListView.as_view(), name="tag"),
    path("<slug:slug>/", views.PostDetailView.as_view(), name="detail"),
]
