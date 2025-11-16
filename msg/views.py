from django.shortcuts import render
from django.http import HttpResponse
# Create your views here.
def message_list(request):
    return HttpResponse("✅ Thank you! We’ll contact you soon.Message Section is working.")
    # return render(request, "messages/message_list.html", {})