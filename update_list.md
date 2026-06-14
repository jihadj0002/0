# Update List

## 1. Dashboard Performance Fixes

### Critical
- [ ] Add `.defer('raw_payload')` to message queryset in `ajax_load_messages`
- [ ] Add `.only()` with 8 fields to conversation queryset in `ajax_load_conversations`
- [ ] Add `LIMIT 50` to message queryset with `has_more` flag for progressive loading ("load older" button)
- [ ] Remove redundant `last_msg` query — use `conversation.message_text` instead

### High
- [ ] Add composite index `(user, platform, updated_at)` on Conversation model
- [ ] Add composite index `(conversation, id)` on Message model
- [ ] Defer `raw_payload` in `get_conversation_history` (api/ai/context.py)
- [ ] Pause dashboard polling when no conversation is selected (JS)
- [ ] Use Page Visibility API to pause polling when tab is backgrounded

### Medium
- [ ] Cap client-side `msgCache` to prevent memory leak
- [ ] Remove `icontains` search inefficiency — use `trigram_similar` or server-side search

---

## 2. Reply Latency Fixes

### Critical
- [ ] Add explicit timeout (30s) to WhatsApp media download in `_persist_message`
- [ ] Add explicit timeout (15s) to external product provider calls (WooCommerce/Shopify/ERP)

### High
- [ ] Parallelize image sending in `sender.py` — send 5 images concurrently instead of sequentially
- [ ] Separate thread pools: one for media/vision/audio processing, one for AI pipeline
- [ ] Add retry logic for LLM calls (transient failures should retry, not abort the tool loop)

### Medium
- [ ] Add circuit breaker for external product providers (slow ERP shouldn't degrade pipeline)
- [ ] Add timeout to vision analysis call in `_persist_message`
- [ ] Add timeout to audio transcription call in `_persist_message`

---

## 3. Profile Image Fix

- [ ] In `_fetch_and_update_profile`: store profile pic via `download_profile_to_storage()` instead of raw URL (so dashboard `hasattr(url)` works and shows the image)

---

## 4. Ticket System (Support)

### Model & Tool
- [ ] New `SupportTicket` model in `back/models.py` (OneToOne to Conversation, fields: subject, description, status, priority, assigned_to, resolved_at)
- [ ] Replace `transfer_chat` tool with `create_ticket(subject, description, priority)` tool in `api/ai/tools.py`
- [ ] Update `tool_create_ticket` handler — creates ticket record + disables AI
- [ ] Update pipeline.py to handle `create_ticket` instead of `transfer_chat`
- [ ] Update system prompt in `api/ai/context.py` to reference `create_ticket`

### Dashboard
- [ ] New `/db/tickets/` view with filterable queue (open/in_progress/resolved/closed/all)
- [ ] New `back/templates/back/tickets.html` template
- [ ] Ajax endpoints: claim ticket, resolve ticket, reopen ticket
- [ ] Link from ticket to conversation in chat dashboard

### Admin
- [ ] Register `SupportTicket` in `back/admin.py`
- [ ] Migration: `python manage.py makemigrations back`

---

## 5. Billing & Credit Admin

- [ ] Admin top-up flow works via `/admin/billing/userbalance/` → select users → "Add credits (top-up)" action → enter amount

---

## 6. Enable/Disable All Bots (done)

- [x] Removed `sync_ai_status_to_conversations` signal — integration toggle no longer retroactively updates all conversations
- [x] `enable_all_bots` now only enables conversations belonging to active integrations
- [x] `disable_all_bots` stays unchanged (disables all)

---

## 7. External Product Search Improvements (done)

- [x] `_external_row` uses SKU as pid (so ERP can search by SKU instead of numeric ID)
- [x] `external_id` preserved as separate field for create_order
- [x] Focused products checked FIRST in `get_product_details` — zero API calls when cached
- [x] Search fallback added in `create_order` for SKU-based pid lookups
- [x] `send_images` card button uses `card.get("sku") or card.get("pid")`
- [x] System prompt updated to skip `get_product_details` for focused products
- [x] Tool descriptions updated: `send_images` returns product info, `get_product_details` marked as "last resort"
- [x] Budget params (min_price/max_price) added to `search_products`
- [x] Empty external search falls through to local DB
- [x] Focused products cache checked as last resort in `get_product_details`

---

## 8. Webhook & Mid Collision (done)

- [x] Mid collision detection added in `_persist_message` checks conversation ownership before treating duplicate as retry

---

## 9. Product Availability

### High
- [ ] When stock hits 0, mark product as out of stock in external provider response
- [ ] Show unavailable/out-of-stock notice on product cards sent via send_images
- [ ] In create_order, check stock before allowing order placement
- [ ] Add out_of_stock field to send_images response for frontend display
- [ ] In search_products, filter out-of-stock products or mark them clearly
- [ ] Add stock status tracking in tool responses so AI knows when to suggest alternatives

### Admin
- [ ] Add stock management section in admin panel
- [ ] Quick stock update form for individual products
- [ ] Bulk stock update via CSV/Excel import
- [ ] Low stock threshold notifications

---

## 10. Customer Profile Enhancements

### High
- [ ] Add `customer_email` field to Conversation model
- [ ] Add `customer_address_history` JSONField to track past addresses
- [ ] Add `order_history_summary` field to Conversation for quick reference
- [ ] Display customer order history in dashboard right panel

### Medium
- [ ] Customer segmentation (new / returning / VIP)
- [ ] Total spend tracking on customer profile
- [ ] Last order date and order count on conversation list
- [ ] Customer notes field for agents

---

## 11. Notification System

### High
- [ ] Add notification model (Notification: user, type, message, link, is_read, created_at)
- [ ] Notify agent when new ticket is created
- [ ] Notify agent when customer sends message while AI is off
- [ ] Dashboard notification badge/counter
- [ ] Sound notification for new messages

### Medium
- [ ] Email notifications for ticket assignment
- [ ] Email notifications for unresolved tickets (daily digest)
- [ ] In-app toast notifications for transfers

---

## 12. Order Management Dashboard

### High
- [ ] `/db/orders/` page with filterable order list (pending/completed/refunded)
- [ ] Order detail view with customer info, items, status history
- [ ] Bulk order status update (mark as completed, refunded)
- [ ] Search orders by ID, customer name, phone, platform

### Medium
- [ ] Order export to CSV
- [ ] Print order invoice
- [ ] Delivery tracking integration
- [ ] Order notes for agents

---

## 13. Advanced Reporting

### Medium
- [ ] Daily/weekly/monthly sales report
- [ ] Top selling products report
- [ ] Conversation volume by platform
- [ ] Average response time report
- [ ] Credit usage report per user
- [ ] Export all reports to CSV

---

## 14. Security & Reliability

### High
- [ ] Add rate limiting on webhook endpoints (prevent abuse)
- [ ] Add request validation on all API endpoints
- [ ] Add audit logging for all admin actions

### Medium
- [ ] Add health check endpoint
- [ ] Add monitoring/logging for pipeline failures
- [ ] Add WebSocket for real-time chat instead of polling
- [ ] Add database connection pooling configuration

---

## 15. UI/UX Improvements

### Medium
- [ ] Dark mode toggle for dashboard
- [ ] Message search within conversation
- [ ] Keyboard shortcuts (Ctrl+Enter to send, Escape to close)
- [ ] Typing indicator when AI is generating response
- [ ] Message timestamps in chat (show date separators)
- [ ] Image gallery view for product images

---

## 16. Multi-language Support

### Medium
- [ ] Language detection on incoming messages
- [ ] Auto-translate customer messages to agent's language
- [ ] Agent reply translation (optional)
- [ ] Template-based responses in multiple languages

---

## 17. AI Pipeline Improvements

### Medium
- [ ] Streaming LLM responses for faster first-token delivery
- [ ] Response caching for common queries (FAQs)
- [ ] Smart batching: combine related tool calls into one LLM response
- [ ] Pipeline timeout (abort after 120s total)
- [ ] Pipeline queue visibility in dashboard

---

## 18. Backup & Data Management

### Low
- [ ] Automated daily database backup
- [ ] Media files backup (R2/cloud storage)
- [ ] Data retention policy (auto-delete old raw_payloads)
- [ ] Export all customer data for a user (GDPR-style)

---

## 19. API Documentation

### Low
- [ ] Public API docs for webhook endpoints
- [ ] API docs for REST endpoints (ERP/mobile)
- [ ] Webhook payload schema documentation
- [ ] Rate limit and error code documentation
