from django.shortcuts import render, redirect
from django.db.models import Sum, Count, Q
from django.contrib.auth.decorators import login_required
from .models import Product, Conversation, Sale
# Create your views here.


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
    }
    return render(request, "back/dashboard.html", context)


@login_required
def orders(request):
    all_orders = Sale.objects.filter(user=request.user).select_related('product').order_by('-created_at')
    return render(request, 'back/orders.html', {'all_orders': all_orders})


@login_required
def c_dashboard(request):
    return render(request, "back/c_dashboard.html", {"user": request.user})

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

        # Create and save product for the logged-in user
        Product.objects.create(
            user=request.user,
            name=name,
            description=description,
            price=price,
            discounted_price=discounted_price if discounted_price else None,
            stock_quantity=stock_quantity,
            upsell_enabled=upsell_enabled,
        )

        return redirect("back:products")  # or wherever you want to go after saving

    return render(request, "back/add_product.html", {"user": request.user})


# def pricing(request):
#     return render(request, "front/pricing.html")
