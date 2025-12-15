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
    
class AddOrderItemView(APIView):
    def post(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        order = get_object_or_404(Sale, oid=order_id, user=user, status="pending")

        serializer = OrderItemSerializer(data={
            "order": order.id,
            "product": request.data.get("product"),
            "quantity": request.data.get("quantity"),
            "price": request.data.get("price"),
        })

        if serializer.is_valid():
            item = serializer.save()

            # reduce stock
            product = item.product
            product.stock_quantity -= item.quantity
            product.save(update_fields=["stock_quantity"])

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class OrderItemUpdateDeleteView(APIView):

    def patch(self, request, pk):
        item = get_object_or_404(OrderItem, pk=pk)
        new_quantity = request.data.get("quantity")

        if not new_quantity or int(new_quantity) <= 0:
            return Response(
                {"error": "Quantity must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST
            )

        diff = int(new_quantity) - item.quantity

        if item.product.stock_quantity < diff:
            return Response(
                {"error": "Not enough stock"},
                status=status.HTTP_400_BAD_REQUEST
            )

        item.product.stock_quantity -= diff
        item.product.save(update_fields=["stock_quantity"])

        item.quantity = new_quantity
        item.save(update_fields=["quantity"])

        return Response(OrderItemSerializer(item).data)

    def delete(self, request, pk):
        item = get_object_or_404(OrderItem, pk=pk)

        # restore stock
        product = item.product
        product.stock_quantity += item.quantity
        product.save(update_fields=["stock_quantity"])

        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class ConfirmOrderView(APIView):
    @transaction.atomic
    def post(self, request, order_id):
        order = get_object_or_404(Sale, oid=order_id, status="pending")

        items = order.items.all()
        if not items.exists():
            return Response(
                {"error": "Order has no items"},
                status=status.HTTP_400_BAD_REQUEST
            )

        total = sum(item.price * item.quantity for item in items)

        order.amount = total
        order.status = "delivering"
        order.save(update_fields=["amount", "status"])

        return Response(
            {
                "order_id": order.oid,
                "total": total,
                "status": order.status
            },
            status=status.HTTP_200_OK
        )




@method_decorator(csrf_exempt, name='dispatch')
class UserConvCreateView(APIView):
    def post(self, request, username):
        user = get_object_or_404(User, username=username)

        customer_id = request.data.get("customer_id")
        platform = request.data.get("platform")

        if not customer_id:
            return Response({"error": "customer_id is required"}, status=400)

        # Check if conversation already exists for this user + customer_id + platform
        existing_convo = Conversation.objects.filter(
            user=user,
            customer_id=customer_id,
            platform=platform
        ).first()

        if existing_convo:
            return Response({
                "message": "Conversation already exists",
                "sessionId": customer_id,
                "conversation": ConversationSerializer(existing_convo).data
            }, status=status.HTTP_200_OK)

        # Create a new conversation
        data = request.data.copy()
        data["user"] = user.id

        serializer = ConversationSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

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
            conversation.auto_enable_ai()
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
    
class GetLastMessages(APIView):
    def get(self, request, username, id):
        user = get_object_or_404(User, username=username)
        convo = get_object_or_404(Conversation, customer_id=id, user=user)
        # orders = get_object_or_404(Sale, customer_id=id, user=user)
        current_product = convo.current_product
        is_ai_enabled = convo.is_ai_enabled


        messages_qs = (
            Message.objects
            .filter(conversation=convo)
            .order_by('-timestamp')[:10]
        )

        orders_qs = Sale.objects.filter(customer_id=id, user=user)
        last_orders_qs = orders_qs.order_by('-created_at')[:2]
        
        messages = reversed(messages_qs)

        conversation_text = "\n".join(
            f"{msg.sender.capitalize()}: {msg.text}"
            for msg in messages
        )

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
