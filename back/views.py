from django.shortcuts import render, redirect
from django.db.models import Sum, Count, Q, Avg
from django.contrib.auth.decorators import login_required
from .models import Product, Conversation, Sale
# Create your views here.

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta

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
def get_dashboard_metrics(request):
    """Return dashboard summary metrics as JSON.

    Metrics returned:
      - avg_order: average completed order amount in the selected range (string formatted)
      - chat_sales: number of completed sales in range (int)
      - response_time: approximate average response time as 'Hh Mm' based on matching sale times (or 'N/A')
      - total_messages: count of Conversation objects in range (int)
      - replied: conversations with non-empty response_text (int)
      - pending: conversations without a response_text (int)

    Notes/assumptions:
      - There is no explicit response timestamp on Conversation; to approximate response time we match
        Conversations to Sales by `customer_id` and compute the delta between conversation.timestamp and
        the earliest Sale.created_at that occurs after the conversation. If no matches found the response_time
        is returned as 'N/A'. This is a pragmatic heuristic given existing models.
    """
    user = request.user
    range_key = request.GET.get("range", "30D")

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

    # Sales-based metrics
    sales = Sale.objects.filter(user=user, created_at__gte=start_date)
    completed_sales_qs = sales.filter(status__iexact="completed")
    chat_sales = completed_sales_qs.count()
    avg_order_val = completed_sales_qs.aggregate(avg=Avg('amount'))['avg'] or 0

    # Conversation-based metrics
    conversations = Conversation.objects.filter(user=user, timestamp__gte=start_date)
    total_messages = conversations.count()
    replied_qs = conversations.filter(response_text__isnull=False).exclude(response_text="")
    replied = replied_qs.count()
    pending = total_messages - replied

    # Approximate response time by matching conversations to sales (by customer_id)
    response_deltas = []
    # iterate over replied conversations and try to find a sale occurring after the conversation timestamp
    for conv in replied_qs:
        # find earliest sale by the same customer that occurred after the conversation
        sale = (
            Sale.objects.filter(user=user, customer_id=conv.customer_id, created_at__gte=conv.timestamp)
            .order_by('created_at')
            .first()
        )
        if sale:
            delta = sale.created_at - conv.timestamp
            if delta.total_seconds() >= 0:
                response_deltas.append(delta)

    if response_deltas:
        avg_seconds = sum(d.total_seconds() for d in response_deltas) / len(response_deltas)
        hours = int(avg_seconds // 3600)
        minutes = int((avg_seconds % 3600) // 60)
        response_time_str = f"{hours}h {minutes}m"
    else:
        response_time_str = "N/A"

    # Format average order to two decimals with currency-like formatting
    try:
        avg_order_formatted = f"{float(avg_order_val):.2f}"
    except Exception:
        avg_order_formatted = str(avg_order_val)

    return JsonResponse({
        "avg_order": avg_order_formatted,
        "chat_sales": chat_sales,
        "response_time": response_time_str,
        "total_messages": total_messages,
        "replied": replied,
        "pending": pending,
    })


@login_required
def orders(request):
    all_orders = Sale.objects.filter(user=request.user).select_related('product').order_by('-created_at')
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
    all_convo = Conversation.objects.filter(user=request.user)
    
    context = {
        "all_convo": all_convo,
        "user": request.user,
    }

    return render(request, "back/c_dashboard.html", context)

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
    return render(request, "back/options.html", {"user": request.user})

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
                    "image": Product.image.url if Product.image else "",
                },
            })

        # Otherwise, handle normal form submit
        return redirect("back:products")

        

    return render(request, "back/add_product.html", {"user": request.user})


@login_required
def edit_product(request, pk):

    product = get_object_or_404(Product, pk=pk, user=request.user)
    print("Editing product:", product)

    if request.method == "GET":
        # Return product data as JSON for prefill
        return JsonResponse({
            "id": product.id,
            "name": product.name,
            "price": float(product.price),
            "discounted_price": float(product.discounted_price) if product.discounted_price else "",
            "stock_quantity": product.stock_quantity,
            "description": product.description,
            "status": product.status,
            "image": product.image.url if product.image else "",
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

