from rest_framework import serializers
from back.models import UserProfile, Product, Conversation, Sale, Setting, ProductImages, Message, OrderItem
from django.db import transaction



class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = '__all__'

class ProductImagesSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImages
        fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    product_images = ProductImagesSerializer(source='productimages_set', many=True, read_only=True)

    class Meta:
        model = Product
        fields = '__all__'
    
    def get_image(self, obj):
        request = self.context.get('request')
        if obj.image:
            return request.build_absolute_uri(obj.image.url) if request else obj.image.url
        return ""

class ConversationSerializer(serializers.ModelSerializer):

    def to_internal_value(self, data):
        data = data.copy()

        for key, value in data.items():
            if value == "":
                data[key] = None

        return super().to_internal_value(data)

    class Meta:
        model = Conversation
        fields = '__all__'

class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = '__all__'

class SaleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sale
        fields = "__all__"

    def validate(self, data):
        product = data.get("product")
        quantity = data.get("quantity", 1)

        if product and product.stock_quantity < quantity:
            raise serializers.ValidationError("Not enough stock")

        return data

    @transaction.atomic
    def create(self, validated_data):
        product = validated_data.get("product")
        quantity = validated_data.get("quantity", 1)

        sale = super().create(validated_data)

        if product:
            product.stock_quantity -= quantity
            product.save(update_fields=["stock_quantity"])

        return sale
    
class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = "__all__"

    def validate(self, data):
        product = data["product"]
        quantity = data["quantity"]

        if product.stock_quantity < quantity:
            raise serializers.ValidationError("Not enough stock")

        return data


class SettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Setting
        fields = '__all__'







# [
# {
# "response": 
# [
# {
# "id": 
# 5,
# "name": 
# "Basic Bot",
# "description": 
# "10,000 Monthly Replies\r\n3M AI Model Tokens/Month\r\nUnlimited WhatsApp Service Messages\r\nUnlimited Telegram Conversations\r\n24/7 Basic Support\r\nMeta API Integration Support\r\nWeb App Integration Support\r\nBasic Website Crawling Support\r\nEmail Support\r\nBasic RAG Database Support",
# "price": 
# "1499.00",
# "discounted_price": 
# "999.00",
# "stock_quantity": 
# 100,
# "upsell_enabled": 
# true,
# "last_synced": 
# "2025-10-29T20:14:06.445461Z",
# "user": 
# 5
# }
# ]
# }
# ]