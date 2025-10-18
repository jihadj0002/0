# core/admin.py
from django.contrib import admin
from .models import Contact

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "business", "created_at")
    search_fields = ("name", "email", "business")
    list_filter = ("created_at",)