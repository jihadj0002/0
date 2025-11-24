from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm
import datetime
from django.http import HttpResponse
from .models import Contact
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.core.mail import send_mail  # optional if you want email
from .models import Survay

# Create your views here.
def home(request):
    context = {
        "year": datetime.date.today().year,
        "matrixai_logo_url": f"{settings.MEDIA_URL}images/matrixai.png"
    }
    return render(request, "front/home01.html", context)



def pricing(request):
    return render(request, "front/pricing.html")

def privacy_policy(request):
    return render(request, "front/p_policy.html")

def forumm(request):
    if request.method == "POST":
        
        name = request.POST.get("name")
        phone = request.POST.get("phone")
        business_name = request.POST.get("business_name")
        business_type = request.POST.get("business_type")
        customer_range = request.POST.get("customer_range")
        email = request.POST.get("email")
        social_page = request.POST.get("social_page")

        Survay.objects.create(
            name=name,
            phone=phone,
            business_name=business_name,
            business_type=business_type,
            customer_range=customer_range,
            email=email,
            social_page=social_page,
        )

        return redirect('front:home')

    return render(request, "front/forum.html")

def contact(request):
    if request.method == "POST":
        name = request.POST.get("name")
        email = request.POST.get("email")
        business = request.POST.get("business")

        # 🔹 Option 1: Just print (debugging)
        #print(f"New Contact → {name}, {email}, {business}")

        # 🔹 Option 2: Save into DB (make a model)
        Contact.objects.create(name=name, email=email, business=business)

        # 🔹 Option 3: Send email notification
        # send_mail(
        #     f"AI Agent Demo Request from {name}",
        #     f"Business: {business}\nEmail: {email}",
        #     "your@email.com",
        #     ["yourother@email.com"],
        # )

        return HttpResponse("✅ Thank you! We’ll contact you soon.")
    
    return HttpResponse("❌ Invalid request.")



# login_required()
# def dashboard(request):
#     return render(request, "front/dashboard.html", {"user": request.user})


# def c_dashboard(request):
#     return render(request, "front/c_dashboard.html", {"user": request.user})

# def products(request):
#     return render(request, "front/products.html", {"user": request.user})

# def stats(request):
#     return render(request, "front/stats.html", {"user": request.user})

# def sett(request):
#     return render(request, "front/options.html", {"user": request.user})



def login_view(request):
    print("Login open")
    
    if request.user.is_authenticated:
        return redirect('back:dashboard')  # Redirect if already logged in
    
    # Always get next URL from GET parameter
    next_url = request.GET.get('next', '')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        # print(form)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                print("Login successful")
                login(request, user)
                messages.success(request, f"Welcome back, {username}!")
                
                # Redirect to next_url if provided, else dashboard
                if next_url:
                    return redirect(next_url)
                return redirect('back:dashboard')
            else:
                messages.error(request, "Invalid username or password.")
                print("Login failed")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
    
    return render(request, 'front/login.html', {
        'form': form,
        'next': next_url
    })


def signup(request):
    if request.method == "POST":
        try:
            username = request.POST.get('username')
            password = request.POST.get('password')
            user_obj = User.objects.filter(username=username)
            
            if user_obj.exists():
                messages.error(request, "Username is taken")
                return redirect("front:signup")
            user = User.objects.create(username=username) #Here using password=password dosent works because thats not clean data I guess. 
            user.set_password(password)
            
            user.save()
            messages.success(request, "User Created Successfully")
            
        except Exception as e:
            messages.error(request, "Smething Went Wrong")
    
    return render(request, "front/sign-up.html")


def logout_view(request):
    logout(request)
    messages.success(request, "You have been successfully logged out.")
    return redirect('front:home')