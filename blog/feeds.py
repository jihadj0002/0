from django.contrib.syndication.views import Feed, FeedDoesNotExist
from django.utils.feedgenerator import Atom1Feed
from django.urls import reverse
from django.utils import timezone
from .models import BlogPost


class BlogRssFeed(Feed):
    title = "MatrixAi Blog"
    link = "/blog/"
    description = (
        "AI chatbot tips, e-commerce growth strategies, "
        "and customer automation guides from MatrixAi."
    )
    language = "en"
    feed_url = "/blog/feed/rss/"

    def items(self):
        return (
            BlogPost.objects.filter(
                status="published", published_at__lte=timezone.now()
            )
            .select_related("category", "author")
            .order_by("-published_at")[:20]
        )

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.excerpt or item.get_meta_description()

    def item_pubdate(self, item):
        return item.published_at

    def item_updateddate(self, item):
        return item.updated_at

    def item_categories(self, item):
        cats = []
        if item.category:
            cats.append(item.category.name)
        return cats

    def item_author_name(self, item):
        return item.author.get_full_name() or item.author.username

    def item_enclosure_url(self, item):
        if item.featured_image:
            return item.featured_image.url
        return None

    def item_enclosure_length(self, item):
        return 0

    def item_enclosure_mime_type(self, item):
        return "image/jpeg"


class BlogAtomFeed(BlogRssFeed):
    feed_type = Atom1Feed
    subtitle = BlogRssFeed.description
    feed_url = "/blog/feed/atom/"
