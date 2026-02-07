import json
from rest_framework import serializers
from back.models import Package, PackageImages, UserProfile, Product, Conversation, Sale, Setting, ProductImages, Message, OrderItem
from django.db import transaction
from .utils.files import download_to_storage


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = '__all__'

class ProductImagesSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImages
        fields = '__all__'

class PackageImagesSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackageImages
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
    
class PackageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    product_images = PackageImagesSerializer(source='packageimages_set', many=True, read_only=True)

    class Meta:
        model = Package
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
        read_only_fields = ("user",)

        
class MessageSerializer(serializers.ModelSerializer):

    def create(self, validated_data):
        conversation = validated_data["conversation"]
        attachments = validated_data.get("attachments")

        if (
            conversation.platform == "whatsapp"
            and attachments
            and isinstance(attachments, dict)
        ):
            payload = attachments.get("payload", {})
            url = payload.get("url")

            if url:
                try:
                    public_url = download_to_storage(
                        url,
                        folder="whatsapp_media"
                    )

                    payload["url"] = public_url
                    attachments["stored"] = True

                except Exception as e:
                    attachments["download_error"] = str(e)

            attachments["payload"] = payload
            validated_data["attachments"] = attachments

        return super().create(validated_data)

    class Meta:
        model = Message
        fields = "__all__"

    
class MessageMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "sender",
            "text"
        ]

class ConversationSummarySerializer(serializers.ModelSerializer):
    conversation = serializers.SerializerMethodField()
    last_order = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "customer_id",

            "chat_summary",
            "current_product",
            "current_package",
            "is_ai_enabled",

            "last_order",
            "extra_data",
            "conversation",
            "detected_intent",
            "updated_at",
        ]

    def get_conversation(self, obj):
        messages = (
            obj.messages
            .order_by("-timestamp")[:10]
        )
        messages = reversed(messages)  # oldest → newest

        conversation_text = "\n".join(
                    f"{msg.sender.capitalize()}: {msg.text}"
                    for msg in messages
                )
        return conversation_text

# ADD LAST ORDER IF NEEDED
    def get_last_order(self, obj):
        last_sale = (Sale.objects.filter(user=obj.user,customer_id=obj.customer_id)
                        .order_by("-created_at").first())

        if not last_sale:
            return None

        return SaleSerializer(last_sale).data

        
    
class SaleSerializer(serializers.ModelSerializer):
    oid = serializers.ReadOnlyField()

    class Meta:
        model = Sale
        fields = "__all__"
        
class OrderItemSerializer(serializers.ModelSerializer):
    order = serializers.SlugRelatedField(
        slug_field="oid",
        read_only=True   # 👈 IMPORTANT
    )
    product = serializers.SlugRelatedField(
        queryset=Product.objects.all(),
        slug_field="pid"
    )

    class Meta:
        model = OrderItem
        fields = ["id", "order", "product", "quantity", "price", "external_product_id", "external_variation_id", "raw_product_data"]
        read_only_fields = ["price"]

    def validate(self, data):
        product = data["product"]
        quantity = data["quantity"]

        if product.stock_quantity < quantity:
            raise serializers.ValidationError("Not enough stock")

        return data



    @transaction.atomic
    def create(self, validated_data):
        order = self.context["order"]  # 👈 trusted order
        product = validated_data["product"]
        quantity = validated_data["quantity"]

        validated_data["order"] = order
        validated_data["price"] = (
            product.discounted_price or product.price
        )

        item = super().create(validated_data)

        product.stock_quantity -= quantity
        product.save(update_fields=["stock_quantity"])

        return item
    


    


class ExternalOrderItemSerializer(serializers.Serializer):
    product_id = serializers.CharField(max_length=255)
    variation_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        allow_null=True
    )
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        default=0
    )
    product_name = serializers.CharField(
        required=False,
        allow_blank=True
    )
    raw_product_data = serializers.JSONField(
        required=False,
        default=dict
    )
    


class ExternalOrderSerializer(serializers.Serializer):
    customer_id = serializers.CharField(max_length=255)
    customer_name = serializers.CharField(
        required=False,
        allow_blank=True
    )
    customer_phone = serializers.CharField(
        required=False,
        allow_blank=True
    )
    customer_address = serializers.CharField(
        required=False,
        allow_blank=True
    )
    customer_city = serializers.CharField(
        required=False,
        allow_blank=True
    )
    customer_state = serializers.CharField(
        required=False,
        allow_blank=True
    )
    delivered_to = serializers.ChoiceField(
        choices=["inside_dhaka", "outside_dhaka"],
        required=False,
        default="inside_dhaka"
    )

    items = ExternalOrderItemSerializer(many=True, required=False)

    def to_internal_value(self, data):
        items = data.get("items")

        # Case 1: items is a string → parse it
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except json.JSONDecodeError:
                raise serializers.ValidationError(
                    {"items": "Invalid JSON string for items"}
                )

        # Case 2: items is a list with ONE string inside → parse that string
        if (
            isinstance(items, list)
            and len(items) == 1
            and isinstance(items[0], str)
        ):
            try:
                items = json.loads(items[0])
            except json.JSONDecodeError:
                raise serializers.ValidationError(
                    {"items": "Invalid JSON string for items"}
                )

        data = data.copy()
        data["items"] = items

        return super().to_internal_value(data)



    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        return value





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