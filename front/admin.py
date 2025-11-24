# core/admin.py
from django.contrib import admin
from .models import Contact, Survay

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "business", "created_at")
    search_fields = ("name", "email", "business")
    list_filter = ("created_at",)

@admin.register(Survay)
class SurvayAdmin(admin.ModelAdmin):
    list_display = ("name", "business_name", "business_type", "customer_range", "email", "submitted_at")
    search_fields = ("name", "business_name", "email")
    list_filter = ("business_type", "customer_range", "submitted_at")