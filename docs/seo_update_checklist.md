For a modern Django project (2025–2026), the approach is no longer just "generate a sitemap.xml." Search engines and AI-powered search systems (Google AI Overviews, ChatGPT browsing, Perplexity, Bing Copilot, etc.) look for a combination of structured technical SEO signals.

Here's what I'd recommend.

1. Use Django's built-in sitemap framework

Django already includes a mature sitemap framework.

Install in INSTALLED_APPS:

INSTALLED_APPS = [
    ...
    "django.contrib.sitemaps",
]

Example project structure:

project/
    urls.py

blog/
    models.py
    sitemap.py

Example:

# blog/sitemap.py

from django.contrib.sitemaps import Sitemap
from .models import BlogPost

class BlogSitemap(Sitemap):
    protocol = "https"

    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return (
            BlogPost.objects
            .filter(status="published")
            .order_by("-updated_at")
        )

    def lastmod(self, obj):
        return obj.updated_at

In urls.py

from django.contrib.sitemaps.views import sitemap
from blog.sitemap import BlogSitemap

sitemaps = {
    "blog": BlogSitemap,
}

urlpatterns = [
    ...
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": sitemaps},
        name="django.contrib.sitemaps.views.sitemap",
    ),
]

Result:

https://example.com/sitemap.xml
2. Create a Sitemap Index

If your site has:

blog
docs
products
categories
pages

don't put everything in one sitemap.

Instead:

sitemap.xml

contains

blog.xml
pages.xml
categories.xml
authors.xml

Google prefers sitemap indexes for larger sites.

Example:

sitemap.xml

├── sitemap-blog.xml
├── sitemap-pages.xml
├── sitemap-categories.xml
└── sitemap-news.xml
3. Include only canonical URLs

Do NOT include

?page=2

?sort=new

?filter=python

/search?q=django

Only include

/blog/django-seo-guide/
/blog/python-basics/
/about/
4. Add last modification dates

This is probably the most important sitemap field.

Google wants

<lastmod>
2026-07-20
</lastmod>

Use

updated_at

not

created_at
5. Add robots.txt
User-agent: *

Allow: /

Sitemap: https://example.com/sitemap.xml

This is still recommended.

6. Add canonical URLs

Every page:

<link rel="canonical"
      href="https://example.com/blog/my-post/">

Avoid duplicate URLs.

7. Structured Data (Very Important)

This is arguably more impactful than the sitemap itself for rich search results.

For blog posts include JSON-LD like:

{
  "@context":"https://schema.org",
  "@type":"BlogPosting",
  "headline":"Django SEO Guide",
  "author":{
      "@type":"Person",
      "name":"John"
  },
  "datePublished":"2026-07-10",
  "dateModified":"2026-07-15",
  "image":"https://...",
  "mainEntityOfPage":"https://..."
}

Google increasingly relies on structured data to understand content.

8. Open Graph + Twitter Cards
<meta property="og:title">
<meta property="og:description">
<meta property="og:image">

<meta name="twitter:card">

Useful when links are shared.

9. Proper Meta Tags

Each page should have

<title>

<meta name="description">

<meta name="robots">

<link rel="canonical">

The title should be unique and descriptive.

10. Semantic HTML

Instead of

<div>
<div>
<div>

Use

<article>

<header>

<section>

<nav>

<footer>

<aside>

<main>

AI systems and search engines parse semantic HTML more effectively.

11. Internal Linking

Every blog article should link to:

related posts
categories
author
previous article
next article

Avoid orphan pages that have no internal links.

12. Generate Slugs

Use

example.com/blog/django-sitemap-guide/

Not

example.com/post?id=451
13. Images

Every image should have

<img
    src="..."
    alt="How Django Sitemap Works">

Descriptive alt text helps accessibility and image search.

14. Core Web Vitals

Optimize:

lazy loading images
WebP/AVIF
compression (Brotli/Gzip)
CDN
caching
minimized CSS/JS

Performance affects user experience and can influence search visibility.

15. AI-Friendly Content

Modern AI search systems don't use a special "AI sitemap." They primarily consume your regular HTML and structured data. To make content easier for them to interpret:

Use one clear <h1> per page.
Organize content with a logical heading hierarchy (<h2>, <h3>, etc.).
Write concise introductions that summarize the page.
Answer common questions directly.
Include lists, tables, and code blocks where appropriate.
Mark up articles with BlogPosting or Article schema.
Avoid hiding important content behind JavaScript when possible.
16. Submit to Search Consoles

After deploying:

Add your property to Google Search Console.
Submit https://example.com/sitemap.xml.
Monitor indexing, coverage, and enhancements reports.
Optionally submit the sitemap to Bing Webmaster Tools as well.
17. Suggested Django SEO Architecture
project/
│
├── seo/
│     ├── context_processors.py
│     ├── schema.py
│     ├── meta.py
│     └── sitemap.py
│
├── blog/
│     ├── models.py
│     ├── views.py
│     ├── sitemap.py
│     └── templates/
│
├── templates/
│     └── base.html
│
├── robots.txt
├── sitemap.xml
└── favicon.ico
Recommended model fields

Your BlogPost model should include fields that support SEO and sitemaps:

title
slug
summary
content
author
status
published_at
updated_at
seo_title
meta_description
featured_image
canonical_url
Overall SEO Checklist
Feature	Recommended
Django sitemap framework	✅
Sitemap index	✅
Separate sitemaps by content type	✅
lastmod using updated_at	✅
robots.txt with sitemap URL	✅
Canonical URLs	✅
JSON-LD (BlogPosting)	✅
Open Graph & Twitter Cards	✅
Semantic HTML	✅
Descriptive image alt text	✅
Internal linking	✅
Core Web Vitals optimization	✅
Google Search Console submission	✅

This setup aligns well with current best practices for traditional search engines and AI-powered search experiences. It provides clear crawl paths, structured metadata, and high-quality page signals without relying on deprecated SEO techniques.