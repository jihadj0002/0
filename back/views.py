from django.shortcuts import render
from django.contrib.auth.decorators import login_required
# Create your views here.


@login_required
def dashboard(request):
    print(request.user)
    return render(request, "back/dashboard.html", {"user": request.user})

@login_required
def c_dashboard(request):
    return render(request, "back/c_dashboard.html", {"user": request.user})

@login_required
def products(request):
    return render(request, "back/products.html", {"user": request.user})

@login_required
def stats(request):
    return render(request, "back/stats.html", {"user": request.user})

@login_required
def sett(request):
    return render(request, "back/options.html", {"user": request.user})


# def pricing(request):
#     return render(request, "front/pricing.html")
