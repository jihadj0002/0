from django.shortcuts import render

# Create your views here.

from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from rest_framework import serializers
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
import json
from django.db import transaction
from back.models import UserProfile, Product, Conversation, Message, Sale, Setting, ProductImages, OrderItem
from .serializers import (
    UserProfileSerializer, ProductSerializer,MessageSerializer,
    ConversationSerializer, SaleSerializer, SettingSerializer, ProductImagesSerializer, OrderItemSerializer
)

class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

class ConversationViewSet(viewsets.ModelViewSet):
    queryset = Conversation.objects.all()
    serializer_class = ConversationSerializer

class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleSerializer

class SettingViewSet(viewsets.ModelViewSet):
    queryset = Setting.objects.all()
    serializer_class = SettingSerializer

class UserProductListView(APIView):
    def get(self, request, username):
        user = get_object_or_404(User, username=username)
        products = Product.objects.filter(user=user)
        serializer = ProductSerializer(products, many=True, context={'request': request})
        return Response(serializer.data)
    
class ProductImagesSerializer(serializers.ModelSerializer):
    images = serializers.SerializerMethodField()

    class Meta:
        model = ProductImages
        fields = ['id', 'images']

    def get_images(self, obj):
        request = self.context.get('request')
        if obj.images:
            return request.build_absolute_uri(obj.images.url) if request else obj.images.url
        return ""

class UserProductDataView(APIView):
    
    def get(self, request, username, pk):
        # Ensure the token user matches the username in the URL
        if request.user.username != username:
            return Response({'error': 'Unauthorized access'}, status=403)

        # Fetch the product belonging to this user
        product = get_object_or_404(Product, id=pk, user=request.user)
        serializer = ProductSerializer(product)
        return Response(serializer.data)


class UserProductUpdateView(APIView):
    def put(self, request, username, pk):
        user = get_object_or_404(User, username=username)
        product = get_object_or_404(Product, id=pk, user=user)
        serializer = ProductSerializer(product, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserOrderListCreateView(APIView):
    def get(self, request, username):
        user = get_object_or_404(User, username=username)
        orders = Sale.objects.filter(user=user)
        serializer = SaleSerializer(orders, many=True)
        return Response(serializer.data)

    def post(self, request, username):
        user = get_object_or_404(User, username=username)
        data = request.data.copy()
        data['user'] = user.id
        serializer = SaleSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    


class UserOrderCreateView(APIView):
    def post(self, request, username):
        user = get_object_or_404(User, username=username)
        data = request.data.copy()
        data['user'] = user.id
        serializer = SaleSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class OrderStartView(APIView):
    def post(self, request, username):
        user = get_object_or_404(User, username=username)
        customer_id = request.data.get("customer_id")
        sale = Sale.objects.create(
            user=user,
            customer_id=customer_id,
            status="draft"
        )

        return Response(
            {
                "order_id": sale.oid,
                "message": "Order started"
            },
            status=status.HTTP_201_CREATED
        )
    
class AddOrderItem(APIView):
    def post(self, request, username):
        user = get_object_or_404(User, username=username)

        order, created = Sale.objects.get_or_create(user=user, status="draft", defaults={"customer_id": request.data.get("customer_id")})

        serializer = OrderItemSerializer(
            data={
                "product": request.data.get("pid"),
                "quantity": request.data.get("quantity"),
            },
            context={"order": order}
        )

        if serializer.is_valid(raise_exception=True):
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def get(self, request, username):
        user = get_object_or_404(User, username=username)
        customer_id = request.data.get("customer_id")

        order = get_object_or_404(user=user, status="draft", customer_id=customer_id)
        

        items = order.items.select_related("product")

        data = {
            "order_id": order.oid,
            "status": order.status,
            "amount": order.amount,
            "items": [
                {
                    "id": item.id,
                    "pid": item.product.pid,
                    "quantity": item.quantity,
                    "price": item.price,
                    "line_total": item.price * item.quantity
                }
                for item in items
            ]
        }

        return Response(data, status=status.HTTP_200_OK)
    
    @transaction.atomic
    def patch(self, request, username):
        user = get_object_or_404(User, username=username)
        customer_id = request.data.get("customer_id")

        order = get_object_or_404(user=user, status="draft", customer_id=customer_id)
        
        pid = request.data.get("pid")
        new_quantity = request.data.get("quantity")

        if not pid:
            return Response(
                {"error": "pid is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        item = get_object_or_404(
            OrderItem,
            order=order,
            product__pid=pid
        )

        if not new_quantity or int(new_quantity) <= 0:
            return Response(
                {"error": "Quantity must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST
            )

        diff = int(new_quantity) - item.quantity

        if diff > 0 and item.product.stock_quantity < diff:
            return Response(
                {"error": "Not enough stock"},
                status=status.HTTP_400_BAD_REQUEST
            )

        item.product.stock_quantity -= diff
        item.product.save(update_fields=["stock_quantity"])

        item.quantity = int(new_quantity)
        item.save(update_fields=["quantity"])

        return Response(
            {
                "order_id": order.oid,
                "pid": pid,
                "quantity": item.quantity,
                "price": item.price,
                "line_total": item.price * item.quantity
            },
            status=status.HTTP_200_OK
        )
    
    
    @transaction.atomic
    def delete(self, request, username):
        user = get_object_or_404(User, username=username)
        customer_id = request.data.get("customer_id")
        order_id = request.data.get("order_id")
        

        order = get_object_or_404(user=user, status="draft", customer_id=customer_id)
        order = get_object_or_404(
            Sale,
            oid=order_id,
            user=user,
            status__in=["draft", "pending"]
        )

        pid = request.data.get("pid")
        if not pid:
            return Response(
                {"error": "pid is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        item = get_object_or_404(
            OrderItem,
            order=order,
            product__pid=pid
        )

        item.product.stock_quantity += item.quantity
        item.product.save(update_fields=["stock_quantity"])

        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
class AddOrderItemView(APIView):

    def get(self, request, username, order_id):
        user = get_object_or_404(User, username=username)

        order = get_object_or_404(
            Sale,
            oid=order_id,
            user=user
        )
        

        items = order.items.select_related("product")

        data = {
            "order_id": order.oid,
            "status": order.status,
            "amount": order.amount,
            "items": [
                {
                    "id": item.id,
                    "pid": item.product.pid,
                    "quantity": item.quantity,
                    "price": item.price,
                    "line_total": item.price * item.quantity
                }
                for item in items
            ]
        }

        return Response(data, status=status.HTTP_200_OK)

    def post(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        

        # order = get_object_or_404(
        #     Sale,
        #     oid=order_id,
        #     user=user,
        #     status="draft"
        # )
        order, created = Sale.objects.get_or_create(user=user, status="draft", defaults={"customer_id": request.data.get("customer_id")})
        

        serializer = OrderItemSerializer(
            data={
                "product": request.data.get("pid"),
                "quantity": request.data.get("quantity"),
            },
            context={"order": order}
        )

        if serializer.is_valid(raise_exception=True):
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OrderItemUpdateDeleteView(APIView):

    @transaction.atomic
    def patch(self, request, username, order_id):
        user = get_object_or_404(User, username=username)

        order = get_object_or_404(
            Sale,
            oid=order_id,
            user=user,
            status__in=["draft", "pending"]
        )

        pid = request.data.get("pid")
        new_quantity = request.data.get("quantity")

        if not pid:
            return Response(
                {"error": "pid is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        item = get_object_or_404(
            OrderItem,
            order=order,
            product__pid=pid
        )

        if not new_quantity or int(new_quantity) <= 0:
            return Response(
                {"error": "Quantity must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST
            )

        diff = int(new_quantity) - item.quantity

        if diff > 0 and item.product.stock_quantity < diff:
            return Response(
                {"error": "Not enough stock"},
                status=status.HTTP_400_BAD_REQUEST
            )

        item.product.stock_quantity -= diff
        item.product.save(update_fields=["stock_quantity"])

        item.quantity = int(new_quantity)
        item.save(update_fields=["quantity"])

        return Response(
            {
                "order_id": order.oid,
                "pid": pid,
                "quantity": item.quantity,
                "price": item.price,
                "line_total": item.price * item.quantity
            },
            status=status.HTTP_200_OK
        )

    @transaction.atomic
    def delete(self, request, username, order_id):
        user = get_object_or_404(User, username=username)

        order = get_object_or_404(
            Sale,
            oid=order_id,
            user=user,
            status__in=["draft", "pending"]
        )

        pid = request.data.get("pid")
        if not pid:
            return Response(
                {"error": "pid is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        item = get_object_or_404(
            OrderItem,
            order=order,
            product__pid=pid
        )

        item.product.stock_quantity += item.quantity
        item.product.save(update_fields=["stock_quantity"])

        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConfirmOrderView(APIView):
    @transaction.atomic
    def post(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        order = get_object_or_404(Sale, user=user, oid=order_id, status="draft")

        items = order.items.all()
        if not items.exists():
            return Response(
                {"error": "Order has no items"},
                status=status.HTTP_400_BAD_REQUEST
            )

        total = sum(item.price * item.quantity for item in items)

        order.amount = total
        order.status = "pending"
        order.customer_name = request.data.get("customer_name")
        order.customer_address = request.data.get("customer_address")
        order.customer_phone = request.data.get("customer_phone")

        order.save(update_fields=["amount", "status", "customer_name", "customer_address", "customer_phone"])

        return Response(
            {
                "order_id": order.oid,
                "total": total,
                "status": order.status,
                "customer_name": order.customer_name,
                "customer_address": order.customer_address,
                "customer_phone": order.customer_phone,
            },
            status=status.HTTP_200_OK
        )
    

class Update_External_Order_Item_To_Web(APIView):
    @transaction.atomic
    def post(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        order = get_object_or_404(Sale, user=user, oid=order_id, source="external", status="pending")
        order_items = get_object_or_404(OrderItem, order=order)
        product_name = request.data.get("product_name")
        external_order_id = request.data.get("external_order_id")
        price = request.data.get("price")
        raw_product_data = request.data.get("raw_product_data", "{}")
        


        
        try:
            # Logic to update external order to web goes here
            # For example, sending data to an external API

            order.updated_to_web = "updated"
            order.save(update_fields=["updated_to_web", "external_order_id"])

            order_items.product_name = product_name
            order_items.raw_product_data = raw_product_data
            order_items.price = price

            order_items.save(update_fields=["product_name", "raw_product_data", "price"])

            return Response(
                {
                    "order_id": order.oid,
                    "status": "External order updated to web successfully",
                    "status": "Updated Product Name successfully"
                },
                status=status.HTTP_200_OK
            )
        except Exception as e:
            order.updated_to_web = "failed"
            order.save(update_fields=["updated_to_web"])
            return Response(
                {
                    "order_id": order.oid,
                    "error": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        


class NewOrder(APIView):

    @transaction.atomic
    def post(self, request, username):
        user = get_object_or_404(User, username=username)

        customer_id = request.data.get("customer_id")
        product_id = request.data.get("product_id")
        quantity = int(request.data.get("quantity", 1))
        customer_name = request.data.get("customer_name", "")
        customer_address = request.data.get("customer_address", "")
        customer_phone = request.data.get("customer_phone", "")
        customer_city = request.data.get("customer_city", "")
        customer_state = request.data.get("customer_state", "")
        if not customer_id or not product_id:
            return Response(
                {"error": "customer_id and product_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        product = get_object_or_404(Product, pid=product_id)

        # 1️⃣ Create Sale (Internal)
        sale = Sale.objects.create(
            user=user,
            source="internal",
            customer_id=customer_id,
            status="pending",
            customer_name=customer_name,
            customer_address=customer_address,
            customer_phone=customer_phone,
            customer_city=customer_city,
            customer_state=customer_state,
        )

        # 2️⃣ Create OrderItem
        order_item = OrderItem.objects.create(
            order=sale,
            product=product,
            internal_product=product,
            product_name=product.name,
            price=product.price,
            quantity=quantity,
        )

        # 3️⃣ Update total amount
        sale.amount = order_item.price * order_item.quantity
        sale.save()

        return Response(
            {
                "order_id": sale.oid,
                "status": sale.status,
                "total": sale.amount,
                "items": [
                    {
                        "product_id": product.pid,
                        "product_name": order_item.product_name,
                        "quantity": order_item.quantity,
                        "price": order_item.price,
                    }
                ],
            },
            status=status.HTTP_201_CREATED
        )

        #Test Order JSON
        {
  "customer_id": "CUST-001",
  "product_id": "sku_ab12cd",
  "quantity": 3,
  "customer_name": "John Doe",
  "customer_address": "123 Main Street, Lagos",
  "customer_phone": "+2348012345678"
}

    



class NewOrderExternal(APIView):
    @transaction.atomic
    
    def post(self, request, username):
        user = get_object_or_404(User, username=username)
        
        data = request.data

        if isinstance(data, list):
            data = data[0]


        customer_id = data.get("customer_id")

        # Required fields
        items = data.get("items", [])

        if not customer_id or not items:
            return Response(
                {"error": "customer_id and items are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():

                # Create Sale
                sale = Sale.objects.create(
                    user=user,
                    source="external",
                    # external_order_id=data.get("external_order_id"),
                    customer_id=customer_id,
                    customer_name=data.get("customer_name", ""),
                    customer_address=data.get("customer_address", ""),
                    customer_phone=data.get("customer_phone", ""),
                    customer_city=data.get("customer_city", ""),
                    customer_state=data.get("customer_state", ""),
                    delivered_to=data.get("delivered_to", "inside_dhaka"),
                    status="pending",
                )
                print("Created Sale with ID:", sale.id)

                total_amount = 0

                # A default internal product for external items
                default_product, created = Product.objects.get_or_create(
                    user=user,
                    defaults={
                        "name": "External Order Placeholder",
                        "price": 0,
                        "stock_quantity": 99999,
                        # "status": True,
                    }
                )
                print("Default product for external items:", default_product.pid)
                for item in items:
                    price = item.get("price", 0)
                    quantity = int(item.get("quantity", 1))
                    

                    OrderItem.objects.create(
                        order=sale,
                        product=default_product,  # required FK
                        internal_product=None,
                        product_name=item.get("product_name", "External Product"),
                        external_product_id=item.get("external_product_id"),
                        external_variation_id=item.get("external_variation_id"),
                        
                        price=price,
                        quantity=quantity,
                        raw_product_data=item.get("raw_product_data", {}),
                    )
                    
                    total_amount += price * quantity

                # Update total amount
                sale.amount = total_amount
                sale.save()

            return Response(
                {
                    "message": "External order created successfully",
                    "order_id": sale.id,
                    "oid": sale.oid,
                    "amount": sale.amount,
                },
                status=status.HTTP_201_CREATED,
            )

        except Product.DoesNotExist:
            return Response(
                {"error": "Default external product not found"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        
    # Test Order External JSON
#     {
#   "external_order_id": "EXT-ORD-12345",
#   "customer_id": "CUST-001",
#   "customer_name": "John Doe",
#   "customer_address": "123 Main Street, Lagos",
#   "customer_phone": "+2348012345678",
#   "items": [
#     {
#       "external_product_id": "EXT-PROD-001",
#       "product_name": "Wireless Mouse",
#       "price": 2500,
#       "quantity": 2,
#       "raw_product_data": {
#         "color": "black",
#         "brand": "Logitech"
#       }
#     },
#     {
#       "external_product_id": "EXT-PROD-002",
#       "product_name": "USB Keyboard",
#       "price": 4000,
#       "quantity": 1,
#       "raw_product_data": {
#         "layout": "QWERTY",
#         "connection": "USB"
#       }
#     }
#   ]
# }


@method_decorator(csrf_exempt, name='dispatch')
class UserConvCreateView(APIView):
    def post(self, request, username):
        user = get_object_or_404(User, username__iexact=username)

        customer_id = request.data.get("customer_id")
        platform = request.data.get("platform")

        if not customer_id or not platform:
            return Response(
                {"error": "customer_id and platform are required"},
                status=400
            )

        # Prevent duplicates
        convo = Conversation.objects.filter(
            user=user,
            customer_id=str(customer_id),
            platform=platform
        ).first()

        if convo:
            return Response(
                {
                    "message": "Conversation already exists",
                    "sessionId": customer_id,
                    "conversation_id": convo.id
                },
                status=200
            )

        # ✅ CREATE CONVERSATION DIRECTLY
        convo = Conversation.objects.create(
            user=user,
            customer_id=str(customer_id),
            platform=platform
        )

        return Response(
            {
                "message": "Conversation created",
                "sessionId": customer_id,
                "conversation_id": convo.id
            },
            status=201
        )

    

@method_decorator(csrf_exempt, name='dispatch')
class UserConvUpdateView(APIView):
    def put(self, request, username, aid):
        user = get_object_or_404(User, username=username)


        # Only allow sending messages to conversations owned by user
        conversation = get_object_or_404(Conversation, customer_id=aid, user=user)
        serializer = ConversationSerializer(
            conversation, data=request.data, partial=True
        )

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



@method_decorator(csrf_exempt, name='dispatch')    
class MessageCreateView(APIView):
    def post(self, request, username, aid):
        user = get_object_or_404(User, username=username)

        # Only allow sending messages to conversations owned by user
        conversation = get_object_or_404(Conversation, customer_id=aid, user=user)

        data = request.data.copy()
        data["conversation"] = conversation.id  # attach to convo

        serializer = MessageSerializer(data=data)
        if serializer.is_valid():
            message = serializer.save()

            # optional: update conversation.last_message or auto_enable_ai()
            # conversation.auto_enable_ai()
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

# Get Message by mid (platform message id)
@method_decorator(csrf_exempt, name='dispatch')
class MessageRetrieveView(APIView):
    """
    Retrieve a single message by mid (platform message id)
    """

    def get(self, request, username, aid, mid):
        # 1️⃣ Get the user
        user = get_object_or_404(User, username=username)

        # 2️⃣ Get the conversation
        conversation = get_object_or_404(Conversation, customer_id=aid, user=user)

        # 3️⃣ Get the message by mid within this conversation
        message = get_object_or_404(Message, conversation=conversation, mid=mid)

        # 4️⃣ Serialize and return
        serializer = MessageSerializer(message)
        return Response(serializer.data)

    


class DisableConvoAI(APIView):
    def get(self, request, username, id):
        user = get_object_or_404(User, username=username)
        conversation = get_object_or_404(Conversation, id=id, user=user)

        # Disable AI
        conversation.is_ai_enabled = False
        conversation.save()

        # Return JSON for AJAX fetch()
        return Response({
            'status': 'success',
            'message': 'AI disabled for this conversation',
            'ai_enabled': False,
            'conversation_id': conversation.id
        }, status=status.HTTP_200_OK)
class EnableConvoAI(APIView):
    def get(self, request, username, id):
        user = get_object_or_404(User, username=username)
        conversation = get_object_or_404(Conversation, id=id, user=user)
        conversation.is_ai_enabled = True
        conversation.save()
        return Response({'status': 'AI enabled for this conversation'}, status=status.HTTP_200_OK)

class GetConvoAIStatus(APIView):
    def get(self, request, username, id):
        user = get_object_or_404(User, username=username)
        conversation = get_object_or_404(Conversation, id=id, user=user)
        return Response({'is_ai_enabled': conversation.is_ai_enabled}, status=status.HTTP_200_OK)

class GetConvoStatus(APIView):
    def get(self, request,username, id):

        user = get_object_or_404(User, username=username)
        convo = get_object_or_404(Conversation, customer_id=id, user=user)
        return JsonResponse({
            "id": convo.id,
            "customer_id": convo.customer_id,
            "customer_name": convo.customer_name,
            "refer_customer_with": convo.refer_customer_with,
            "customer_gender": convo.customer_gender,

            "chat_summary": convo.chat_summary,
            "is_ai_enabled": convo.is_ai_enabled,
            "timestamp": convo.timestamp.strftime("%d %b, %Y"),

        })
class SelectProductView(APIView):
    def post(self, request, username, aid):
        user = get_object_or_404(User, username=username)

        conversation = get_object_or_404(Conversation, customer_id=aid, user=user)

        current_product = request.data.get("current_product")
        

        conversation.current_product = current_product
        conversation.save(update_fields=["current_product"])

        return Response({
            "status": "success",
            "message": f"Product {current_product} selected for conversation.",
            "conversation_id": conversation.id,
        }, status=status.HTTP_200_OK)
    
class GetLastMessages(APIView):
    def get(self, request, username, id):
        print("Fetching last messages for conversation:", id)
        user = get_object_or_404(User, username__iexact=username)
        print("User found:", user.username)
        # convo = get_object_or_404(Conversation, customer_id=id, user=user)
        convo = Conversation.objects.filter(customer_id=str(id),user=user).first()
        if not convo:
            print("No conversation found for customer_id:", id)
            return JsonResponse({"error": "Conversation not found"},status=404)
        print("Conversation found:", convo)
        # orders = get_object_or_404(Sale, customer_id=id, user=user)
        current_product = convo.current_product
        print("Current product:", current_product)
        is_ai_enabled = convo.is_ai_enabled
        print("Conversation found:", convo)
        print("Current product:", current_product)


        messages_qs = (
            Message.objects
            .filter(conversation=convo)
            .order_by('-timestamp')[:10]
        )
        print("Messages fetched:", messages_qs)
        orders_qs = Sale.objects.filter(customer_id=id, user=user)
        last_orders_qs = orders_qs.order_by('-created_at')[:1]
        
        messages = reversed(messages_qs)
        print("Reversed messages:", messages)
        conversation_text = "\n".join(
            f"{msg.sender.capitalize()}: {msg.text}"
            for msg in messages
        )
        print("Compiled conversation text:", conversation_text)
        orders_data = []
        for order in last_orders_qs:
            items = [
                {
                    "product_pid": item.product.pid,
                    "product_name": item.product.name,
                    "quantity": item.quantity,
                    "price": str(item.price),
                }
                for item in order.items.all()
            ]

            orders_data.append({
                "order_id": order.oid,
                "status": order.status,
                "amount": str(order.amount),
                "created_at": order.created_at,
                "customer_name": order.customer_name,
                "customer_address": order.customer_address,
                "customer_phone": order.customer_phone,
                "items": items,
            })

        return JsonResponse(
            {
                "conversation_id": convo.id,
                "is_ai_enabled": convo.is_ai_enabled,
                "customer_id": convo.customer_id,
                "conversation": conversation_text,
                "last_orders": orders_data,
                "current_product": current_product,
            },
            json_dumps_params={"ensure_ascii": False},
            safe=False
        )
