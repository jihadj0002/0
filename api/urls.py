from rest_framework import routers
from django.urls import path, include
from . import views

router = routers.DefaultRouter()
router.register(r'userprofiles', views.UserProfileViewSet)
router.register(r'products', views.ProductViewSet)
router.register(r'conversations', views.ConversationViewSet)
router.register(r'sales', views.SaleViewSet)
router.register(r'settings', views.SettingViewSet)

urlpatterns = [
    path('', include(router.urls)),

    path('<str:username>/product_list', views.UserProductListView.as_view(), name='user-product-list'),
    path('<str:username>/product/<int:pk>', views.UserProductDataView.as_view(), name='user-product-update'),
    path('<str:username>/product/<int:pk>/update', views.UserProductUpdateView.as_view(), name='user-product-update'),
    path('<str:username>/orders', views.UserOrderListCreateView.as_view(), name='user-order-list'),

]
