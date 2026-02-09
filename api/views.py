from django.shortcuts import render

# Create your views here.
from decimal import Decimal, InvalidOperation
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
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from .utils.files import download_profile_to_storage

import json
from django.db import transaction
from back.models import Package, PackageItem, UserProfile, Product, Conversation, Message, Sale, Setting, ProductImages, OrderItem, Integration
from .serializers import (
    UserProfileSerializer, ProductSerializer,MessageSerializer, PackageSerializer,ExternalOrderSerializer, ExternalOrderItemSerializer,
    ConversationSerializer, SaleSerializer, SettingSerializer, ProductImagesSerializer, OrderItemSerializer, ConversationSummarySerializer
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
    

# Package List View Endpoint

class UserPackageListView(APIView):
    def get(self, request, username):
        user = get_object_or_404(User, username=username)
        packages = Package.objects.filter(user=user)
        serializer = PackageSerializer(packages, many=True, context={'request': request})
        return Response(serializer.data)
    


class UserPackageDataView(APIView):
    
    def get(self, request, username, pacid):
        # Ensure the token user matches the username in the URL
        if request.user.username != username:
            return Response({'error': 'Unauthorized access'}, status=403)

        # Fetch the package belonging to this user
        package = get_object_or_404(Package, pacid=pacid, user=request.user)
        serializer = PackageSerializer(package)
        return Response(serializer.data)
    


# Package Data view Endpoint
    
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


# class ExtOrderUpdate(APIView):
#     def post(self, request, username, order_id):
#         user = get_object_or_404(User, username=username)
#         order = get_object_or_404(Sale, user=user, oid=order_id, source="external", status="pending")
#         try:
#             user = get_object_or_404(User, username__iexact=username)
#             conversation = get_object_or_404(Conversation,customer_id=customer_id, user=user)
#             # messages = (Message.objects.filter(conversation=conversation).order_by("-timestamp")[:10])
#             # if not messages.exists():
#             #     return JsonResponse({"text": "Starting new Converstation"}, status=200)
#             # messages = reversed(messages)
#             serializer = ConversationSummarySerializer(conversation)
#             print("Serialized conversation summary:", serializer.data)
#             return Response(serializer.data)
#         except Exception as e:
#             return JsonResponse({"error": str(e)}, status=500)      



class Update_External_Order_Item_To_Web(APIView):
    @transaction.atomic
    def post(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        order = get_object_or_404(Sale, user=user, oid=order_id, source="external", status="pending")
        order_items = OrderItem.objects.filter(order=order)
        product_name = request.data.get("product_name")
        external_order_id = request.data.get("external_order_id")
        price = request.data.get("price")
        raw_product_data = request.data.get("raw_product_data", "{}")

        if price is None:
            return Response(
                {"error": "price is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            price = Decimal(price)
        except (InvalidOperation, TypeError):
            return Response(
                {"error": "price must be a valid decimal number"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        


        
        try:
            # Logic to update external order to web goes here
            # For example, sending data to an external API

            order.updated_to_web = "updated"
            order.external_order_id = external_order_id
            order.save(update_fields=["updated_to_web", "external_order_id"])
            order_items.update(
                product_name=product_name,
                raw_product_data=raw_product_data,
                price=price,
            )

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
        package_id = request.data.get("package_id")
        quantity = int(request.data.get("quantity", 1))
        
        customer_name = request.data.get("customer_name", "")
        customer_address = request.data.get("customer_address", "")
        customer_phone = request.data.get("customer_phone", "")
        customer_city = request.data.get("customer_city", "")
        customer_state = request.data.get("customer_state", "")

        
        if not customer_id:
            return Response(
                {"error": "customer_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not product_id and not package_id:
            return Response(
                {"error": "Either product_id or package_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if product_id and package_id:
            return Response(
                {"error": "Only one of product_id or package_id is allowed"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # product = get_object_or_404(Product, pid=product_id)
        conversation = get_object_or_404(Conversation,customer_id=customer_id, user=user)

        # 1️⃣ Create Sale (Internal)
        sale = Sale.objects.create(
            user=user,
            source="internal",
            customer_id=customer_id,
            conversation=conversation,
            status="pending",
            customer_name=customer_name,
            customer_address=customer_address,
            customer_phone=customer_phone,
            customer_city=customer_city,
            customer_state=customer_state,
        )

        items_response = []
        total_amount = 0
        
        # =========================
        # 🟢 PRODUCT ORDER
        # =========================
        if product_id:
            product = get_object_or_404(Product, pid=product_id)

            order_item = OrderItem.objects.create(
                order=sale,
                product=product,
                internal_product=product,
                product_name=product.name,
                price=product.discounted_price or product.price,
                quantity=quantity,
            )

            total_amount = order_item.price * quantity

            items_response.append({
                "product_id": product.pid,
                "product_name": product.name,
                "quantity": quantity,
                "price": order_item.price,
            })

        # =========================
        # 🟦 PACKAGE ORDER
        # =========================
        
        else:
            package = get_object_or_404(Package, pacid=package_id)
            sale.package = package

            add_products = request.data.get("add_products", [])
            remove_products = request.data.get("remove_products", [])

            # Base package price
            base_price = package.discounted_price or package.price
            total_amount = base_price

            # Map existing package items by pid
            package_items = {
                item.product.pid: item
                for item in package.items.select_related("product")
            }

            # =========================
            # 🔴 HANDLE PACKAGE ITEMS
            # =========================
            for pid, item in package_items.items():

                # ❌ REMOVED ITEM
                if pid in remove_products:
                    total_amount -= item.remove_price
                    # continue

                    OrderItem.objects.create(
                        order=sale,
                        product=item.product,
                        internal_product=item.product,
                        product_name=item.product.name,
                        price=0,
                        quantity=1,
                        raw_product_data={
                            "package": package.name,
                            "action": "removed",
                            "remove_price": str(item.remove_price),
                        }
                    )

                    items_response.append({
                        "product_id": pid,
                        "product_name": item.product.name,
                        "action": "removed",
                        "price_effect": -float(item.remove_price),
                    })
                    continue

                # ✅ INCLUDED ITEM
                OrderItem.objects.create(
                    order=sale,
                    product=item.product,
                    internal_product=item.product,
                    product_name=item.product.name,
                    price=item.product.discounted_price or item.product.price,
                    quantity=1,
                    raw_product_data={
                        "package": package.name,
                        "action": "included",
                    }
                )

                items_response.append({
                    "product_id": pid,
                    "product_name": item.product.name,
                    "action": "included",
                })

            # =========================
            # 🟢 HANDLE ADDED PRODUCTS
            # =========================
            existing_pids = set(package_items.keys())

            for pid in add_products:

                # ❌ BLOCK DUPLICATE ADD
                if pid in existing_pids:
                    return Response(
                        {"error": f"Product {pid} already exists in package"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                product = get_object_or_404(Product, pid=pid)

                product_price = product.discounted_price or product.price
                total_amount += product_price

                OrderItem.objects.create(
                    order=sale,
                    product=product,
                    internal_product=product,
                    product_name=product.name,
                    price=product_price,
                    quantity=1,
                    raw_product_data={
                        "package": package.name,
                        "action": "added",
                        "product_price": str(product_price),
                    }
                )

                items_response.append({
                    "product_id": pid,
                    "product_name": product.name,
                    "action": "added",
                    "price_effect": float(product_price),
                })



        # 3️⃣ Update Sale total
        print("Total amount for order:", total_amount)
        sale.amount = total_amount
        sale.save()

        return Response(
            {
                "order_id": sale.oid,
                "status": sale.status,
                "total": sale.amount,
                "items": items_response,
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

    
# class NewOrderPackage(APIView):

#     @transaction.atomic
#     def post(self, request, username):
#         user = get_object_or_404(User, username=username)

#         customer_id = request.data.get("customer_id")
#         product_id = request.data.get("product_id")
#         quantity = int(request.data.get("quantity", 1))
#         customer_name = request.data.get("customer_name", "")
#         customer_address = request.data.get("customer_address", "")
#         customer_phone = request.data.get("customer_phone", "")
#         customer_city = request.data.get("customer_city", "")
#         customer_state = request.data.get("customer_state", "")
#         if not customer_id or not product_id:
#             return Response(
#                 {"error": "customer_id and product_id are required"},
#                 status=status.HTTP_400_BAD_REQUEST
#             )

#         product = get_object_or_404(Product, pid=product_id)

#         # 1️⃣ Create Sale (Internal)
#         sale = Sale.objects.create(
#             user=user,
#             source="internal",
#             customer_id=customer_id,
#             status="pending",
#             customer_name=customer_name,
#             customer_address=customer_address,
#             customer_phone=customer_phone,
#             customer_city=customer_city,
#             customer_state=customer_state,
#         )

#         # 2️⃣ Create OrderItem
#         order_item = OrderItem.objects.create(
#             order=sale,
#             product=product,
#             internal_product=product,
#             product_name=product.name,
#             price=product.price,
#             quantity=quantity,
#         )

#         # 3️⃣ Update total amount
#         sale.amount = order_item.price * order_item.quantity
#         sale.save()

#         return Response(
#             {
#                 "order_id": sale.oid,
#                 "status": sale.status,
#                 "total": sale.amount,
#                 "items": [
#                     {
#                         "product_id": product.pid,
#                         "product_name": order_item.product_name,
#                         "quantity": order_item.quantity,
#                         "price": order_item.price,
#                     }
#                 ],
#             },
#             status=status.HTTP_201_CREATED
#         )

#         #Test Order JSON
#         {
#   "customer_id": "CUST-001",
#   "product_id": "sku_ab12cd",
#   "quantity": 3,
#   "customer_name": "John Doe",
#   "customer_address": "123 Main Street, Lagos",
#   "customer_phone": "+2348012345678"
# }



class ExternalSaleCreateAPIView(APIView):
    """
    Create an external sale
    """

    @transaction.atomic
    def post(self, request, username):
        user = get_object_or_404(User, username=username)
        data = request.data

        # Required field
        customer_id = data.get("customer_id")
        if not customer_id:
            return Response(
                {"error": "customer_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Optional: attach to conversation if exists
        conversation = Conversation.objects.filter(
            user=user,
            customer_id=customer_id
        ).first()

        sale = Sale.objects.create(
            user=user,
            source="external",
            conversation=conversation,
            customer_id=customer_id,
            customer_name=data.get("customer_name", ""),
            customer_phone=data.get("customer_phone", ""),
            customer_address=data.get("customer_address", ""),
            customer_city=data.get("customer_city", ""),
            customer_state=data.get("customer_state", ""),
            delivered_to=data.get("delivered_to", "inside_dhaka"),
            external_order_id=data.get("external_order_id"),
            amount=data.get("amount", 0),
            status="pending",
        )

        return Response(
            {
                "message": "External sale created successfully",
                "order_id": sale.id,
                "oid": sale.oid,
                "amount": sale.amount,
                "status": sale.status,
            },
            status=status.HTTP_201_CREATED,
        )
    



class NewOrderExternal(APIView):
    @transaction.atomic
    def post(self, request, username):
        user = get_object_or_404(User, username=username)

        # 🔹 Validate input using serializer
        serializer = ExternalOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        items = data["items"]
        customer_id = data["customer_id"]

        with transaction.atomic():
            # 🔹 Conversation must exist
            conversation = get_object_or_404(
                Conversation,
                customer_id=customer_id,
                user=user
            )

            # 🔹 Create Sale
            sale = Sale.objects.create(
                user=user,
                source="external",
                customer_id=customer_id,
                conversation=conversation,
                customer_name=data.get("customer_name", ""),
                customer_address=data.get("customer_address", ""),
                customer_phone=data.get("customer_phone", ""),
                customer_city=data.get("customer_city", ""),
                customer_state=data.get("customer_state", ""),
                delivered_to=data.get("delivered_to", "inside_dhaka"),
                status="draft",
            )

            total_amount = 0

            # 🔹 Get or create default product (safe)
            default_product = Product.objects.filter(
                user=user,
                name="External Order Placeholder"
            ).first()

            if not default_product:
                default_product = Product.objects.create(
                    user=user,
                    name="External Order Placeholder",
                    price=0,
                    stock_quantity=99999,
                )

            # 🔹 Create order items
            order_items = []
            for item in items:
                price = item.get("price", 0)
                quantity = item["quantity"]

                order_items.append(
                    OrderItem(
                        order=sale,
                        product=default_product,
                        internal_product=None,
                        product_name=item.get("product_name", "External Product"),
                        external_product_id=item["product_id"],
                        external_variation_id=item.get("variation_id"),
                        price=price,
                        quantity=quantity,
                        raw_product_data=item.get("raw_product_data", {}),
                    )
                )

                total_amount += price * quantity

            # 🔹 Bulk insert for performance
            OrderItem.objects.bulk_create(order_items)

            # 🔹 Update sale total
            sale.amount = total_amount
            sale.save(update_fields=["amount"])

        return Response(
            {
                "status" : "success",
                "message": "External order created successfully",
                "order_id": sale.id,
                "oid": sale.oid,
                "amount": sale.amount,
            },
            status=status.HTTP_201_CREATED,
        )

class NewOrderExternalConfirm(APIView):
    @transaction.atomic
    def post(self, request, username):
        user = get_object_or_404(User, username=username)

        # 🔹 Validate input using serializer
        serializer = ExternalOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        items = data["items"]
        customer_id = data["customer_id"]

        with transaction.atomic():
            # 🔹 Conversation must exist
            conversation = get_object_or_404(
                Conversation,
                customer_id=customer_id,
                user=user
            )

            # 🔹 Create Sale
            sale = Sale.objects.create(
                user=user,
                source="external",
                customer_id=customer_id,
                conversation=conversation,
                customer_name=data.get("customer_name", ""),
                customer_address=data.get("customer_address", ""),
                customer_phone=data.get("customer_phone", ""),
                customer_city=data.get("customer_city", ""),
                customer_state=data.get("customer_state", ""),
                delivered_to=data.get("delivered_to", "inside_dhaka"),
                status="pending",
            )

            total_amount = 0

            # 🔹 Get or create default product (safe)
            default_product = Product.objects.filter(
                user=user,
                name="External Order Placeholder"
            ).first()

            if not default_product:
                default_product = Product.objects.create(
                    user=user,
                    name="External Order Placeholder",
                    price=0,
                    stock_quantity=99999,
                )

            # 🔹 Create order items
            order_items = []
            for item in items:
                price = item.get("price", 0)
                quantity = item["quantity"]

                order_items.append(
                    OrderItem(
                        order=sale,
                        product=default_product,
                        internal_product=None,
                        product_name=item.get("product_name", "External Product"),
                        external_product_id=item["product_id"],
                        external_variation_id=item.get("variation_id"),
                        price=price,
                        quantity=quantity,
                        raw_product_data=item.get("raw_product_data", {}),
                    )
                )

                total_amount += price * quantity

            # 🔹 Bulk insert for performance
            OrderItem.objects.bulk_create(order_items)

            # 🔹 Update sale total
            sale.amount = total_amount
            sale.save(update_fields=["amount"])

        return Response(
            {
                "status" : "success",
                "message": "External order created successfully",
                "order_id": sale.id,
                "oid": sale.oid,
                "amount": sale.amount,
            },
            status=status.HTTP_201_CREATED,
        )

class NewOrderExternalUpdate(APIView):

    def get_sale(self, user, order_id):
        return get_object_or_404(
            Sale,
            oid=order_id,   # or id=order_id
            user=user,
            source="external",
        )

    # =========================
    # UPDATE (FULL REPLACE)
    # =========================
    @transaction.atomic
    def put(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        sale = self.get_sale(user, order_id)

        serializer = ExternalOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        customer_id = data["customer_id"]
        items = data["items"]

        conversation = get_object_or_404(
            Conversation,
            customer_id=customer_id,
            user=user
        )

        # Update sale fields
        sale.customer_id = customer_id
        sale.conversation = conversation
        sale.customer_name = data.get("customer_name", "")
        sale.customer_phone = data.get("customer_phone", "")
        sale.customer_address = data.get("customer_address", "")
        sale.customer_city = data.get("customer_city", "")
        sale.customer_state = data.get("customer_state", "")
        sale.delivered_to = data.get("delivered_to", sale.delivered_to)
        sale.status = "draft"
        sale.save()

        # Remove old items
        OrderItem.objects.filter(order=sale).delete()

        default_product, _ = Product.objects.get_or_create(
            user=user,
            name="External Order Placeholder",
            defaults={"price": 0, "stock_quantity": 99999}
        )

        total_amount = 0

        for item in items:
            price = item.get("price", 0)
            qty = item["quantity"]

            OrderItem.objects.create(
                order=sale,
                product=default_product,
                internal_product=None,
                product_name=item.get("product_name", "External Product"),
                external_product_id=item["product_id"],
                external_variation_id=item.get("variation_id"),
                price=price,
                quantity=qty,
                raw_product_data=item.get("raw_product_data", {}),
            )

            total_amount += price * qty

        sale.amount = total_amount
        sale.save(update_fields=["amount"])

        return Response(
            {
                "message": "Order updated successfully",
                "oid": sale.oid,
                "amount": sale.amount,
            },
            status=status.HTTP_200_OK,
        )

    # =========================
    # PARTIAL UPDATE
    # =========================
    @transaction.atomic
    def patch(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        sale = self.get_sale(user, order_id)

        serializer = ExternalOrderSerializer(
            data=request.data,
            partial=True,
            context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # =========================
        # Update conversation if needed
        # =========================
        if "customer_id" in data:
            conversation = get_object_or_404(
                Conversation,
                customer_id=data["customer_id"],
                user=user
            )
            sale.customer_id = data["customer_id"]
            sale.conversation = conversation

        # =========================
        # Update sale fields
        # =========================
        sale_fields = [
            "customer_name",
            "customer_phone",
            "customer_address",
            "customer_city",
            "customer_state",
            "delivered_to",
            "status",
        ]

        for field in sale_fields:
            if field in data:
                setattr(sale, field, data[field])

        sale.save()

        # =========================
        # APPEND / UPDATE ITEMS
        # =========================
        if "items" in data:
            default_product, _ = Product.objects.get_or_create(
                user=user,
                name="External Order Placeholder",
                defaults={"price": 0, "stock_quantity": 99999},
            )

            total_amount = sale.amount or 0

            for item in data["items"]:
                lookup = {
                    "order": sale,
                    "external_product_id": item["product_id"],
                    "external_variation_id": item.get("variation_id"),
                }

                order_item, created = OrderItem.objects.get_or_create(
                    **lookup,
                    defaults={
                        "product": default_product,
                        "internal_product": None,
                        "product_name": item.get("product_name", "External Product"),
                        "price": item.get("price", 0),
                        "quantity": item.get("quantity", 1),
                        "raw_product_data": item.get("raw_product_data", {}),
                    }
                )

                if not created:
                    # adjust total: remove old amount
                    total_amount -= order_item.price * order_item.quantity

                    # update fields
                    order_item.price = item.get("price", order_item.price)
                    order_item.quantity = item.get("quantity", order_item.quantity)
                    order_item.product_name = item.get(
                        "product_name", order_item.product_name
                    )
                    order_item.raw_product_data = item.get(
                        "raw_product_data", order_item.raw_product_data
                    )
                    order_item.save()

                # add new amount
                total_amount += order_item.price * order_item.quantity

            sale.amount = total_amount
            sale.save(update_fields=["amount"])

        return Response(
            {
                "message": "Order updated successfully",
                "oid": sale.oid,
                "amount": sale.amount,
            },
            status=status.HTTP_200_OK,
        )


    # =========================
    # DELETE
    # =========================
    @transaction.atomic
    def delete(self, request, username, order_id):
        user = get_object_or_404(User, username=username)
        sale = self.get_sale(user, order_id)

        OrderItem.objects.filter(order=sale).delete()
        sale.delete()

        return Response(
            {"message": "Order deleted successfully"},
            status=status.HTTP_204_NO_CONTENT,
        )

class ExternalOrderConfirmView(APIView):

    @transaction.atomic
    def post(self, request, username, order_id):
        user = get_object_or_404(User, username=username)

        sale = get_object_or_404(
            Sale,
            oid=order_id,   # or id=order_id
            user=user,
            source="external",
        )

        # =========================
        # Validation
        # =========================
        if sale.status == "pending":
            return Response(
                {"error": "Order already pending"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not OrderItem.objects.filter(order=sale).exists():
            return Response(
                {"error": "Cannot confirm order without items"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # =========================
        # Confirm order
        # =========================
        sale.status = "pending"
        sale.save(update_fields=["status"])

        return Response(
            {
                "message": "Order confirmed successfully",
                "oid": sale.oid,
                "amount": sale.amount,
                "status": sale.status,
            },
            status=status.HTTP_200_OK,
        )




def get_ai_status(user, platform):
        integration = Integration.objects.filter(
            user=user,
            platform=platform
        ).first()

        return integration.is_enabled if integration else False

@method_decorator(csrf_exempt, name='dispatch')
class UserConvCreateView(APIView):
    def post(self, request, username):
        user = get_object_or_404(User, username=username)

        customer_id = request.data.get("customer_id")
        platform = request.data.get("platform")
        ai_enabled = get_ai_status(user, platform)

        if not customer_id:
        
            return Response({"error": "customer_id is required"}, status=400)

        # Check if conversation already exists for this user + customer_id + platform
        existing_convo = Conversation.objects.filter(
            user=user,
            customer_id=customer_id,
            platform=platform,
            
        ).first()
        

        if existing_convo:
            return Response({
                "message": "Conversation already exists",
                "sessionId": customer_id,
                "conversation": ConversationSerializer(existing_convo).data
            }, status=status.HTTP_200_OK)

        # Create a new conversation
        
        data = request.data.copy()
        serializer = ConversationSerializer(data=data)
        if serializer.is_valid():
            # print("Conversation created:", serializer.data)
            # # serializer.save()               #Problem Here
            # print("Conversation created Done:", serializer.data)

            convo = Conversation.objects.create(
                user=user,
                customer_id=str(customer_id),
                platform=platform,
                is_ai_enabled=ai_enabled
            )

            return Response(
                {
                    "message": "Conversation created",
                    "sessionId": customer_id,
                    "conversation_id": convo.id,
                    "is_ai_enabled": convo.is_ai_enabled
                },
                status=201
            )
            

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    

@method_decorator(csrf_exempt, name='dispatch')
class UserConvUpdateView(APIView):
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def put(self, request, username, aid):
        user = get_object_or_404(User, username=username)

        # Ensure conversation belongs to user
        conversation = get_object_or_404(
            Conversation,
            customer_id=aid,
            user=user
        )

        data = request.data.copy()
        profile_image = data.get("profile_image")
        print("Received profile_image:", profile_image)

        # ===============================
        # CASE 1: profile_image is a URL
        # ===============================
        if isinstance(profile_image, str) and profile_image.startswith("http"):
            try:
                image_url = download_profile_to_storage(profile_image)
                print("Downloaded image URL:", image_url)
                conversation.profile_image = image_url
                conversation.save(update_fields=["profile_image"])
                return Response({"profile_image": image_url})
            except Exception as e:
                return Response(
                    {"profile_image": f"Failed to download image: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # ===============================
        # CASE 2: profile_image is FILE
        # (DRF handles it automatically)
        # ===============================

        serializer = ConversationSerializer(
            conversation,
            data=data,
            partial=True
        )

        if serializer.is_valid():
            serializer.save()
            conversation.save()
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@method_decorator(csrf_exempt, name='dispatch')    
class MessageCreateView(APIView):
    def get(self, request, username, aid):
        user = get_object_or_404(User, username=username)

        # Only allow sending messages to conversations owned by user
        conversation = get_object_or_404(Conversation, customer_id=aid, user=user)

        messages = Message.objects.filter(conversation=conversation).order_by("timestamp")
        serializer = MessageSerializer(messages, many=True)
        return Response(serializer.data)

    def post(self, request, username, aid):
        user = get_object_or_404(User, username=username)

        # Only allow sending messages to conversations owned by user
        conversation = get_object_or_404(Conversation, customer_id=aid, user=user)

        data = request.data.copy()
        data["conversation"] = conversation.id  # attach to convo

        serializer = MessageSerializer(data=data)
        if serializer.is_valid():
            message = serializer.save()
            print("Message created:", serializer.data)
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
        current_package = request.data.get("current_package")
        detected_intent = request.data.get("detected_intent", "")
        extra_data = request.data.get("extra_data", "")
        
        conversation.detected_intent = detected_intent
    
        if extra_data:
            conversation.save(update_fields=["extra_data"])
            return Response({
            "status": "success",
            "message": f"extra_data {extra_data} Given for conversation.",
            "conversation_id": conversation.id,
            }, status=status.HTTP_200_OK)
        
        if detected_intent:
            conversation.save(update_fields=["detected_intent"])
            return Response({
            "status": "success",
            "message": f"detected_intent {detected_intent} Given for conversation.",
            "conversation_id": conversation.id,
            }, status=status.HTTP_200_OK)

        if not current_package and not current_product:
            return Response(
                {"error": "Either current_package or current_product is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if current_package and current_product:
            return Response(
                {"error": "Only one of current_product or current_package is allowed"},
                status=status.HTTP_400_BAD_REQUEST
            )


        if current_product:
            conversation.current_product = current_product
            conversation.save(update_fields=["current_product"])
            return Response({
            "status": "success",
            "message": f"Product {current_product} selected for conversation.",
            "conversation_id": conversation.id,
            }, status=status.HTTP_200_OK)

        if current_package:
            conversation.current_package = current_package
            
            conversation.save(update_fields=["current_package"])

            return Response({
                "status": "success",
                "message": f"Package {current_package} selected for conversation.",
                "conversation_id": conversation.id,
            }, status=status.HTTP_200_OK)
    
class GetLastMessages(APIView):
    def get(self, request, username, id):
        customer_id = str(id)
        print("Fetching last messages for conversation:", customer_id)
        user = get_object_or_404(User, username__iexact=username)
        print("User found:", user.username)
        # convo = get_object_or_404(Conversation, customer_id=id, user=user)
        convo = Conversation.objects.filter(customer_id=str(id),user=user).first()
        if not convo:
            print("No conversation found for customer_id:", customer_id)
            return JsonResponse({"error": "Conversation not found"},status=404)
        print("Conversation found:", convo)
        # orders = get_object_or_404(Sale, customer_id=id, user=user)
        current_product = convo.current_product
        print("Current product:", current_product if current_product else "Nonee")
        is_ai_enabled = convo.is_ai_enabled
        print("Conversation found:", convo)
        print("Current product:", current_product)



        print("Starting Messages Fetching:")

        try:
            messages_qss = convo.messages.order_by('-timestamp')        #Find Messages by related name from Conversation model

            print("Messages fetched:", messages_qss)
            print("Starting Last order Fetching:")

            if not messages_qss.exists():
                conversation_text = "This is a new Conversation"
                print("No messages found. Returning default conversation text.")
                return JsonResponse({"conversation_text": conversation_text}, status=200)
                
            else:
                messages_qs = list(messages_qss[:10])
                print(f"Found {messages_qss.count()} messages.")
                
                messages = reversed(messages_qs)
                print("Reversed messages:", messages)
                conversation_text = "\n".join(
                    f"{msg.sender.capitalize()}: {msg.text}"
                    for msg in messages
                )
                print("Compiled conversation text:", conversation_text)
                
        except Exception as e:
            print("Error fetching messages:", str(e))
            conversation_text = "This is a Latest Conversation"
            
            



        try:
            last_order = (
                Sale.objects
                .filter(customer_id=customer_id, user=user)
                .order_by('-created_at')
                .first()
            )

            if last_order:
                # order exists
                print("Last order found:", last_order)
                last_orders_qs = last_order.order_by('-created_at')[:1]
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
                pass
            else:
                # no order found
                print("No last order found for this customer.")
                orders_data = []
                orders_data.append({
                        "status": "No orders found",
                    })
                pass

        except Exception as e:
            # only catches real errors (DB issue, etc.)
            print("Error in Last Order", e)


        # orders_qs = Sale.objects.filter(customer_id=customer_id, user=user)

        
        

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



class LastMessageView(APIView):
    def get(self, request, username, id):
        customer_id = str(id)
        try:
            user = get_object_or_404(User, username__iexact=username)
            conversation = (Conversation.objects.filter(user=user, customer_id=customer_id).order_by("-id").first())
            
            # conversation = get_object_or_404(Conversation,customer_id=customer_id, user=user)

            if not conversation:
                return Response(
                    {"error": "Conversation not found"},
                    status=404
            )
            # messages = (Message.objects.filter(conversation=conversation).order_by("-timestamp")[:10])
            # if not messages.exists():
            #     return JsonResponse({"text": "Starting new Converstation"}, status=200)
            # messages = reversed(messages)
            serializer = ConversationSummarySerializer(conversation)
            print("Serialized conversation summary:", serializer.data)
            return Response(serializer.data)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)            