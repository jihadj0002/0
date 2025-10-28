from rest_framework import serializers
from back.models import UserProfile, Product, Conversation, Sale, Setting

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'

class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = '__all__'

class SaleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sale
        fields = '__all__'

class SettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Setting
        fields = '__all__'
