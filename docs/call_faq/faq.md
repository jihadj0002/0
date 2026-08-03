# MatrixAI FAQ

**Purpose**: Single source of truth for customer-facing questions. Used by the sales team before/after calls and by the CRM FAQ page.
**Aligned with**: `00_CONTEXT/features_bengali.md`, `00_CONTEXT/pricing.md` (BDT 999 / 2499 / 4999 plans).
**Version**: 3.0 | **Last updated**: 2026-08-04

---

## Quick Answers (TL;DR — for the 7 most-asked questions)

| Customer asks | One-line answer |
|---------------|-----------------|
| "কত টাকা?" | "৯৯৯ টাকা থেকে মাসে — দিনে মাত্র ৩৩ টাকা। ডেমো দেখেন তো?" |
| "কী কী করে?" | "মেসেজের উত্তর ১ সেকেন্ডে, প্রোডাক্ট দেখায়, অর্ডার নেয় — ২৪/৭।" |
| "কোন চ্যানেল?" | "Facebook, Instagram, WhatsApp — সব এক জায়গায় (২৪৯৯+ এ Telegram)।" |
| "কতক্ষণে সেটআপ?" | "১০-৩০ মিনিট — আমরা করে দিই, আপনি কিছুই করবেন না।" |
| "আগে চেষ্টা করা যায়?" | "জি — ফ্রি ডেমো সেটআপ, পছন্দ না হলে বন্ধ।" |
| "বাংলা বোঝে?" | "বাংলা, ইংরেজি, বাংলিশ — সব, কাস্টমারের নিজের ভাষায়।" |
| "পেজ ব্লক হবে?" | "Meta নিয়ম মেনে চলি — compliant by design।" |

---

## Getting Started

### 1. What is MatrixAI in simple terms?
MatrixAI is your 24/7 sales assistant for Facebook, Instagram, and WhatsApp. It answers repetitive questions (price, size, availability), sends DMs to commenters, captures orders inside the chat, and hands off to a human whenever needed.

**Agents, say it like this**: "আপনার পেজের AI সেলস অ্যাজেন্ট — কাস্টমার মেসেজ দিলে ১ সেকেন্ডে উত্তর, প্রোডাক্ট দেখায়, অর্ডার নেয়, ২৪/৭।"

### 2. Do I need a website or online store to use MatrixAI?
No. You sell directly through Facebook, Instagram, and WhatsApp. MatrixAI replies with prices, photos, and checkout options right inside the conversation.

### 3. Which channels are supported?
- Facebook Page inbox + post comments (Messenger)
- Instagram DMs + post/reel comments
- WhatsApp Business (Cloud API or via a Business Solution Provider)

### 4. Who is MatrixAI for?
Social sellers running businesses on social media: Facebook e-commerce pages, Instagram shops, and WhatsApp Business accounts. Ideal if you answer "দাম কত?" many times a day — those repeated messages are lost sales you can automate.

---

## Connections & Integrations

### 5. What do I need to connect Instagram?
- An Instagram **Business** account (not personal)
- It must be linked to a Facebook Page
- Admin access to both
- "Allow Access to Messages" enabled in Instagram settings

### 6. What do I need to connect Facebook?
- Admin role on the Facebook Page
- Permission to manage messages and comments
- Accept requested permissions during the connection flow

### 7. How do I connect WhatsApp Business?
- A verified Facebook Business account
- A phone number not currently registered on the WhatsApp mobile app
- WhatsApp Cloud API set up (or connect via your BSP)
- Approved message templates for proactive notifications

### 8. Can I use a personal Instagram account?
No. Instagram must be a Business account linked to a Facebook Page to access messaging.

---

## Catalog & Auto-Replies

### 9. How does MatrixAI know prices and product details?
You import a product catalog via CSV or Google Sheets (name, SKU, price, stock, sizes, image URL). MatrixAI looks up the product and auto-fills replies with the latest info.

### 10. What file formats are supported for product imports?
CSV upload or Google Sheets link. Recommended columns: `sku, name, description, price, currency, stock, sizes/variants, image_url, keywords/aliases`.

### 11. Can it handle sizes, colors, and variants?
Yes. Variants appear inline as quick buttons (Size S/M/L, Color Red/Blue) so buyers choose in one tap.

### 12. Can MatrixAI reply in different languages?
Yes. It detects the buyer's language and replies in that language (Bangla, English, and mixed Banglish included).

### 13. Can I customize the answers?
Yes. Edit templates for price, availability, shipping, returns, and your own FAQs. You can also set rules for when to escalate to a human.

---

## Selling in Chat

### 14. Can buyers order directly in DMs or WhatsApp?
Yes. Buyers add items, pick a variant, share their address, and choose payment — entirely inside chat.

### 15. What payment options are supported?
Payment links (such as bKash, Nagad) and Cash on Delivery (COD). You choose which options to offer per channel.

### 16. Does MatrixAI send invoices or order updates?
Yes. It sends an invoice/receipt and order status updates (confirmed, shipped, delivered) using compliant message templates, especially on WhatsApp.

---

## Comment-to-DM & Engagement

### 17. Can MatrixAI DM people who comment "price" on my posts?
Yes. Comment-to-DM sends a private message to commenters with predefined templates (price, details) and continues the conversation privately — turning public comments into private sales conversations.

### 18. Is Comment-to-DM safe and compliant?
Yes when configured correctly. It respects platform policies, frequency caps, and opt-outs so your page never gets flagged for spamming.

### 19. Can I schedule follow-ups for buyers who didn't buy?
Yes. Set a polite reminder 6-12 hours after someone asks for price but doesn't checkout. On WhatsApp, proactive messages must use approved templates outside the 24-hour window.

---

## Inbox & Handoff

### 20. Do I get one inbox for Facebook, Instagram, and WhatsApp?
Yes. All conversations appear in a unified inbox with tags, filters, search, and team assignment.

### 21. Can I take over a conversation from the bot?
Anytime. Pause automation on a thread, assign it to a teammate, and resume later — MatrixAI preserves full context (product, cart, notes).

### 22. Can I set quiet hours / after-hours?
Yes. Set schedules where MatrixAI delays non-urgent replies or follow-ups so customers don't get messaged late at night.

---

## Compliance, Privacy & Security

### 23. How does WhatsApp's 24-hour rule affect me?
You can freely reply within 24 hours of the buyer's last message. After 24 hours, business-initiated outreach (order updates, reminders) must use approved template messages.

### 24. How do customers opt out?
Buyers can send keywords like "stop," or use the "Unsubscribe" option. MatrixAI will not send further marketing messages to opted-out contacts.

### 25. Is my data secure?
Yes. Data is encrypted in transit and at rest, with role-based access and audit logs. Only authorized users on your account can view conversations and catalogs.

---

## Troubleshooting & Common Errors

### 26. Instagram or Facebook DMs aren't coming into the inbox. What do I check?
- The Instagram account is Business and linked to the Facebook Page
- Message access is enabled in Instagram settings
- All requested permissions were granted during connection
- The Page/IG account isn't restricted or under review

### 27. WhatsApp messages aren't sending. Why?
Common causes:
- Phone number isn't fully set up on WhatsApp Cloud API/BSP
- Messaging outside the 24-hour window without an approved template
- Rate limits or temporary platform issues
- Template rejected or variables don't match the approved format

### 28. Comment-to-DM isn't triggering on a post. What now?
Ensure MatrixAI has comment permissions, keyword rules match the commenters' text, and the post is mapped to a product or default reply template. Verify the post is on a connected Page/IG account.

### 29. The bot sent the wrong price. How do I fix it?
Update the product in your CSV/Sheet and re-sync. Check the SKU/aliases match what customers type. You can also force a specific product for a specific post.

### 30. How do I pause all automations temporarily?
Use the global Pause/Resume control. Pause by channel (WhatsApp only) or by feature (Comment-to-DM only) without disconnecting accounts.

---

## Plans & Billing

### 31. How is MatrixAI priced?
Three plans, Bangladesh pricing (per month):

| Plan | Monthly fee | Best for |
|------|-------------|----------|
| **Basic 999** | BDT 999 | Small pages — 10,000 conversations, 24/7 support |
| **Basic 2499** | BDT 2,499 | Growing business — unlimited chats + WhatsApp/Telegram |
| **Pro** | BDT 4,999 | Large pages — unlimited everything, 5M tokens, agentic RAG |
| **Enterprise** | Custom | Organizations — dedicated agent, advanced RAG, custom setup |

### 32. Do I have a trial?
Yes. A free demo setup — we connect your page and show the AI live on a sample, then you start a paid plan only when you're ready.

### 33. What payment methods do you accept?
Mobile banking (bKash, Nagad) and major credit/debit cards.

### 34. Can I change or cancel my plan anytime?
Yes. Upgrades take effect immediately; downgrades and cancellations apply at the next billing cycle. Your data remains exportable after cancellation.

### 35. Are refunds available?
We honor refunds for billing errors and where required by law. Other cases depend on usage and plan terms — contact support.

### 36. Does adding more channels cost extra?
Usually yes. Each additional connected channel (extra Facebook Page or WhatsApp number) may have an add-on fee. Ask the sales team for your case.

---

## Data & Support

### 37. Can I export my leads and conversations?
Yes. Export to CSV or sync to Google Sheets/your CRM. You can also push events to your own tools via webhooks.

### 38. What analytics do I get?
Response time, handled vs. escalated, conversion from comments/DMs, top products, and channel breakdown (IG, FB, WhatsApp).

### 39. How do I contact support?
In-app chat, email, and a knowledge base with setup guides and templates. Priority support is available on higher plans.

### 40. What happens during platform outages?
Messages queue and retry automatically. After recovery, pending automations resume and failures are logged so you can review and resend if needed.

---

## Onboarding & Post-Purchase

### 41. What happens after I sign up?
We connect your page (10-30 minutes, we guide you), upload your products via CSV or Google Sheets, the AI learns your prices and policies, and it goes live from the next message. You watch everything in the dashboard — no technical knowledge needed.

### 42. Can I test MatrixAI on my own page before paying?
Yes. The free demo setup connects your page and shows the AI live on real messages. You only start a paid plan when you're ready (see Q32).

### 43. What if my customers don't want to talk to a bot?
Most customers never notice — replies come in your name, in their language, with real product info. And whenever a customer or you wants a human, the conversation hands off to your team with full history.

### 44. Do I need any technical knowledge?
No. Connection, product upload, and setup are guided — we do it together with you. The dashboard is built for non-technical sellers.

---

## For Agents — Answer Bank

| Customer says | You answer |
|----------------|-----------|
| "বট কি বাংলা বুঝবে?" | "জি, বাংলা-ইংরেজি-বাংলিশ সব বুঝে, নিজের ভাষায় উত্তর দেয়।" |
| "কত টাকা?" | "৯৯৯ টাকা থেকে শুরু, মাসে — আপনার ব্যবসার জন্য কোনটা লাগবে, ১০ মিনিটের ডেমোতে দেখাই।" |
| "আমার প্রোডাক্ট অনেক ভ্যারিয়েন্ট" | "সেটাই আমাদের strengths — শবি, আকার, দাম-স্টক সব ঠিকমতো বলে।" |
| "কাস্টমার বটে কথা বলবে না?" | "কাস্টমার জানতে পারবে না এটা বট — আর চাইলে মানুষের হাতে হ্যান্ডওভারও করে।" |
| "আগে trial করা যায়?" | "জি, ১০ মিনিটে demo setup করি — পছন্দ না হলে বাদ।" |
| "পেজ block হবে না তো?" | "আমরা Meta নিয়ম মেনে চলে — compliant by design, block risk নেই।" |
| "কতক্ষণে সেটআপ হবে?" | "১০-৩০ মিনিট — সব আমরা করে দিই, আপনি শুধু পেজের অ্যাডমিন পাস দিবেন।" |
| "ডিসকাউন্ট আছে?" | "একটা অফার আছে — আমি মালিকের কাছে কনফার্ম করে জানাচ্ছি।" |
| "আমার প্রোডাক্ট অনেক ভ্যারিয়েন্ট" | "সেটাই আমাদের strength — সাইজ, রঙ, দাম-স্টক সব ঠিকমতো বলে।" |
| "কোন প্ল্যানটা আমার জন্য?" | "আপনার দিনে কত মেসেজ আসে? — সেই অনুযায়ী ৯৯৯ থেকে শুরু, পরে বাড়ালে আপগ্রেড।" |

> পাইপলাইন, কনভার্সন, ক্যাডেন্স দেখুন: `03_SALES/crm/PIPELINE.md`। প্রোডাক্ট ও দাম: `00_CONTEXT/features_bengali.md` + `00_CONTEXT/pricing.md`।