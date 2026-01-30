from django.shortcuts import render, redirect
from django.core.files.storage import default_storage

from django.db.models import Sum, Count, Q, Avg
from django.contrib.auth.decorators import login_required
from .models import Product, Conversation, Sale, Message, Integration, Package, PackageItem
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

    context = {
        "total_sales": total_sales,
        "total_conversations": total_conversations,
        "conversion_rate": conversion_rate,
        "active_products": active_products,
        "top_products": top_products,
        "orders_count": orders_count,

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

    # 👉 get last message for preview info
    last_msg = (
        Message.objects
        .filter(conversation=convo)
        .order_by("-timestamp")
        .first()
    )

    # ==========================
    # Conversation meta data
    # ==========================

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

        "last_message": last_msg.text if last_msg else "",
        # ✅ local time
        "timestamp": local_created.strftime("%H:%M") if local_created else "",
        "updated_at": local_updated.strftime("%H:%M") if local_updated else "",
    }

    # ==========================
    # Messages query
    # ==========================

    qs = Message.objects.filter(
        conversation=convo
    ).order_by("id")

    if last_msg_id:
        qs = qs.filter(id__gt=last_msg_id)

    messages_data = []

    for msg in qs:
        local_msg_time = timezone.localtime(msg.timestamp) if msg.timestamp else None


        messages_data.append({
            "id": msg.id,
            "sender": msg.sender,
            "text": msg.text,
            # ✅ local timezone
            "timestamp": local_msg_time.strftime("%d %b, %Y %H:%M") if local_msg_time else "",

            "image": msg.attachments.get("payload", {}).get("url") if msg.attachments else None
        })

    # ==========================
    # Final response
    # ==========================

    return JsonResponse({
        "conversation": conversation_data,
        "messages": messages_data
    })

@login_required
def ajax_load_conversations(request):
    platform = request.GET.get("platform", "all")
    q = request.GET.get("q", "").strip()

    convos = Conversation.objects.filter(user=request.user)
    
    if platform != "all":
        convos = convos.filter(platform=platform)
        print("Conversation Platform selected")
        print(platform)

    if q:
        convos = convos.filter(
            Q(customer_name__icontains=q) |
            Q(customer_id__icontains=q)
            
        )
        print("Conversation Query selected")
        print(q)
    
    # convos = convos.order_by("-updated_at")[:50]
    
    convos = convos.annotate(
        sort_time=Coalesce("updated_at", "timestamp")
    ).order_by("-sort_time")[:50]




    data = []

    for c in convos:

         # ✅ convert to local timezone
        local_time = timezone.localtime(c.updated_at) if c.updated_at else None
        
        print(local_time)
        data.append({
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
        integration = user.integrations.filter(platform="messenger").first()
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
        integration = user.integration_set.filter(platform="whatsapp").first()
        if not integration:
            return HttpResponseForbidden("WhatsApp integration not configured.")

        # Assuming that `attachments` contains the image URL
        msg = Message.objects.create(
            conversation=convo,
            sender="agent",
            attachments={"payload": {"url": image_url}}
        )

        url = "https://www.wasenderapi.com/api/send-image"
        headers = {"Authorization": f"Bearer {integration.access_token}"}
        data = {
            "to": convo.customer_id,
            "image": image_url
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

        access_token = user.integrations.filter(platform="messenger").first().access_token
        sender_id = user.integrations.filter(platform="messenger").first().integration_id
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

        access_token = user.integration_set.filter(platform="whatsapp").first().access_token
        sender_id = user.integration_set.filter(platform="whatsapp").first().integration_id
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
        "all_products": all_products
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


@login_required
def settingss(request):
    user = request.user

    # get or create integrations
    integration, created = Integration.objects.get_or_create(user=user,platform="messenger",
                                                             defaults={"is_enabled": False,"is_connected": False,})
    print("Integration:", integration, "Created:", created)

    integration_whatsapp, created = Integration.objects.get_or_create(user=user,platform="whatsapp",
        defaults={
            "is_enabled": False,
            "is_connected": False,
        }
    )
    print("Integration WhatsApp:", integration_whatsapp, "Created:", created)

    if request.method == "POST":
        platform = request.POST.get("platform")
        # Select the correct integration
        if platform == "messenger":
            target = integration
            print("Updating Messenger Integration settings...")


            print(target)
        elif platform == "whatsapp":
            target = integration_whatsapp
            print(target)
        else:
            messages.error(request, "Unknown platform")
            return redirect("back:options")
        
        # Update fields
        target.webhook_url = request.POST.get("webhook_url")
        target.access_token = request.POST.get("access_token")
        target.integration_id = request.POST.get("integration_id")
        target.is_enabled = request.POST.get("is_enabled") == "on"
        target.save()

        messages.success(request, f"{platform.capitalize()} settings updated")
        return redirect("back:options")
    
    # counts for UI
    active_conversations = Conversation.objects.filter(user=user, is_ai_enabled=True).count()
    deactivated_conversations = Conversation.objects.filter(user=user, is_ai_enabled=False).count()
    active_conversations_wp = Conversation.objects.filter(user=user, is_ai_enabled=True, platform="whatsapp").count()
    deactivated_conversations_wp = Conversation.objects.filter(user=user, is_ai_enabled=False, platform="whatsapp").count()

    context = {
        "integration": integration,
        "integration_wp": integration_whatsapp,
        "active_conversations": active_conversations,
        "deactivated_conversations": deactivated_conversations,
        "active_conversations_wp": active_conversations_wp,
        "deactivated_conversations_wp": deactivated_conversations_wp,
    }

    return render(request, "back/options.html", context)

@login_required
def disable_all_bots(request):
    Conversation.objects.filter(user=request.user, is_ai_enabled=True).update(is_ai_enabled=False)
    print("All bots disabled for user:", request.user.username)
    # return JsonResponse({"success": True, "message": "All bots disabled."})
    return redirect("back:options")  # update with your URL name
@login_required


def enable_all_bots(request):
    Conversation.objects.filter(user=request.user, is_ai_enabled=False).update(is_ai_enabled=True)
    print("All bots enabled for user:", request.user.username)
    # return JsonResponse({"success": True, "message": "All bots enabled."})
    return redirect("back:options")  # update with your URL name

@login_required
def add_product(request):
    if request.method == "POST":
        name = request.POST.get("name")
        description = request.POST.get("description")
        price = request.POST.get("price")
        discounted_price = request.POST.get("discounted_price")
        stock_quantity = request.POST.get("stock_quantity")
        upsell_enabled = request.POST.get("upsell_enabled") == "on"
        image = request.FILES.get("image")


        # Create and save product for the logged-in user
        Product.objects.create(
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
        return redirect("back:products")

        

    return render(request, "back/add_product.html", {"user": request.user})


@login_required
def edit_product(request, pk):

    product = get_object_or_404(Product, pk=pk, user=request.user)
    

    if request.method == "GET":
        # print("GET request for product data")
        # Return product data as JSON for prefill
        return JsonResponse({
            "id": product.id,
            "name": product.name,
            "price": float(product.price),
            "discounted_price": float(product.discounted_price) if product.discounted_price else "",
            "stock_quantity": product.stock_quantity,
            "description": product.description,
            "status": product.status,
            "image": product.image.url if product.image and hasattr(product.image, "url") else ""
        })

    elif request.method == "POST":
        # Update product with submitted form data
        product.name = request.POST.get("name")
        product.price = request.POST.get("price")
        product.discounted_price = request.POST.get("discounted_price") or None
        product.stock_quantity = request.POST.get("stock_quantity")
        product.description = request.POST.get("description")
        product.status = request.POST.get("status") == "True"

        if "image" in request.FILES:
            product.image = request.FILES["image"]

        product.save()

        return JsonResponse({"success": True, "message": "Product updated successfully!"})

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