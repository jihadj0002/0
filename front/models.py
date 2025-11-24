from django.db import models

# Create your models here.
# core/models.py
from django.db import models

class Contact(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    business = models.CharField(max_length=150, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.email})"




class Survay(models.Model):
    BUSINESS_TYPES = [
        ("ecommerce", "E-commerce"),
        ("service", "Service Business"),
        ("local_shop", "Local Shop"),
        ("b2b", "Wholesale / B2B"),
        ("personal_brand", "Personal Brand"),
        ("education", "Education"),
        ("health_beauty", "Health & Beauty"),
        ("saas", "Startup / SaaS"),
        ("corporate", "Corporate"),
        ("other", "Other"),
    ]

    CUSTOMER_RANGES = [
        ("1-5", "1-5"),
        ("5-10", "5-10"),
        ("10-50", "10-50"),
        ("50-100", "50-100"),
        ("100-500", "100-500"),
        ("500-1000", "500-1000"),
    ]

    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    business_name = models.CharField(max_length=200)
    business_type = models.CharField(max_length=50, choices=BUSINESS_TYPES)
    customer_range = models.CharField(max_length=20, choices=CUSTOMER_RANGES)
    email = models.EmailField()
    social_page = models.CharField(max_length=255, blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} — {self.business_name}"
