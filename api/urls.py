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
    path('<str:username>/product/<int:pk>/', views.UserProductDataView.as_view(), name='user-product-view'),

    path('<str:username>/product/<int:pk>/update', views.UserProductUpdateView.as_view(), name='user-product-update'),
    path('<str:username>/orders', views.UserOrderListCreateView.as_view(), name='user-order-list'),
    path('<str:username>/orders/add', views.UserOrderCreateView.as_view(), name='user-order-add'),
    path('<str:username>/conv', views.ConvoCreateView.as_view(), name='convo-create'),
    path('<str:username>/conv/transfer/<str:customer_id>', views.ChatTransfer.as_view(), name='convo-transfer'),
    path('<str:username>/conv/status/<str:customer_id>', views.ChatStatus.as_view(), name='convo-status'),


]
