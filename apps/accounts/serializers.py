from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Department

User = get_user_model()


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ('id', 'name', 'code')


class UserSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    is_admin = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'department',
            'is_staff',
            'is_admin',
            'date_joined',
        )
        read_only_fields = fields


class LoginSerializer(TokenObtainPairSerializer):
    """登录成功后除 access/refresh 外，一并返回当前用户信息。"""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['is_admin'] = user.is_admin
        if user.department_id:
            token['department_id'] = user.department_id
            token['department_code'] = user.department.code
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data['user'] = UserSerializer(self.user).data
        return data
