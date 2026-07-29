# SEO Task List — TheMatrixAi Blog

Audit date: 2026-07-29 | Items: 28 gaps found (92/124 implemented)

---

## 🔴 P0 — Must Fix

- [x] **P0-1**: SEO on standalone pages — pricing.html, login.html, signup.html updated (home01.html must remain untouched per CLAUDE.md)
- [x] **P0-2**: `noindex,follow` on paginated listing pages (page 2+) — post_list, category_list, tag_list
- [x] **P0-3**: `rel="prev"` / `rel="next"` on paginated archive pages
- [x] **P0-4**: Self-referencing canonical that strips `?page=N` on paginated pages
- [x] **P0-5**: OG/Twitter tags on blog listing page (post_list.html)
- [x] **P0-6**: JSON-LD structured data on listing/category/tag pages

## 🟡 P1 — Should Fix

- [x] **P1-1**: Add `meta_title`, `meta_description` fields to Tag model + migration
- [x] **P1-2**: Add TagSitemap to sitemap
- [x] **P1-3**: Add `lastmod` to CategorySitemap and StaticSitemap
- [x] **P1-4**: Create sitemap index structure — /sitemap.xml → sitemap-blog.xml, sitemap-categories.xml, sitemap-tags.xml, sitemap-pages.xml
- [ ] **P1-5**: Add width/height to listing card images to prevent CLS — post_detail hero has width/height, listing cards use CSS 200px height
- [x] **P1-6**: Fix/verify publisher logo URL in JSON-LD — updated to use R2 CDN URL
- [ ] **P1-7**: Newsletter double opt-in / email confirmation
- [x] **P1-8**: Author linking — linked author name in post_detail to /blog/

## 🟢 P2 — Nice to Have

- [ ] **P2-1**: WebP/AVIF image pipeline (easy-thumbnails or sorl-thumbnail)
- [ ] **P2-2**: `<picture>` element with WebP fallback in templates
- [ ] **P2-3**: SearchResultsPage JSON-LD schema on search page
- [x] **P2-4**: Breadcrumb JSON-LD on category/tag/search pages — added to category_list, tag_list via render_breadcrumb_data tag
- [x] **P2-5**: Canonical URL cleanup for search pages — covered by P0-4
- [ ] **P2-6**: Semantic HTML refactor for standalone pages (main, header, section)
- [ ] **P2-7**: Content analytics events (gtag post views, newsletter signups)
- [ ] **P2-8**: SEO dashboard trends (30-day view count)
- [ ] **P2-9**: Bing Webmaster Tools submission note
- [ ] **P2-10**: Image CDN optimization / R2 image transforms
- [ ] **P2-11**: CKEditor image alt-text enforcement
- [x] **P2-12**: `<time datetime>` on all dates — added to post_list, category_list, tag_list, search_results, post_detail
- [ ] **P2-13**: Table `<caption>` and `<th scope>` for accessibility
- [x] **P2-14**: Noopener/noreferrer on external links — upgraded share links to rel="noopener noreferrer"
