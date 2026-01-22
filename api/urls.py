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

    path('<str:username>/package_list', views.UserPackageListView.as_view(), name='user-package-list'),
    path('<str:username>/package/<str:pacid>/', views.UserPackageDataView.as_view(), name='user-package-view'),
    # path('<str:username>/package/<str:pacid>/add', views.UserPackageItemAddView.as_view(), name='user-package-item-add'),
    # path('<str:username>/package/<str:pacid>/remove', views.UserPackageItemRemoveView.as_view(), name='user-package-item-remove'),
    # Add Package Item and remove Package Item endpoints


    path('<str:username>/orders', views.UserOrderListCreateView.as_view(), name='user-order-list'),
    path('<str:username>/orders/add', views.UserOrderCreateView.as_view(), name='user-order-add'),
    
    path('<str:username>/orders/start', views.OrderStartView.as_view(), name='user-order-start'),
    
    path('<str:username>/orders/new', views.NewOrder.as_view(), name='user-order-new'),
    path('<str:username>/orders/newex', views.NewOrderExternal.as_view(), name='user-order-new-external'),
    path('<str:username>/orders/monowa', views.ExternalSaleCreateAPIView.as_view(), name='monowa-user-order-new-external'),
    path('<str:username>/orders/<str:order_id>/update-ext-success', views.Update_External_Order_Item_To_Web.as_view(), name='user-order-update-external-success'),
    
    path('<str:username>/orders/items', views.AddOrderItem.as_view(), name='user-order-add'),
    
    path('<str:username>/orders/<str:order_id>/items', views.AddOrderItemView.as_view(), name='user-order-add-item'),
    path('<str:username>/orders/<str:order_id>/edit', views.OrderItemUpdateDeleteView.as_view(), name='user-order-update-delete-item'),
    path('<str:username>/orders/<str:order_id>/confirm', views.ConfirmOrderView.as_view(), name='user-order-confirm'),
    


    # Conversation AI Management
    path('<str:username>/conv/', views.UserConvCreateView.as_view(), name='convo-handler'),
    path('<str:username>/conv/msgs/<str:id>', views.GetLastMessages.as_view(), name='get-last-messages'),
    path('<str:username>/conv/msg/<str:id>', views.LastMessageView.as_view(), name='get-last-messages-multi'),
    
    path('<str:username>/conv/<str:aid>/select_p', views.SelectProductView.as_view(), name='select-product'),
    
    path('<str:username>/conv/<str:aid>/update', views.UserConvUpdateView.as_view(), name='chat-convo-update'),

    path('<str:username>/conv/<str:aid>/msg', views.MessageCreateView.as_view(), name='chat-handler'),
    path('<str:username>/conv/<str:aid>/msg/<str:mid>', views.MessageRetrieveView.as_view(), name='chat-msg-retrieve'),

    
    path('<str:username>/conv/disable/<str:id>', views.DisableConvoAI.as_view(), name='bot-disable'),
    path('<str:username>/conv/enable/<str:id>', views.EnableConvoAI.as_view(), name='bot-enable'),
    path('<str:username>/conv/AIstatus/<str:id>', views.GetConvoAIStatus.as_view(), name='get-bot-status'),
    path('<str:username>/conv/status/<str:id>', views.GetConvoStatus.as_view(), name='get-conv-status'),
    


]
