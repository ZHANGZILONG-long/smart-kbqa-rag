from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

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


class RegisterSerializer(serializers.Serializer):
    """
    公开注册：创建普通用户（不可自提管理员权限）。
    body: username, password, password_confirm, email?, department_id?
    """

    username = serializers.CharField(min_length=3, max_length=150)
    password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    password_confirm = serializers.CharField(write_only=True, min_length=8, max_length=128)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    department_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_username(self, value):
        username = (value or '').strip()
        if not username:
            raise serializers.ValidationError('用户名不能为空')
        if User.objects.filter(username__iexact=username).exists():
            raise serializers.ValidationError('用户名已被占用')
        # 与 AbstractUser 默认校验对齐：字母数字和 @.+-_
        import re

        if not re.fullmatch(r'[\w.@+-]+', username):
            raise serializers.ValidationError(
                '用户名只能包含字母、数字和 @ . + - _'
            )
        return username

    def validate_department_id(self, value):
        if value is None:
            return value
        if not Department.objects.filter(pk=value).exists():
            raise serializers.ValidationError('部门不存在')
        return value

    def validate(self, attrs):
        if attrs.get('password') != attrs.get('password_confirm'):
            raise serializers.ValidationError({'password_confirm': '两次输入的密码不一致'})
        try:
            validate_password(attrs['password'])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)})
        return attrs

    def create(self, validated_data):
        department_id = validated_data.pop('department_id', None)
        validated_data.pop('password_confirm', None)
        password = validated_data.pop('password')
        user = User(
            username=validated_data['username'],
            email=(validated_data.get('email') or '').strip(),
            is_staff=False,
            is_superuser=False,
            is_active=True,
            department_id=department_id,
        )
        user.set_password(password)
        user.save()
        return user

    def to_auth_response(self, user):
        refresh = RefreshToken.for_user(user)
        refresh['username'] = user.username
        refresh['is_admin'] = user.is_admin
        if user.department_id:
            refresh['department_id'] = user.department_id
            refresh['department_code'] = user.department.code
        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
        }
