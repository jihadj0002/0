from django import template
from django.utils.html import strip_tags, escape
from django.utils.safestring import mark_safe

register = template.Library()
SITE_NAME = "MatrixAi"


def _format_title(title):
    if title.endswith(f"| {SITE_NAME}") or title.endswith(SITE_NAME):
        return title
    return f"{title} | {SITE_NAME}"


@register.simple_tag(takes_context=True)
def render_meta_tags(context, post=None):
    request = context.get("request")

    if post:
        title = _format_title(post.get_meta_title())
        description = post.get_meta_description()
    else:
        title = f"{SITE_NAME} — AI Customer Support for Your Business"
        description = (
            "AI chatbot for WhatsApp, Messenger, Instagram, and Telegram. "
            "Automate customer support, take orders, and grow your business 24/7."
        )

    tags = [
        f'<meta name="description" content="{escape(description)}">',
    ]
    return mark_safe("\n".join(tags))


@register.simple_tag(takes_context=True)
def render_og_tags(context, post=None):
    request = context.get("request")
    url = request.build_absolute_uri() if request else ""
    site_url = f"{request.scheme}://{request.get_host()}" if request else ""

    if post:
        title = post.get_meta_title()
        description = post.get_meta_description()
        og_image = post.get_og_image_url()
        og_type = "article"
    else:
        title = f"{SITE_NAME} — AI Customer Support for Your Business"
        description = (
            "AI chatbot for WhatsApp, Messenger, Instagram, and Telegram. "
            "Automate customer support, take orders, and grow your business 24/7."
        )
        og_image = f"{site_url}/static/images/og-default.png"
        og_type = "website"

    tags = [
        f'<meta property="og:title" content="{escape(title)}">',
        f'<meta property="og:description" content="{escape(description)}">',
        f'<meta property="og:url" content="{escape(url)}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
    ]
    if og_image:
        tags.append(f'<meta property="og:image" content="{escape(og_image)}">')
        tags.append('<meta property="og:image:width" content="1200">')
        tags.append('<meta property="og:image:height" content="630">')

    tags.extend([
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{escape(title)}">',
        f'<meta name="twitter:description" content="{escape(description)}">',
    ])
    if og_image:
        tags.append(f'<meta name="twitter:image" content="{escape(og_image)}">')

    return mark_safe("\n".join(tags))


@register.simple_tag(takes_context=True)
def render_structured_data(context, post=None):
    request = context.get("request")
    import json

    if not post:
        return mark_safe("")

    url = request.build_absolute_uri() if request else ""
    site_url = f"{request.scheme}://{request.get_host()}" if request else ""

    data = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": post.get_meta_title(),
        "description": post.get_meta_description(),
        "image": post.get_og_image_url(),
        "datePublished": post.published_at.isoformat() if post.published_at else "",
        "dateModified": post.updated_at.isoformat(),
        "author": {
            "@type": "Person",
            "name": post.author.get_full_name() or post.author.username,
        },
        "publisher": {
            "@type": "Organization",
            "name": SITE_NAME,
            "logo": {
                "@type": "ImageObject",
                "url": f"{site_url}/static/images/logo.png",
            },
        },
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "wordCount": len(strip_tags(post.content).split()),
    }
    html = f"<script type=\"application/ld+json\">{json.dumps(data, indent=2)}</script>"
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def render_breadcrumb_data(context, post=None):
    import json
    request = context.get("request")
    site_url = f"{request.scheme}://{request.get_host()}" if request else ""

    items = [
        {"@type": "ListItem", "position": 1, "name": "Home",
         "item": f"{site_url}/"},
        {"@type": "ListItem", "position": 2, "name": "Blog",
         "item": f"{site_url}/blog/"},
    ]

    if post and post.category:
        items.append({
            "@type": "ListItem", "position": 3, "name": post.category.name,
            "item": f"{site_url}/blog/category/{post.category.slug}/",
        })

    if post:
        url = request.build_absolute_uri() if request else ""
        items.append({
            "@type": "ListItem", "position": len(items) + 1,
            "name": post.title, "item": url,
        })

    data = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": items,
    }
    html = f"<script type=\"application/ld+json\">{json.dumps(data, indent=2)}</script>"
    return mark_safe(html)


@register.simple_tag(takes_context=True)
def blog_cta_url(context, url_path):
    request = context.get("request")
    slug = getattr(getattr(context.get("post"), "slug", None), "slug", None)
    if not slug:
        post = context.get("post")
        if post:
            slug = post.slug
    sep = "&" if "?" in url_path else "?"
    utm = f"utm_source=blog&utm_medium=organic&utm_campaign=post-{slug or 'default'}"
    return f"{url_path}{sep}{utm}"
