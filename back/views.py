from django.shortcuts import render
from django.contrib.auth.decorators import login_required
# Create your views here.

def dashboard(request):
    return render(request, "back/dashboard.html", {"user": request.user})




login_required()
def c_dashboard(request):
    return render(request, "back/c_dashboard.html", {"user": request.user})

def products(request):
    return render(request, "back/products.html", {"user": request.user})

def stats(request):
    return render(request, "back/stats.html", {"user": request.user})

def sett(request):
    return render(request, "back/options.html", {"user": request.user})


# def pricing(request):
#     return render(request, "front/pricing.html")
