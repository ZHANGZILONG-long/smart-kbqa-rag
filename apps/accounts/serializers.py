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
    is_super_admin = serializers.BooleanField(read_only=True)
    is_dept_admin = serializers.BooleanField(read_only=True)
    can_upload = serializers.BooleanField(read_only=True)
    can_manage_staff = serializers.BooleanField(read_only=True)
    role_display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'department',
            'role',
            'role_display',
            'is_staff',
            'is_active',
            'is_admin',
            'is_super_admin',
            'is_dept_admin',
            'can_upload',
            'can_manage_staff',
            'date_joined',
            'last_login',
        )
        read_only_fields = fields


class StaffUserSerializer(UserSerializer):
    """员工列表/详情：附带当前操作者可执行的管理动作。"""

    can_edit = serializers.SerializerMethodField()
    allowed_roles = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + (
            'can_edit',
            'allowed_roles',
        )

    def _actor(self):
        request = self.context.get('request')
        return getattr(request, 'user', None) if request else None

    def get_can_edit(self, obj):
        actor = self._actor()
        return bool(actor and actor.can_edit_staff_user(obj))

    def get_allowed_roles(self, obj):
        actor = self._actor()
        if not actor:
            return []
        return actor.allowed_roles_for_target(obj)


class StaffUpdateSerializer(serializers.Serializer):
    """
    管理员更新员工信息/权限。
    - 部门管理员：仅本部门普通员工；不可改角色为管理员、不可改部门到外部门
    - 总管理员：可改角色（含设为管理员）、部门、启用状态等
    """

    email = serializers.EmailField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    role = serializers.ChoiceField(choices=User.Role.choices, required=False)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)

    def validate_department_id(self, value):
        if value is None:
            return value
        if not Department.objects.filter(pk=value).exists():
            raise serializers.ValidationError('部门不存在')
        return value

    def validate(self, attrs):
        request = self.context['request']
        actor = request.user
        target: User = self.context['target']

        if not actor.can_edit_staff_user(target):
            raise serializers.ValidationError('无权编辑该员工')

        # 禁止把自己禁用（避免锁死）
        if (
            target.pk == actor.pk
            and 'is_active' in attrs
            and attrs['is_active'] is False
        ):
            raise serializers.ValidationError({'is_active': '不能禁用自己的账号'})

        if 'role' in attrs:
            new_role = attrs['role']
            allowed = actor.allowed_roles_for_target(target)
            if new_role not in allowed:
                if actor.is_dept_admin:
                    raise serializers.ValidationError(
                        {'role': '部门管理员不能任命其他管理员，只能管理本部门普通员工'}
                    )
                raise serializers.ValidationError({'role': '无权设置该角色'})

            # 总管理员不能把自己降级为非总管理员（防误操作锁死）
            if (
                target.pk == actor.pk
                and actor.is_super_admin
                and new_role != User.Role.SUPER_ADMIN
            ):
                raise serializers.ValidationError(
                    {'role': '不能取消自己的总管理员身份'}
                )

            # 最后一个总管理员不可被降级
            if (
                target.is_super_admin
                and new_role != User.Role.SUPER_ADMIN
            ):
                other_supers = User.objects.filter(
                    models_q_super_admin()
                ).exclude(pk=target.pk).exists()
                if not other_supers:
                    raise serializers.ValidationError(
                        {'role': '系统至少保留一名总管理员'}
                    )

            # 设为「某部门管理员」时必须绑定具体部门（研发/财务等）
            if new_role == User.Role.DEPT_ADMIN:
                final_dept = attrs.get('department_id', target.department_id)
                if not final_dept:
                    raise serializers.ValidationError(
                        {
                            'department_id': (
                                '设为部门管理员时必须指定所属部门'
                                '（例如研发部、财务部）'
                            )
                        }
                    )

        if 'department_id' in attrs:
            new_dept = attrs['department_id']
            if actor.is_dept_admin:
                # 某部门管理员不可把员工调出本部门
                if new_dept is None or int(new_dept) != int(actor.department_id):
                    raise serializers.ValidationError(
                        {'department_id': '只能管理本部门员工，不能调往其他部门'}
                    )
            # 已是部门管理员时，不能清空部门
            final_role = attrs.get('role', target.role)
            if final_role == User.Role.DEPT_ADMIN and new_dept is None:
                raise serializers.ValidationError(
                    {
                        'department_id': (
                            '部门管理员必须归属具体部门'
                            '（例如研发部、财务部），不能清空'
                        )
                    }
                )

        return attrs

    def update(self, instance: User, validated_data):
        actor = self.context['request'].user

        if 'email' in validated_data:
            instance.email = (validated_data['email'] or '').strip()
        if 'first_name' in validated_data:
            instance.first_name = (validated_data['first_name'] or '').strip()
        if 'last_name' in validated_data:
            instance.last_name = (validated_data['last_name'] or '').strip()
        if 'is_active' in validated_data:
            instance.is_active = bool(validated_data['is_active'])
        if 'department_id' in validated_data:
            instance.department_id = validated_data['department_id']

        if 'role' in validated_data and actor.is_super_admin:
            new_role = validated_data['role']
            instance.role = new_role
            if new_role == User.Role.SUPER_ADMIN:
                instance.is_superuser = True
                instance.is_staff = True
            elif new_role == User.Role.DEPT_ADMIN:
                instance.is_superuser = False
                if instance.pk != actor.pk:
                    instance.is_staff = False
                if not instance.department_id:
                    raise serializers.ValidationError(
                        {
                            'department_id': (
                                '设为部门管理员时必须指定所属部门'
                            )
                        }
                    )
            else:
                instance.is_superuser = False
                instance.is_staff = False

        instance.save()
        return instance


def models_q_super_admin():
    """查询业务总管理员或 Django superuser。"""
    from django.db.models import Q

    return Q(is_superuser=True) | Q(role=User.Role.SUPER_ADMIN)


def _token_claims(token, user):
    token['username'] = user.username
    token['role'] = user.role
    token['is_admin'] = user.is_admin
    token['is_super_admin'] = user.is_super_admin
    token['is_dept_admin'] = user.is_dept_admin
    token['can_upload'] = user.can_upload
    token['can_manage_staff'] = user.can_manage_staff
    if user.department_id:
        token['department_id'] = user.department_id
        token['department_code'] = user.department.code
    return token


class LoginSerializer(TokenObtainPairSerializer):
    """登录成功后除 access/refresh 外，一并返回当前用户信息。"""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        return _token_claims(token, user)

    def validate(self, attrs):
        data = super().validate(attrs)
        data['user'] = UserSerializer(self.user).data
        return data


class RegisterSerializer(serializers.Serializer):
    """
    公开注册：只能创建普通用户，不可自提管理角色。
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
            role=User.Role.USER,
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
        _token_claims(refresh, user)
        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
        }


class ChangePasswordSerializer(serializers.Serializer):
    """当前用户修改自己的登录密码。"""

    old_password = serializers.CharField(write_only=True, max_length=128)
    new_password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    new_password_confirm = serializers.CharField(
        write_only=True, min_length=8, max_length=128
    )

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('当前密码不正确')
        return value

    def validate(self, attrs):
        if attrs.get('new_password') != attrs.get('new_password_confirm'):
            raise serializers.ValidationError(
                {'new_password_confirm': '两次输入的新密码不一致'}
            )
        if attrs.get('old_password') == attrs.get('new_password'):
            raise serializers.ValidationError(
                {'new_password': '新密码不能与当前密码相同'}
            )
        user = self.context['request'].user
        try:
            validate_password(attrs['new_password'], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'new_password': list(exc.messages)})
        return attrs

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password'])
        return user
