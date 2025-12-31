from django.shortcuts import render, redirect
from django.db.models import Sum, Count, Q, Avg
from django.contrib.auth.decorators import login_required
from .models import Product, Conversation, Sale, Message, Integration
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
    return render(request, 'back/orders.html', {'all_orders': all_orders})


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
@require_POST
def send_message_ajax(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
        convo_id = data.get('conversation_id')
        text = data.get('text', '').strip()
    except Exception:
        return HttpResponseBadRequest("Invalid payload")

    if not convo_id or not text:
        return HttpResponseBadRequest("Missing conversation id or text")

    convo = get_object_or_404(Conversation, id=convo_id, user=request.user)

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
        "sent_text": msg.text,
        "sent_ts": timezone.localtime(msg.timestamp).strftime("%-d %b, %Y %H:%M"),
    }

    # If AI is enabled, simulate an immediate bot reply (replace with real AI call)
    if convo.is_ai_enabled:
        bot_text = f"Auto-reply: Received '{text[:200]}'"
        bot_msg = Message.objects.create(
            conversation=convo,
            sender='bot',
            text=bot_text,
        )
        response_data.update({
            "bot_reply_html": bot_msg.text,
            "bot_reply_ts": timezone.localtime(bot_msg.timestamp).strftime("%-d %b, %Y %H:%M"),
        })
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
def stats(request):
    return render(request, "back/stats.html", {"user": request.user})

@login_required
def sett(request):
    user = request.user

    integration, created = Integration.objects.get_or_create(
        user=user,
        platform="messenger",
        defaults={
            "is_enabled": False,
            "is_connected": False,
        }
    )

    active_conversations = Conversation.objects.filter(user=user, is_ai_enabled=True).count()
    deactivated_conversations = Conversation.objects.filter(user=user, is_ai_enabled=False).count()

    if request.method == "POST":
        integration.webhook_url = request.POST.get("webhook_url")
        integration.access_token = request.POST.get("access_token")
        integration.is_enabled = request.POST.get("is_enabled") == "on"
        integration.save()

        return redirect("back:options")  # update with your URL name

    context = {
        "integration": integration,
        "active_conversations": active_conversations,
        "deactivated_conversations": deactivated_conversations,
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