from types import SimpleNamespace
from django.shortcuts import render, redirect
from django.core.files.storage import default_storage
from django.contrib.auth.decorators import login_required, user_passes_test

from django.db.models import Sum, Count, Q, Avg
from .models import Product, Conversation, Sale, Message, Integration, Package, PackageItem, ProductSource, SupportTicket
from django.views.decorators.http import require_GET
# Create your views here.
from django.db.models.functions import TruncDay

from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
import json, csv, requests
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from django.core.paginator import Paginator
from django.utils.dateparse import parse_datetime
from django.db.models.functions import Coalesce

@login_required
def dashboard(request):
    user = request.user
    
    total_sales = (
        Sale.objects.filter(user=user, status="completed")
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )

    completed_sales = Sale.objects.filter(user=user, status="completed").count()
    total_conversations = Conversation.objects.filter(user=user).count()
    # print("Total Conversations:", total_conversations)
    active_products = Product.objects.filter(user=user, stock_quantity__gt=0).count()

    orders_count = Sale.objects.filter(user=user).count()

    active_productss = Product.objects.filter(user=user, stock_quantity__gt=0)
    top_products = active_productss.order_by('-stock_quantity')[:3]
    
    conversion_rate = (
        round((completed_sales / total_conversations) * 100, 2)
        if total_conversations > 0
        else 0
    )

    try:
        from billing.models import UserBalance
        balance = UserBalance.objects.select_related('plan').get(user=user)
    except Exception:
        balance = None

    context = {
        "total_sales": total_sales,
        "total_conversations": total_conversations,
        "conversion_rate": conversion_rate,
        "active_products": active_products,
        "top_products": top_products,
        "orders_count": orders_count,
        "balance": balance,
    }
    return render(request, "back/dashboard.html", context)

# Dashboard Analytics View
@login_required
def get_order_analytics(request):
    user = request.user
    range_key = request.GET.get("range", "30D")
    # print(range_key)

    now = timezone.now()
    if range_key == "1D":
        start_date = now - timedelta(days=1)
    elif range_key == "7D":
        start_date = now - timedelta(days=7)
    elif range_key == "30D":
        start_date = now - timedelta(days=30)
    elif range_key == "6M":
        start_date = now - timedelta(days=180)
    else:
        start_date = now - timedelta(days=30)

    orders = Sale.objects.filter(user=user, created_at__gte=start_date)
    # print("Printing ")
    # print(orders)

    total_orders = orders.count()
    completed_orders = orders.filter(status="completed").count()
    pending_orders = orders.filter(status="pending").count()

    # For the small chart, simulate trend data (you can later replace this with real grouping)
    chart_data = list(range(0, total_orders if total_orders < 12 else 12))
    print(chart_data)

    return JsonResponse({
        "total_orders": total_orders,
        "completed": completed_orders,
        "pending": pending_orders,
        "chart_data": chart_data,
    })


# Dashboard Sales & Revenue Analytics View
@login_required
def get_sales_analytics(request):
    user = request.user
    range_key = request.GET.get("range", "30D")
    # print(range_key)

    now = timezone.now()
    if range_key == "1D":
        start_date = now - timedelta(days=1)
    elif range_key == "7D":
        start_date = now - timedelta(days=7)
    elif range_key == "30D":
        start_date = now - timedelta(days=30)
    elif range_key == "6M":
        start_date = now - timedelta(days=180)
    else:
        start_date = now - timedelta(days=30)

    orders = Sale.objects.filter(user=user, created_at__gte=start_date)
    # print("Printing ")
    # print(orders)

    total_orders = orders.count()
    completed_orders = orders.filter(status="completed").count()
    pending_orders = orders.filter(status="pending").count()

    # For the small chart, simulate trend data (you can later replace this with real grouping)
    chart_data = list(range(0, total_orders if total_orders < 12 else 12))
    print(chart_data)

    return JsonResponse({
        "total_orders": total_orders,
        "completed": completed_orders,
        "pending": pending_orders,
        "chart_data": chart_data,
    })


@login_required

def get_chat_metrics(request):
    user = request.user
    range_key = request.GET.get("range", "30D")

    now = timezone.now()
    ranges = {
        "1D": timedelta(days=1),
        "7D": timedelta(days=7),
        "30D": timedelta(days=30),
        "6M": timedelta(days=180),
    }
    start_date = now - ranges.get(range_key, timedelta(days=30))

    # -------------------------------------------------
    # TOTAL CONVERSATIONS (ALL, USER-BASED)
    # -------------------------------------------------
    total_conversations = Conversation.objects.filter(user=user).count()

    # -------------------------------------------------
    # TOTAL SENT MESSAGES (ALL CONVERSATIONS)
    # -------------------------------------------------
    total_messages = Message.objects.filter(
        conversation__user=user, sender='bot', timestamp__gte=start_date
    ).count()

    # -------------------------------------------------
    # AVERAGE MESSAGES PER CONVERSATION
    # -------------------------------------------------
    average_messages = round(
        total_messages / total_conversations, 1
    ) if total_conversations else 0

    # -------------------------------------------------
    # CHART DATA (MESSAGES PER DAY IN RANGE)
    # -------------------------------------------------
    chart_qs = (
        Message.objects.filter(
            conversation__user=user,
            timestamp__gte=start_date
        )
        .annotate(day=TruncDay("timestamp"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )

    chart_data = [row["count"] for row in chart_qs]

    return JsonResponse({
        "total_conversations": total_conversations,
        "replied_messages": total_messages,  # rename in frontend if you want
        "average_messages": average_messages,
        "chart_data": chart_data,
    })

@login_required
def orders(request):
    all_orders = (
        Sale.objects
        .filter(user=request.user)
        .order_by('-created_at')
    )
    # conversations = Conversation.objects.filter(user=request.user)
    # convo_map = {c.customer_id: c.id for c in conversations}

    context = {
        'all_orders': all_orders, 
        
    }

    return render(request, 'back/orders.html', context)


@csrf_exempt  # because we manually include CSRF token in fetch()
def update_order_status(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            order_id = data.get("order_id")
            new_status = data.get("status")

            order = Sale.objects.get(id=order_id, user=request.user)
            order.status = new_status
            order.save()

            return JsonResponse({"success": True, "status": new_status})
        except Sale.DoesNotExist:
            return JsonResponse({"success": False, "error": "Order not found"})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request"})


@login_required
def c_dashboard_demo(request):
    # ==========================
    # DEMO CONVERSATIONS
    # ==========================

    return render(request, "back/c_dashboard_demo.html")


@login_required
def c_dashboard(request):
    all_convo = Conversation.objects.filter(user=request.user).order_by('-timestamp')

    # Get selected conversation ID from URL query (?cid=123)
    convo_id = request.GET.get("cid")
    selected_convo = None
    messages = None

    paginator = Paginator(all_convo, 30)  # 30 chats per page
    page_number = request.GET.get("page", 1)
    all_convo = paginator.get_page(page_number)

    if convo_id:
        selected_convo = get_object_or_404(
            Conversation, id=convo_id, user=request.user
        )
        messages = selected_convo.messages.all().order_by("timestamp")

    context = {
        "user": request.user,
        "all_convo": all_convo,
        "selected_convo": selected_convo,
        "messages": messages,
    }

    return render(request, "back/c_dashboard.html", context)


@login_required
def message_dashboard(request):
    return render(request, "back/ajax_c_dashboard.html")


@login_required
def tickets_view(request):
    return render(request, "back/tickets.html")


@login_required
def ajax_tickets(request):
    status_filter = request.GET.get("status", "all")
    qs = SupportTicket.objects.select_related("conversation", "assigned_to")
    if request.user.is_staff:
        pass
    else:
        qs = qs.filter(
            Q(conversation__user=request.user) |
            Q(assigned_to=request.user)
        )
    if status_filter != "all":
        qs = qs.filter(status=status_filter)
    data = []
    for t in qs:
        data.append({
            "id": t.pk,
            "subject": t.subject,
            "description": t.description,
            "status": t.status,
            "priority": t.priority,
            "assigned_to": t.assigned_to.username if t.assigned_to else None,
            "customer_name": t.conversation.customer_name or t.conversation.customer_id,
            "customer_id": t.conversation.customer_id,
            "platform": t.conversation.platform,
            "conversation_id": t.conversation_id,
            "created_at": timezone.localtime(t.created_at).strftime("%d %b %H:%M") if t.created_at else "",
            "resolved_at": timezone.localtime(t.resolved_at).strftime("%d %b %H:%M") if t.resolved_at else None,
        })
    return JsonResponse({"tickets": data})


@login_required
@require_POST
def ajax_ticket_claim(request):
    ticket_id = request.POST.get("ticket_id")
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if ticket.assigned_to and ticket.assigned_to != request.user:
        return JsonResponse({"error": "Already assigned to someone else"}, status=400)
    ticket.assigned_to = request.user
    if ticket.status == "open":
        ticket.status = "in_progress"
    ticket.save(update_fields=["assigned_to", "status"])
    return JsonResponse({"status": ticket.status, "assigned_to": request.user.username})


@login_required
@require_POST
def ajax_ticket_resolve(request):
    ticket_id = request.POST.get("ticket_id")
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    if ticket.assigned_to and ticket.assigned_to != request.user:
        return JsonResponse({"error": "Not assigned to you"}, status=400)
    ticket.resolve()
    return JsonResponse({"status": "resolved"})


@login_required
@require_POST
def ajax_ticket_reopen(request):
    ticket_id = request.POST.get("ticket_id")
    ticket = get_object_or_404(SupportTicket, pk=ticket_id)
    ticket.status = "open"
    ticket.resolved_at = None
    ticket.save(update_fields=["status", "resolved_at"])
    return JsonResponse({"status": "open"})


@login_required
@require_POST
def bot_preview(request):
    """Dry-run the AI pipeline for a given conversation + message. No platform send, no credit deduction."""
    import json as _json
    from api.ai.context import build_system_prompt, get_conversation_history
    from api.ai.providers import call_llm
    from api.ai.tools import TOOL_DEFINITIONS, execute_tool

    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    conv_id = body.get("conversation_id")
    message_text = (body.get("message") or "").strip()

    if not conv_id or not message_text:
        return JsonResponse({"error": "conversation_id and message are required"}, status=400)

    conversation = get_object_or_404(Conversation, id=conv_id, user=request.user)
    user = request.user

    integration = Integration.get_active(user, conversation.platform)
    model = (integration.ai_model or None) if integration else None

    system_prompt = build_system_prompt(user, conversation)
    history = get_conversation_history(conversation, limit=20)
    history.append({"role": "user", "content": message_text})
    messages_list = [{"role": "system", "content": system_prompt}] + history

    tool_calls_log = []
    final_text = None
    total_input = 0
    total_output = 0
    error = None

    try:
        for _ in range(5):
            llm_msg, usage = call_llm(messages=messages_list, tools=TOOL_DEFINITIONS, model=model)
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)

            if not llm_msg.tool_calls:
                final_text = llm_msg.content or ""
                break

            messages_list.append(llm_msg)

            for tc in llm_msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = _json.loads(tc.function.arguments or "{}")
                except Exception:
                    fn_args = {}

                tc_result = execute_tool(fn_name, fn_args, user, conversation)
                tool_calls_log.append({"name": fn_name, "args": fn_args})

                messages_list.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _json.dumps(tc_result, default=str),
                })

                if fn_name == "create_ticket":
                    final_text = "I'm connecting you with a human agent now."
                    break

            if final_text:
                break

    except Exception as exc:
        error = str(exc)

    return JsonResponse({
        "response": final_text or "",
        "tool_calls": tool_calls_log,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "model": model or "gpt-4o-mini",
        "error": error,
    })








@login_required
@require_GET
def ajax_load_messages(request):
    convo_id = request.GET.get("cid")
    last_msg_id = request.GET.get("last_id")
    username = request.user.username

    if not convo_id:
        return JsonResponse({"messages": []})

    convo = get_object_or_404(
        Conversation,
        id=convo_id,
        user=request.user
    )

    local_created = timezone.localtime(convo.timestamp) if convo.timestamp else None
    local_updated = timezone.localtime(convo.updated_at) if convo.updated_at else None

    conversation_data = {
        "id": convo.id,
        "username": username,
        "customer_id": convo.customer_id,
        "platform": convo.platform,
        "name": convo.customer_name or f"ID: {convo.id}",
        "profile_image": (
            convo.profile_image.url
            if convo.profile_image and hasattr(convo.profile_image, "url")
            else None
        ),
        "chat_summary": convo.chat_summary,
        "is_ai_enabled": convo.is_ai_enabled,
        "last_message": convo.message_text or "",
        "timestamp": local_created.strftime("%H:%M") if local_created else "",
        "updated_at": local_updated.strftime("%H:%M") if local_updated else "",
    }

    # ==========================
    # Messages query
    # ==========================

    qs = Message.objects.filter(conversation=convo).defer("raw_payload")

    if last_msg_id:
        qs = qs.filter(id__gt=last_msg_id).order_by("id")
    else:
        qs = qs.order_by("-id")[:50]
        qs = list(qs)
        qs.reverse()

    messages_data = []

    for msg in qs:
        local_msg_time = timezone.localtime(msg.timestamp) if msg.timestamp else None

        attachment = None
        if msg.attachments:
            if isinstance(msg.attachments, dict):
                att = msg.attachments
                att_type = att.get("type")
                if not att_type:
                    if att.get("cards"):
                        att_type = "product_cards" if len(att["cards"]) > 1 else "product_card"
                    elif att.get("images"):
                        att_type = "image"
                attachment = {
                    "type": att_type,
                    "images": att.get("images"),
                    "cards": att.get("cards"),
                    "url": (
                        att["payload"].get("url") if isinstance(att.get("payload"), dict) else None
                        or (att.get("images") or [None])[0]
                        or att.get("url")
                    ),
                    "payload": att.get("payload") if isinstance(att.get("payload"), dict) else None,
                }
            elif isinstance(msg.attachments, str):
                attachment = {"type": "image", "url": msg.attachments}

        messages_data.append({
            "id": msg.id,
            "sender": msg.sender,
            "text": msg.text,
            "timestamp": local_msg_time.strftime("%d %b, %Y %H:%M") if local_msg_time else "",
            "attachment": attachment,
        })

    # ==========================
    # Final response
    # ==========================

    return JsonResponse({
        "conversation": conversation_data,
        "messages": messages_data,
    })

@login_required
def ajax_load_conversations(request):
    platform = request.GET.get("platform", "all")
    q = request.GET.get("q", "").strip()

    convos = Conversation.objects.filter(user=request.user).only(
        "id", "customer_name", "customer_id", "platform", "message_text",
        "updated_at", "timestamp", "profile_image",
    )
    
    if platform != "all":
        convos = convos.filter(platform=platform)

    if q:
        convos = convos.filter(
            Q(customer_name__icontains=q) |
            Q(customer_id__icontains=q)
        )
    
    # convos = convos.order_by("-updated_at")[:50]
    
    convos = convos.annotate(
        sort_time=Coalesce("updated_at", "timestamp")
    ).order_by("-sort_time")[:50]




    data = []

    for c in convos:

         # ✅ convert to local timezone
        local_time = timezone.localtime(c.updated_at) if c.updated_at else None
        
        # print(local_time)
        data.append({

            # "image": msg.attachments.get("payload", {}).get("url") if msg.attachments else None
            "profile_image": (c.profile_image.url if c.profile_image and hasattr(c.profile_image, "url")
                else None
            ),
            "id": c.id,
            "customer_name": c.customer_name,
            "customer_id": c.customer_id,
            "platform": c.platform,
            "last_message": c.message_text  or "New message",
            # send formatted local time
            "updated_at": local_time.strftime("%H:%M") if local_time else "",
            # send Unformatted Global time
            # raw time for sorting
            "updated_at_raw": timezone.localtime(c.sort_time).isoformat(),
        })


    return JsonResponse({"conversations": data})


@login_required
@require_POST
def send_image_ajax(request):
    user = request.user
    print(f"User: {user}")

    # Get conversation_id from POST data
    convo_id = request.POST.get("conversation_id")
    print(f"Conversation ID: {convo_id}")
    
    # Get the image file from request
    image = request.FILES.get("image")
    print(f"Image: {image}")

    if not convo_id or not image:
        return HttpResponseBadRequest("Missing conversation id or image")

    # Get the conversation object
    convo = get_object_or_404(Conversation, id=convo_id, user=user)

    # =======================
    # Save Image to Cloudflare R2
    # =======================
    
    # Save image using the default storage backend (Cloudflare R2)
    file_name = f"{timezone.now().strftime('%Y%m%d%H%M%S')}_{image.name}"
    file_path = default_storage.save(f"media/{file_name}", ContentFile(image.read()))
    
    # Generate the public URL for the image
    image_url = default_storage.url(file_path)
    print(f"Image URL: {image_url}")
    
    # =======================
    # Save message to database
    # =======================
    # Save message with the image attachment URL
    msg = Message.objects.create(
        conversation=convo,
        sender="agent",
        attachments={"payload": {"url": image_url}}  # Save image URL as an attachment field
    )
    print("Message Created")

    # =======================
    # Facebook Messenger Integration
    # =======================
    if convo.platform == "messenger":
        integration = Integration.get_active(user, "messenger")
        if not integration:
            return HttpResponseForbidden("Messenger integration not configured.")

        access_token = integration.access_token
        sender_id = integration.integration_id

        payload = {
            "recipient": {"id": convo.customer_id},
            "message": {
                "attachment": {
                    "type": "image",
                    "payload": {
                        "url": image_url,
                        "is_reusable": True
                    }
                }
            }
        }

        # Send the image to the customer on Facebook Messenger
        url = f"https://graph.facebook.com/v24.0/{sender_id}/messages"
        params = {"access_token": access_token}
        print(f"Messenger API URL: {url}")
        
        response = requests.post(url, params=params, json=payload)
        print(response)

        if response.status_code != 200:
            return JsonResponse({
                "status": "error",
                "message": "Failed to send image via Messenger API."
            }, status=500)
        
        # Extract the message_id from the response
        data = response.json()
        message_id = data.get("message_id")
        print(f"Message ID from Facebook: {message_id}")

        # Update the message with the received message_id
        msg.mid = message_id
        msg.save()

    # =======================
    # WhatsApp Integration
    # =======================
    elif convo.platform == "whatsapp":
        integration = Integration.get_active(user, "whatsapp")
        if not integration:
            return HttpResponseForbidden("WhatsApp integration not configured.")

        # Assuming that `attachments` contains the image URL
        msg = Message.objects.create(
            conversation=convo,
            sender="agent",
            attachments={"payload": {"url": image_url}}
        )

        url = "https://www.wasenderapi.com/api/send-message"
        headers = {"Authorization": f"Bearer {integration.access_token}"}
        data = {
            "to": convo.customer_id,
            "imageUrl": image_url
        }

        response = requests.post(url, headers=headers, json=data)

        if response.status_code != 200:
            return JsonResponse({
                "status": "error",
                "message": "Failed to send image via WhatsApp API."
            }, status=500)
        
        # Extract the message_id from the response
        data = response.json()
        message_id = data.get("message_id")
        print(f"Message ID from Whatsapp: {message_id}")

        # Update the message with the received message_id
        msg.mid = message_id
        msg.save()

    return JsonResponse({
        "status": "ok",
        "id": msg.id,
        "image_url": image_url,
        "sent_ts": timezone.localtime(msg.timestamp).strftime("%d %b, %Y %H:%M"),
        "message_id": msg.mid,  # Return the saved message_id
        "attachments": msg.attachments  # Include the full attachments object
    })


@login_required
@require_POST
def send_message_with_image_ajax(request):
    user = request.user

    # convo_id = request.POST.get('conversation_id')
    # text = request.POST.get('text', '').strip()
    # image = request.FILES.get('image')

    # if not convo_id or (not text and not image):
    #     return HttpResponseBadRequest("Missing conversation id or text/image")

    # convo = get_object_or_404(Conversation, id=convo_id, user=request.user)

    # # Here you would add code to send the message with image via the appropriate API
    # # For simplicity, we'll skip that part and just create the message in our DB

    # msg = Message.objects.create(
    #     conversation=convo,
    #     sender='agent',
    #     text=text,
    #     image=image
    # )

    # convo.message_text = text if text else "Image"
    # convo.save()

    # response_data = {
    #     "status": "ok",
    #     "sent_text": msg.text,
    #     "sent_ts": timezone.localtime(msg.timestamp).strftime(f"%d %b, %Y %H:%M"),
    #     "image_url": msg.image.url if msg.image else "",
    # }

    # return JsonResponse(response_data)
    return JsonResponse({"status": "error", "message": "Image sending not implemented yet."}, status=501)


@login_required
@require_POST
def send_message_ajax(request):
    user = request.user


    try:
        data = json.loads(request.body.decode('utf-8'))
        convo_id = data.get('conversation_id')
        text = data.get('text', '').strip()

    except Exception:
        return HttpResponseBadRequest("Invalid payload")

    if not convo_id or not text:
        return HttpResponseBadRequest("Missing conversation id or text")

    
    convo = get_object_or_404(Conversation, id=convo_id, user=request.user)
    
    if convo.platform == "messenger":

        integration = Integration.get_active(user, "messenger")
        access_token = integration.access_token if integration else None
        sender_id = integration.integration_id if integration else None
        if not access_token and sender_id:
            return HttpResponseForbidden("Messenger integration or Sender ID not configured.")
        else:
            url = f"https://graph.facebook.com/v23.0/{sender_id}/messages"
            params = {
                "access_token": access_token,
            }
            payload = {
                "recipient": {"id": convo.customer_id},
                "message": {"text": text},
            }
            response = requests.post(url, params=params, json=payload)
            if response.status_code != 200:
                return JsonResponse({
                    "status": "error",
                    "message": "Failed to send message via Messenger API."
                }, status=500)
    
    if convo.platform == "whatsapp":

        integration = Integration.get_active(user, "whatsapp")
        access_token = integration.access_token if integration else None
        sender_id = integration.integration_id if integration else None
        if not access_token and sender_id:
            return HttpResponseForbidden("WhatsApp integration not configured.")
        else:
            url = "https://www.wasenderapi.com/api/send-message"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }

            data = {
                "to": f"{convo.customer_id}",
                "text": text,
            }
            response = requests.post(url, headers=headers, json=data)
            if response.status_code != 200:
                return JsonResponse({
                    "status": "error",
                    "message": "Failed to send message via WhatsApp API."
                }, status=500)
    
    

    # Create customer message
    msg = Message.objects.create(
        conversation=convo,
        sender='agent',   # if messages created by the logged-in agent; change to 'customer' if appropriate
        text=text,
    )

    # Update conversation last message_text for preview
    convo.message_text = text
    convo.save()
    
    response_data = {
        "status": "ok",
        "id": msg.id,
        "text": msg.text,
        "sent_ts": timezone.localtime(msg.timestamp).strftime(f"%d %b, %Y %H:%M"),
        
    }

    # If AI is enabled, simulate an immediate bot reply (replace with real AI call)
    # if convo.is_ai_enabled:
    #     bot_text = f"Auto-reply: Received '{text[:200]}'"
    #     bot_msg = Message.objects.create(
    #         conversation=convo,
    #         sender='bot',
    #         text=bot_text,
    #     )
    #     response_data.update({
    #         "bot_reply_html": bot_msg.text,
    #         "bot_reply_ts": timezone.localtime(msg.timestamp).strftime(f"%d %b, %Y %H:%M"),
    #     })
    return JsonResponse(response_data)



@login_required
def products(request):
    # Get only the products owned by the logged-in user
    all_products = Product.objects.filter(user=request.user).order_by('-last_synced')

    context = {
        "user": request.user,
        "all_products": all_products,
        "active_source": ProductSource.get_active_for(request.user),
    }
    return render(request, "back/products.html", context)


@login_required
def packages(request):
    # Get only the packages owned by the logged-in user
    all_packages = Package.objects.filter(user=request.user).order_by('-created_at')
    print("Packages:", all_packages)
    context = {
        "user": request.user,
        "all_packages": all_packages
    }
    return render(request, "back/packages.html", context)




@login_required
def add_package(request):
    if request.method == "POST":
        name = request.POST.get("name")
        description = request.POST.get("description")
        price = request.POST.get("price")
        discounted_price = request.POST.get("discounted_price")
        stock_quantity = request.POST.get("stock_quantity")
        upsell_enabled = request.POST.get("upsell_enabled") == "on"
        image = request.FILES.get("image")


        # Create and save product for the logged-in user
        Package.objects.create(
            user=request.user,
            name=name,
            description=description,
            price=price,
            discounted_price=discounted_price if discounted_price else None,
            stock_quantity=stock_quantity,
            upsell_enabled=upsell_enabled,
            image=image if image else "product.jpg",
        )

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "success": True,
                "product": {
                    "id": Product.id,
                    "name": Product.name,
                    "price": str(Product.price),
                    "image": Product.image.url if Product.image and hasattr(Product.image, "url") else "",
                },
            })

        # Otherwise, handle normal form submit
        return redirect("back:packages")

        

    return render(request, "back/add_product.html", {"user": request.user})


@login_required
def edit_package(request, pk):

    package = get_object_or_404(Package, pk=pk, user=request.user)
    

    if request.method == "GET":
        # print("GET request for product data")
        # Return product data as JSON for prefill
        return JsonResponse({
            "id": package.id,
            "name": package.name,
            "price": float(package.price),
            "discounted_price": float(package.discounted_price) if package.discounted_price else "",
            "stock_quantity": package.stock_quantity,
            "description": package.description,
            "status": package.status,
            "image": package.image.url if package.image and hasattr(package.image, "url") else ""
        })

    elif request.method == "POST":
        # Update product with submitted form data
        package.name = request.POST.get("name")
        package.price = request.POST.get("price")
        package.discounted_price = request.POST.get("discounted_price") or None
        package.stock_quantity = request.POST.get("stock_quantity")
        package.description = request.POST.get("description")
        package.status = request.POST.get("status") == "True"

        if "image" in request.FILES:
            package.image = request.FILES["image"]

        package.save()

        return JsonResponse({"success": True, "message": "Package updated successfully!"})

    return JsonResponse({"error": "Invalid request"}, status=400)


@csrf_exempt
def delete_package(request, pk):
    if request.method == "DELETE":
        try:
            package = get_object_or_404(Package, pk=pk)
            package.delete()
            return JsonResponse({"success": True, "message": "Package deleted successfully."}) 
        except Package.DoesNotExist:
            return JsonResponse({"success": False, "message": "Package not found."}, status=404)
    return JsonResponse({"success": False, "message": "Invalid request method."}, status=400)


@login_required
def stats(request):
    return render(request, "back/stats.html", {"user": request.user})

@login_required
def sett(request):
    user = request.user
    total_conversations = Conversation.objects.filter(user=user).count()
    print("Total Conversations:", total_conversations)
    integration, created = Integration.objects.get_or_create(
        user=user,
        platform="messenger",
        defaults={
            "is_enabled": False,
            "is_connected": False,
        }
    )
    print("Integration:", integration, "Created:", created)



    integration_whapsapp, created = Integration.objects.get_or_create(
        user=user,
        platform="whatsapp",
        defaults={
            "is_enabled": False,
            "is_connected": False,
        }
    )
    print("Integration WhatsApp:", integration_whapsapp, "Created:", created)





    active_conversations = Conversation.objects.filter(user=user, is_ai_enabled=True).count()
    deactivated_conversations = Conversation.objects.filter(user=user, is_ai_enabled=False).count()

    active_conversations_wp = Conversation.objects.filter(user=user, is_ai_enabled=True, platform="whatsapp").count()
    deactivated_conversations_wp = Conversation.objects.filter(user=user, is_ai_enabled=False, platform="whatsapp").count()
    print("Active WhatsApp Conversations:", active_conversations_wp)
    print("Deactivated WhatsApp Conversations:", deactivated_conversations_wp)

    if request.method == "POST":
        print("Received POST data to update integrations.")
        try:
            print("Updating Messenger Integration settings...")
            integration.webhook_url = request.POST.get("webhook_url")
            print("Webhook URL:", integration.webhook_url)
            integration.access_token = request.POST.get("access_token")
            integration.integration_id = request.POST.get("integration_id")
            print("Integration ID:", integration.integration_id)
            integration.is_enabled = request.POST.get("is_enabled") == "on"
            print("Is Messenger Bot Enabled:", integration.is_enabled)
            integration.save()
            print("Messenger Integration updated successfully.")
        
        except Exception as e:
            messages.error(request, f"Error updating Messenger integration: {e}")
        return redirect("back:options")  # update with your URL name
    
    if request.method == "POST":
        print("Received POST data to update integrations.")
        try:
            
            integration_whapsapp.webhook_url = request.POST.get("webhook_url_wp")
            integration_whapsapp.access_token = request.POST.get("access_token_wp")
            integration_whapsapp.integration_id = request.POST.get("sender_number_wp")
            integration_whapsapp.is_enabled = request.POST.get("is_enabled_wp") == "on"
            print("Is WhatsApp Bot Enabled:", integration_whapsapp.is_enabled)
            integration_whapsapp.save()
            print("WhatsApp Integration updated successfully.")
        
        except Exception as e:
            messages.error(request, f"Error updating Messenger integration: {e}")
        return redirect("back:options")  # update with your URL name
    
    
   

    context = {
        "integration": integration,
        "active_conversations": active_conversations,
        "deactivated_conversations": deactivated_conversations,

        "integration_wp": integration_whapsapp,
        "active_conversations_wp": active_conversations_wp,
        "deactivated_conversations_wp": deactivated_conversations_wp,
        "total_conversations": total_conversations,
    }

    return render(request, "back/options.html", context)

def get_whatsapp_username(api_key):
    if not api_key:
        return None

    try:
        response = requests.get(
            "https://www.wasenderapi.com/api/user",
            headers={
                "Authorization": f"Bearer {api_key}"
            },
            timeout=10
        )

        print("WhatsApp API Response:", response.status_code, response.text)

        if response.status_code == 200:
            payload = response.json()

            # Expected structure:
            # {
            #   "success": true,
            #   "data": {
            #     "id": "...",
            #     "name": "...",
            #     "lid": "..."
            #   }
            # }

            if payload.get("success") and payload.get("data"):
                return payload["data"].get("name")

    except requests.RequestException as e:
        print("WhatsApp API error:", e)

    return None


def get_messenger_username(access_token):
    if not access_token:
        return None

    try:
        response = requests.get(
            "https://graph.facebook.com/v18.0/me",
            params={
                "fields": "name",
                "access_token": access_token
            },
            timeout=10
        )
        print("Messenger API Response:", response.status_code, response.text)

        if response.status_code == 200:
            data = response.json()
            return data.get("name")
    except requests.RequestException:
        pass

    return None


@login_required
def settingss(request):
    import secrets
    user = request.user
    username = user.username

    PLATFORMS = ["messenger", "whatsapp", "instagram", "telegram"]

    # Ensure all 4 integrations exist; auto-generate webhook URL + verify token
    base = request.build_absolute_uri(f"/api/{username}/webhook/")
    integrations = {}
    for platform in PLATFORMS:
        obj, _ = Integration.objects.get_or_create(
            user=user, platform=platform,
            defaults={"is_enabled": False, "is_connected": False},
        )
        # Always keep webhook_url fresh (in case domain changes)
        obj.webhook_url = f"{base}{platform}/"
        # Auto-generate a verify token once (needed for Meta hub verification)
        if not obj.verify_token:
            obj.verify_token = secrets.token_urlsafe(24)
        obj.save(update_fields=["webhook_url", "verify_token"])
        integrations[platform] = obj

    if request.method == "POST":
        platform = request.POST.get("platform")
        if platform not in PLATFORMS:
            messages.error(request, "Unknown platform.")
            return redirect("back:options")

        target = integrations[platform]
        target.access_token  = request.POST.get("access_token", "").strip() or None
        target.app_secret    = request.POST.get("app_secret", "").strip() or None
        target.integration_id = request.POST.get("integration_id", "").strip() or None
        target.is_enabled    = request.POST.get("is_enabled") == "on"
        target.save(update_fields=["access_token", "app_secret", "integration_id", "is_enabled"])
        messages.success(request, f"{platform.capitalize()} settings saved.")
        return redirect("back:options")

    active_conversations = Conversation.objects.filter(user=user, is_ai_enabled=True).count()
    deactivated_conversations = Conversation.objects.filter(user=user, is_ai_enabled=False).count()

    return render(request, "back/options.html", {
        "integrations": integrations,
        "active_conversations": active_conversations,
        "deactivated_conversations": deactivated_conversations,
    })

@login_required
def disable_all_bots(request):
    Conversation.objects.filter(user=request.user, is_ai_enabled=True).update(is_ai_enabled=False)
    print("All bots disabled for user:", request.user.username)
    # return JsonResponse({"success": True, "message": "All bots disabled."})
    return redirect("back:options")  # update with your URL name
@login_required


def enable_all_bots(request):
    platforms = Integration.objects.filter(
        user=request.user, is_enabled=True
    ).values_list("platform", flat=True)
    Conversation.objects.filter(
        user=request.user, platform__in=platforms
    ).update(is_ai_enabled=True)

@login_required
def add_product(request):
    if request.method == "POST":
        from .models import ProductImages
        product = Product.objects.create(
            user=request.user,
            name=request.POST.get("name"),
            description=request.POST.get("description"),
            price=request.POST.get("price"),
            discounted_price=request.POST.get("discounted_price") or None,
            stock_quantity=request.POST.get("stock_quantity"),
            upsell_enabled=request.POST.get("upsell_enabled") == "true",
            featured_product=request.POST.get("featured_product") == "true",
            image=request.FILES.get("image") or "product.jpg",
        )
        for f in request.FILES.getlist("gallery_images"):
            ProductImages.objects.create(product=product, images=f)

        return JsonResponse({"success": True, "redirect": True})

    return render(request, "back/add_product.html", {"user": request.user})


@login_required
def edit_product(request, pk):

    product = get_object_or_404(Product, pk=pk, user=request.user)
    

    if request.method == "GET":
        from .models import ProductImages as ProdImgs
        gallery = [
            {"id": img.id, "url": img.images.url}
            for img in ProdImgs.objects.filter(product=product)
            if img.images and hasattr(img.images, "url")
        ]
        return JsonResponse({
            "id": product.id,
            "pid": product.pid,
            "name": product.name,
            "price": float(product.price),
            "discounted_price": float(product.discounted_price) if product.discounted_price else "",
            "stock_quantity": product.stock_quantity,
            "description": product.description or "",
            "status": product.status,
            "featured_product": product.featured_product,
            "upsell_enabled": product.upsell_enabled,
            "image": product.image.url if product.image and hasattr(product.image, "url") else "",
            "gallery": gallery,
        })

    elif request.method == "POST":
        product.name = request.POST.get("name")
        product.price = request.POST.get("price")
        product.discounted_price = request.POST.get("discounted_price") or None
        product.stock_quantity = request.POST.get("stock_quantity")
        product.description = request.POST.get("description")
        product.status = request.POST.get("status") == "True"
        product.featured_product = request.POST.get("featured_product") == "true"
        product.upsell_enabled = request.POST.get("upsell_enabled") == "true"

        if "image" in request.FILES:
            product.image = request.FILES["image"]

        product.save()

        # Add new gallery images if provided
        from .models import ProductImages as ProdImgs
        for f in request.FILES.getlist("gallery_images"):
            ProdImgs.objects.create(product=product, images=f)

        return JsonResponse({
            "success": True,
            "message": "Product updated successfully!",
            "image": product.image.url if product.image and hasattr(product.image, "url") else "",
        })

    return JsonResponse({"error": "Invalid request"}, status=400)


@csrf_exempt
def delete_product(request, pk):
    if request.method == "DELETE":
        try:
            product = get_object_or_404(Product, pk=pk)
            product.delete()
            return JsonResponse({"success": True, "message": "Product deleted successfully."}) 
        except Product.DoesNotExist:
            return JsonResponse({"success": False, "message": "Product not found."}, status=404)
    return JsonResponse({"success": False, "message": "Invalid request method."}, status=400)


@login_required
@require_POST
def delete_gallery_image(request, pk, img_pk):
    from .models import ProductImages
    img = get_object_or_404(ProductImages, pk=img_pk, product__pk=pk, product__user=request.user)
    img.delete()
    return JsonResponse({"success": True})


# ══════════════════════════════════════════
#  PRODUCT SOURCES (Multi-Source Architecture)
# ══════════════════════════════════════════

def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _apply_source_credentials(src, request):
    """
    Set decrypted credential PROPERTIES from POST data, scoped to the provider.
    Only re-sets a property when a non-empty value was submitted, so editing
    with a blank field keeps the previously stored secret.
    """
    provider = src.provider
    get = request.POST.get

    def set_if_present(attr, key):
        val = get(key)
        if val:  # non-empty only
            setattr(src, attr, val)

    if provider == "woocommerce":
        set_if_present("consumer_key", "consumer_key")
        set_if_present("consumer_secret", "consumer_secret")
    elif provider == "shopify":
        set_if_present("access_token", "access_token")
        set_if_present("api_key", "api_key")
    elif provider == "external":
        set_if_present("access_token", "access_token")


@login_required
def product_sources(request):
    sources = ProductSource.objects.filter(user=request.user).order_by("-is_active", "-created_at")
    context = {
        "user": request.user,
        "sources": sources,
        "provider_choices": ProductSource.PROVIDER_CHOICES,
        "mode_choices": ProductSource.MODE_CHOICES,
    }
    return render(request, "back/product_sources.html", context)


@login_required
@require_POST
def add_product_source(request):
    provider = request.POST.get("provider") or "internal"
    src = ProductSource(
        user=request.user,
        provider=provider,
        name=request.POST.get("name") or "",
        store_url=request.POST.get("store_url") or None,
        mode=request.POST.get("mode") or "sync",
    )
    if provider == "external":
        src.order_endpoint_url = request.POST.get("order_endpoint_url") or None

    _apply_source_credentials(src, request)
    src.save()

    if _is_ajax(request):
        return JsonResponse({"success": True, "sid": src.sid})
    messages.success(request, "Product source connected.")
    return redirect("back:product_sources")


@login_required
@require_POST
def edit_product_source(request, sid):
    src = get_object_or_404(ProductSource, sid=sid, user=request.user)

    if request.POST.get("provider"):
        src.provider = request.POST.get("provider")
    if "name" in request.POST:
        src.name = request.POST.get("name") or ""
    if "store_url" in request.POST:
        src.store_url = request.POST.get("store_url") or None
    if request.POST.get("mode"):
        src.mode = request.POST.get("mode")
    if src.provider == "external" and "order_endpoint_url" in request.POST:
        src.order_endpoint_url = request.POST.get("order_endpoint_url") or None

    _apply_source_credentials(src, request)
    src.save()

    if _is_ajax(request):
        return JsonResponse({"success": True, "sid": src.sid})
    messages.success(request, "Product source updated.")
    return redirect("back:product_sources")


@login_required
@require_POST
def delete_product_source(request, sid):
    src = get_object_or_404(ProductSource, sid=sid, user=request.user)
    src.delete()
    if _is_ajax(request):
        return JsonResponse({"success": True})
    messages.success(request, "Product source removed.")
    return redirect("back:product_sources")


@login_required
@require_POST
def activate_product_source(request, sid):
    src = get_object_or_404(ProductSource, sid=sid, user=request.user)
    src.is_active = True
    src.save()  # model auto-demotes other sources
    if _is_ajax(request):
        return JsonResponse({"success": True, "sid": src.sid})
    messages.success(request, "Active product source updated.")
    return redirect("back:product_sources")


@login_required
@require_POST
def test_product_source(request, sid):
    src = get_object_or_404(ProductSource, sid=sid, user=request.user)
    from api.products.factory import get_provider_for_source

    try:
        provider = get_provider_for_source(src, request.user)
        result = provider.test_connection()
    except Exception as e:
        result = {"ok": False, "message": str(e)}

    ok = bool(result.get("ok"))
    src.status = "connected" if ok else "error"
    src.last_error = "" if ok else (result.get("message") or "Connection failed")
    src.save(update_fields=["status", "last_error", "updated_at"])

    return JsonResponse({
        "ok": ok,
        "message": result.get("message", ""),
        "status": src.status,
    })


@login_required
@require_POST
def sync_product_source(request, sid):
    src = get_object_or_404(ProductSource, sid=sid, user=request.user)
    from api.products.sync import sync_products

    try:
        result = sync_products(src)
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=400)

    return JsonResponse({
        "success": True,
        "created": result.get("created", 0),
        "updated": result.get("updated", 0),
        "errors": result.get("errors", 0),
    })


# Export Products as CSV
@login_required
def export_products(request):
    # Fetch products for current user
    products = Product.objects.filter(user=request.user)

    # Prepare CSV file
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="products_export.csv"'

    writer = csv.writer(response)

    # CSV Header
    writer.writerow([
        "Name", "Description", "Price",
        "Discounted Price", "Stock Quantity",
        "Status", "Image URL"
    ])

    # CSV Rows
    for p in products:
        writer.writerow([
            p.name,
            p.description,
            p.price,
            p.discounted_price or "",
            p.stock_quantity,
            "Active" if p.status else "Inactive",
            p.image.url if p.image and hasattr(p.image, "url") else "",
        ])

    return response


# Import Products from CSV

@login_required
def import_products(request):
    if request.method == "POST":
        file = request.FILES.get("file")

        if not file:
            messages.error(request, "Please upload a CSV file.")
            return redirect("import_products")

        try:
            decoded_file = file.read().decode("utf-8").splitlines()
            reader = csv.DictReader(decoded_file)
        except:
            messages.error(request, "Invalid CSV format.")
            return redirect("import_products")

        imported = 0
        skipped = 0

        for row in reader:
            name = row.get("name")
            if not name:
                skipped += 1
                continue

            # Prevent duplicate product names for the same user
            if Product.objects.filter(user=request.user, name=name).exists():
                skipped += 1
                continue

            product = Product(
                user=request.user,
                name=name,
                description=row.get("description") or "",
                price=row.get("price") or 0,
                discounted_price=row.get("discounted_price") or None,
                stock_quantity=int(row.get("stock_quantity") or 0),
                status=row.get("status", "").lower() == "true",
            )

            # Handle image downloading from URL
            image_url = row.get("image")
            if image_url:
                try:
                    r = requests.get(image_url)
                    if r.status_code == 200:
                        file_name = image_url.split("/")[-1]
                        product.image.save(file_name, ContentFile(r.content), save=False)
                except:
                    pass

            product.save()
            imported += 1

        messages.success(request, f"Imported: {imported}, Skipped: {skipped}")
        return redirect("back:products")

    return render(request, "back/import_products.html")

# =====================================================================
# Settings — Store, Agent Identity, Behavior Rules, AI Model
# =====================================================================
@login_required
def settings_view(request):
    from context.models import AgentIdentity, StoreConfig, BehaviorRules
    from billing.models import ModelPricing

    user = request.user
    identity, _ = AgentIdentity.objects.get_or_create(user=user)
    store, _ = StoreConfig.objects.get_or_create(user=user)
    rules, _ = BehaviorRules.objects.get_or_create(user=user)
    integrations = list(Integration.objects.filter(user=user))
    available_models = list(ModelPricing.objects.filter(is_active=True).values_list('model_id', flat=True))

    if request.method == 'POST':
        section = request.POST.get('section', '')

        if section == 'store':
            store.store_name = request.POST.get('store_name', '')
            store.address = request.POST.get('address', '')
            store.whatsapp_number = request.POST.get('whatsapp_number', '')
            store.delivery_charge_inside = request.POST.get('delivery_charge_inside') or 0
            store.delivery_charge_outside = request.POST.get('delivery_charge_outside') or 0
            store.support_open_time = request.POST.get('support_open_time') or '09:00'
            store.support_close_time = request.POST.get('support_close_time') or '21:00'
            store.timezone = request.POST.get('timezone') or 'Asia/Dhaka'
            store.currency = request.POST.get('currency') or 'BDT'
            store.save()
            messages.success(request, 'Store settings saved.')

        elif section == 'agent':
            identity.name = request.POST.get('name') or 'Assistant'
            identity.role = request.POST.get('role', '')
            identity.tone = request.POST.get('tone') or 'friendly'
            identity.style = request.POST.get('style') or 'conversational'
            identity.language = request.POST.get('language') or 'en'
            if 'image' in request.FILES:
                identity.image = request.FILES['image']
            identity.save()
            messages.success(request, 'Agent identity saved.')

        elif section == 'behavior':
            rules.greeting_message = request.POST.get('greeting_message', '')
            rules.custom_instructions = request.POST.get('custom_instructions', '')
            rules.chit_chat_enabled = 'chit_chat_enabled' in request.POST
            rules.chit_chat_style = request.POST.get('chit_chat_style') or 'moderate'
            rules.cross_sell_enabled = 'cross_sell_enabled' in request.POST
            rules.ask_open_ended = 'ask_open_ended' in request.POST
            rules.sample_questions_answers = request.POST.get('sample_questions_answers', '').strip()
            rules.save()
            messages.success(request, 'Behavior rules saved.')

        elif section == 'knowledge':
            rules.knowledge_base = request.POST.get('knowledge_base', '').strip()
            rules.save(update_fields=['knowledge_base'])
            messages.success(request, 'Knowledge base saved.')

        elif section == 'ai_model':
            for intg in integrations:
                intg.ai_model = request.POST.get(f'ai_model_{intg.pk}') or None
                intg.save(update_fields=['ai_model'])
            messages.success(request, 'AI model settings saved.')

        return redirect(f"{request.path}?tab={request.POST.get('section', 'store')}")

    timezones = [
        'Asia/Dhaka', 'Asia/Kolkata', 'Asia/Karachi', 'Asia/Dubai',
        'Asia/Singapore', 'Asia/Bangkok', 'Asia/Jakarta',
        'UTC', 'Europe/London', 'Europe/Paris', 'Europe/Berlin',
        'America/New_York', 'America/Chicago', 'America/Los_Angeles',
    ]

    return render(request, 'back/settings.html', {
        'identity': identity,
        'store': store,
        'rules': rules,
        'integrations': integrations,
        'available_models': available_models,
        'active_tab': request.GET.get('tab', 'store'),
        'timezones': timezones,
    })


# =====================================================================
# Billing Dashboard
# =====================================================================
@login_required
def billing_dashboard(request):
    from billing.models import UserBalance, UsageSummary, CreditTransaction, Plan
    from django.db.models import Sum

    user = request.user
    today = timezone.now().date()

    try:
        balance = UserBalance.objects.select_related('plan').get(user=user)
    except UserBalance.DoesNotExist:
        balance = None

    today_summary = UsageSummary.objects.filter(user=user, date=today).first()

    week_map = {
        s.date: s
        for s in UsageSummary.objects.filter(user=user, date__gte=today - timedelta(days=6))
    }
    chart_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        s = week_map.get(d)
        chart_data.append({
            'date': d.strftime('%d %b'),
            'credits': float(s.total_credits_used) if s else 0,
            'replies': s.total_replies if s else 0,
        })

    month_totals = UsageSummary.objects.filter(
        user=user, date__gte=today.replace(day=1)
    ).aggregate(
        replies=Sum('total_replies'),
        ai_calls=Sum('total_ai_calls'),
        tokens_in=Sum('total_input_tokens'),
        tokens_out=Sum('total_output_tokens'),
        credits=Sum('total_credits_used'),
    )

    recent_txns = list(CreditTransaction.objects.filter(user=user)[:15])
    plans = list(Plan.objects.filter(is_active=True).order_by('price_per_month'))

    return render(request, 'back/billing.html', {
        'balance': balance,
        'today_summary': today_summary,
        'chart_data': json.dumps(chart_data),
        'recent_txns': recent_txns,
        'plans': plans,
        'month_totals': month_totals,
    })


@login_required
@user_passes_test(lambda u: u.is_staff)
def ai_debug(request):
    """AI Debug Interface for testing and analyzing AI behavior"""
    from django.contrib.auth.models import User
    from context.models import AgentIdentity, StoreConfig, BehaviorRules
    from api.ai.context import build_system_prompt, get_conversation_history
    from api.ai.tools import TOOL_DEFINITIONS, execute_tool
    from api.ai.providers import call_llm
    from back.models import ToolCallLog, UsageLog
    from billing.models import CreditTransaction
    from collections import defaultdict
    import json
    import time
    import uuid
    
    # Initialize context variables
    users = User.objects.all().order_by('username')
    selected_user = None
    selected_conversation = None
    conversations = []
    system_prompt = ""
    conversation_history = []
    debug_info = {
        'tool_calls': [],
        'token_usage': {'input': 0, 'output': 0},
        'processing_time': 0,
        'model_used': '',
        'error': None
    }
    tool_filter = request.POST.get('tool_name') or request.GET.get('tool_name') or ''
    reply_filter = request.POST.get('reply_id') or request.GET.get('reply_id') or ''
    error_only = (request.POST.get('error_only') or request.GET.get('error_only')) == '1'
    
    # Handle form submissions
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'select_user':
            user_id = request.POST.get('user_id')
            if user_id:
                selected_user = get_object_or_404(User, id=user_id)
                conversations = Conversation.objects.filter(user=selected_user).order_by('-updated_at')
                selected_conversation = None  # Reset conversation selection when user changes
                
        elif action == 'select_conversation':
            conversation_id = request.POST.get('conversation_id')
            if conversation_id:
                selected_conversation = get_object_or_404(Conversation, id=conversation_id)
                # Verify user owns this conversation or is staff
                if request.user.is_staff or selected_conversation.user == request.user:
                    pass  # Valid selection
                else:
                    selected_conversation = None
                    
        elif action == 'send_message':
            conversation_id = request.POST.get('conversation_id')
            message_text = request.POST.get('message_text', '').strip()
            
            if conversation_id and message_text:
                try:
                    selected_conversation = get_object_or_404(Conversation, id=conversation_id)
                    # Verify access
                    if not (request.user.is_staff or selected_conversation.user == request.user):
                        selected_conversation = None
                    else:
                        # Process the message through AI pipeline with debug info
                        start_time = time.time()
                        
                        # Reset debug info
                        debug_info = {
                            'tool_calls': [],
                            'token_usage': {'input': 0, 'output': 0},
                            'processing_time': 0,
                            'model_used': '',
                            'error': None
                        }
                        
                        # Build system prompt
                        system_prompt = build_system_prompt(selected_conversation.user, selected_conversation)
                        
                        # Get conversation history
                        history = get_conversation_history(selected_conversation, limit=20)
                        
                        # Add current user message
                        history.append({"role": "user", "content": message_text})
                        messages_list = [{"role": "system", "content": system_prompt}] + history
                        
                        # Get model from integration
                        integration = selected_conversation.user.integrations.filter(
                            platform=selected_conversation.platform
                        ).first()
                        model = integration.ai_model if integration else None
                        
                        reply_id = uuid.uuid4().hex
                        final_text = None
                        pending_images = []
                        transferred = False
                        
                        # Tool call iterations
                        for iteration in range(5):  # MAX_TOOL_ITERATIONS
                            try:
                                llm_msg, usage = call_llm(messages=messages_list, tools=TOOL_DEFINITIONS, model=model)
                                debug_info['token_usage']['input'] += usage.get("input_tokens", 0)
                                debug_info['token_usage']['output'] += usage.get("output_tokens", 0)
                                
                                if not llm_msg.tool_calls:
                                    final_text = llm_msg.content or ""
                                    break
                                
                                messages_list.append(llm_msg)
                                
                                # Execute tool calls
                                for tc in llm_msg.tool_calls:
                                    fn_name = tc.function.name
                                    try:
                                        fn_args = json.loads(tc.function.arguments or "{}")
                                    except (json.JSONDecodeError, TypeError):
                                        fn_args = {}
                                    
                                    tool_start = time.time()
                                    result = execute_tool(fn_name, fn_args, selected_conversation.user, selected_conversation)
                                    tool_end = time.time()
                                    
                                    # Log to ToolCallLog
                                    try:
                                        ToolCallLog.objects.create(
                                            conversation=selected_conversation,
                                            user=selected_conversation.user,
                                            reply_id=reply_id,
                                            iteration=iteration,
                                            tool_name=fn_name,
                                            arguments=fn_args,
                                            result_summary=str(result).strip()[:500] if result else "",
                                            execution_time_ms=int((tool_end - tool_start) * 1000),
                                        )
                                    except Exception:
                                        pass
                                    
                                    debug_info['tool_calls'].append({
                                        'name': fn_name,
                                        'args': fn_args,
                                        'result': result,
                                        'is_error': isinstance(result, dict) and bool(result.get('error')),
                                        'execution_time': tool_end - tool_start,
                                        'timestamp': time.time()
                                    })
                                    
                                    if fn_name == "send_images" and isinstance(result, dict):
                                        pending_images.extend(result.get("images", []))
                                    
                                    if fn_name == "create_ticket":
                                        transferred = True
                                    
                                    messages_list.append({
                                        "role": "tool",
                                        "tool_call_id": tc.id,
                                        "content": json.dumps(result),
                                    })
                                
                                if transferred:
                                    final_text = "I'm connecting you with a human agent now. Please wait a moment."
                                    break
                                    
                            except Exception as exc:
                                debug_info['error'] = str(exc)
                                break
                        
                        debug_info['processing_time'] = time.time() - start_time
                        debug_info['model_used'] = model or "gpt-4o-mini"
                        
                        # Save bot response if successful
                        if final_text and not debug_info['error']:
                            # Deduplicate images
                            seen = set()
                            unique_images = []
                            for img in pending_images:
                                if img not in seen:
                                    seen.add(img)
                                    unique_images.append(img)
                                    if len(unique_images) == 5:
                                        break
                            
                            # Save to database
                            Message.objects.create(
                                conversation=selected_conversation,
                                sender="bot",
                                text=final_text,
                                attachments={"images": unique_images} if unique_images else None,
                            )
                            
                            # Add bot message to history for display
                            history.append({"role": "assistant", "content": final_text})
                            
                except Exception as exc:
                    debug_info['error'] = str(exc)
    
    # Set defaults for GET requests or after processing
    if not selected_user and users.exists():
        selected_user = users.first()
        conversations = Conversation.objects.filter(user=selected_user).order_by('-updated_at')
    
    # Build system prompt and history for display
    tool_call_logs = []
    pipeline_runs = []
    if selected_conversation:
        system_prompt = build_system_prompt(selected_conversation.user, selected_conversation)
        conversation_history = get_conversation_history(selected_conversation, limit=50)
        tool_qs = ToolCallLog.objects.filter(conversation=selected_conversation)
        if tool_filter:
            tool_qs = tool_qs.filter(tool_name=tool_filter)
        if reply_filter:
            tool_qs = tool_qs.filter(reply_id__icontains=reply_filter)
        if error_only:
            tool_qs = tool_qs.filter(result_summary__icontains="error")

        tool_call_logs = list(tool_qs.order_by("reply_id", "iteration", "timestamp")[:200])

        reply_ids = sorted({t.reply_id for t in tool_call_logs if t.reply_id})
        usage_by_reply = defaultdict(lambda: {"input": 0, "output": 0, "models": set()})
        if reply_ids:
            for ul in UsageLog.objects.filter(user=selected_conversation.user, reply_id__in=reply_ids):
                usage_by_reply[ul.reply_id]["input"] += ul.input_tokens or 0
                usage_by_reply[ul.reply_id]["output"] += ul.output_tokens or 0
                if ul.model:
                    usage_by_reply[ul.reply_id]["models"].add(ul.model)

        credit_by_reply = {}
        if reply_ids:
            for tx in CreditTransaction.objects.filter(
                user=selected_conversation.user,
                reply_id__in=reply_ids,
                transaction_type="deduction",
            ):
                credit_by_reply[tx.reply_id] = tx.amount

        runs_map = defaultdict(list)
        for t in tool_call_logs:
            runs_map[t.reply_id].append({
                "tool_name": t.tool_name,
                "iteration": t.iteration,
                "timestamp": t.timestamp,
                "execution_time_ms": t.execution_time_ms,
                "arguments": t.arguments,
                "result_summary": t.result_summary,
                "is_error": "error" in (t.result_summary or "").lower(),
            })

        pipeline_runs = []
        for rid, items in runs_map.items():
            usage = usage_by_reply.get(rid, {"input": 0, "output": 0, "models": set()})
            pipeline_runs.append({
                "reply_id": rid,
                "tool_calls": items,
                "usage_input": usage["input"],
                "usage_output": usage["output"],
                "models": sorted(usage["models"]),
                "credit_cost": credit_by_reply.get(rid),
            })
    
    context = {
        'users': users,
        'selected_user': selected_user,
        'conversations': conversations,
        'selected_conversation': selected_conversation,
        'system_prompt': system_prompt,
        'conversation_history': conversation_history,
        'debug_info': debug_info,
        'tool_call_logs': tool_call_logs,
        'pipeline_runs': pipeline_runs,
        'tool_filter': tool_filter,
        'reply_filter': reply_filter,
        'error_only': error_only,
    }
    
    return render(request, 'back/ai_debug.html', context)
