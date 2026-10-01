from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from django.contrib.auth import get_user_model
from django.db.models import Q

from common.audit import write_audit
from common.models import AuditLog
from .models import Department
from .serializers import (
    ChangePasswordSerializer,
    DepartmentSerializer,
    LoginSerializer,
    RegisterSerializer,
    StaffUpdateSerializer,
    StaffUserSerializer,
    UserSerializer,
)

User = get_user_model()


class LoginRateThrottle(ScopedRateThrottle):
    scope = 'login'


class LoginView(TokenObtainPairView):
    """
    POST /api/auth/login/
    body: {"username": "...", "password": "..."}
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer
    throttle_classes = [AnonRateThrottle, LoginRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        ok = 200 <= response.status_code < 300
        actor = None
        if ok:
            username = request.data.get('username')
            if username:
                actor = User.objects.filter(username=username).first()
        write_audit(
            request=request,
            user=actor,
            action=AuditLog.Action.LOGIN,
            detail={'username': request.data.get('username')},
            success=ok,
            status_code=response.status_code,
        )
        return response


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AnonRateThrottle, LoginRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        write_audit(
            request=request,
            user=user,
            action=AuditLog.Action.REGISTER,
            object_type='user',
            object_id=user.id,
            detail={'username': user.username},
            status_code=201,
        )
        return Response(
            serializer.to_auth_response(user),
            status=status.HTTP_201_CREATED,
        )


class DepartmentListView(APIView):
    """
    GET /api/auth/departments/
    注册页下拉用，无需登录。
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        qs = Department.objects.all().order_by('code')
        return Response({'results': DepartmentSerializer(qs, many=True).data})


class RefreshTokenView(TokenRefreshView):
    """
    POST /api/auth/refresh/
    body: {"refresh": "..."}
    """

    permission_classes = [permissions.AllowAny]


class MeView(generics.RetrieveAPIView):
    """
    GET /api/auth/me/
    Header: Authorization: Bearer <access>
    """

    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    """
    POST /api/auth/change-password/
    body: {
      "old_password": "...",
      "new_password": "...",
      "new_password_confirm": "..."
    }
    成功后返回新的 access/refresh，旧 refresh 尽量拉黑。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        old_refresh = request.data.get('refresh')
        if old_refresh:
            try:
                RefreshToken(old_refresh).blacklist()
            except Exception:
                pass

        refresh = RefreshToken.for_user(user)
        from .serializers import _token_claims

        _token_claims(refresh, user)
        write_audit(
            request=request,
            user=user,
            action=AuditLog.Action.CHANGE_PASSWORD,
            object_type='user',
            object_id=user.id,
            status_code=200,
        )
        return Response(
            {
                'detail': '密码已修改',
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            }
        )


class LogoutView(APIView):
    """
    POST /api/auth/logout/
    body: {"refresh": "..."}

    将 refresh token 加入黑名单，使其无法再换取新的 access。
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        refresh = request.data.get('refresh')
        if not refresh:
            return Response(
                {'detail': '请提供 refresh token。'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh)
            token.blacklist()
        except Exception:
            return Response(
                {'detail': '无效的 refresh token。'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.LOGOUT,
            status_code=200,
        )
        return Response({'detail': '已退出登录。'}, status=status.HTTP_200_OK)


class IsStaffManager(permissions.BasePermission):
    """仅总管理员 / 部门管理员可访问员工管理。"""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'can_manage_staff', False)
        )


def _staff_queryset(actor):
    qs = User.objects.select_related('department').order_by('id')
    if actor.is_super_admin:
        return qs
    if actor.is_dept_admin and actor.department_id:
        return qs.filter(department_id=actor.department_id)
    return qs.none()


class StaffListView(APIView):
    """
    GET /api/auth/staff/
    查询参数：
      q=关键字（用户名/邮箱/姓名）
      department_id=
      role=
      is_active=1|0
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffManager]

    def get(self, request):
        actor = request.user
        qs = _staff_queryset(actor)

        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(username__icontains=q)
                | Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
            )

        role = (request.query_params.get('role') or '').strip()
        if role:
            qs = qs.filter(role=role)

        dept_id = request.query_params.get('department_id')
        if dept_id not in (None, ''):
            try:
                dept_id = int(dept_id)
            except (TypeError, ValueError):
                return Response(
                    {'detail': 'department_id 无效'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if actor.is_dept_admin and int(actor.department_id) != dept_id:
                return Response(
                    {'detail': '无权查看其他部门员工'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            qs = qs.filter(department_id=dept_id)

        is_active = request.query_params.get('is_active')
        if is_active in ('1', 'true', 'yes'):
            qs = qs.filter(is_active=True)
        elif is_active in ('0', 'false', 'no'):
            qs = qs.filter(is_active=False)

        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except ValueError:
            page = 1
        try:
            page_size = min(100, max(1, int(request.query_params.get('page_size', 50))))
        except ValueError:
            page_size = 50

        total = qs.count()
        start = (page - 1) * page_size
        rows = qs[start : start + page_size]
        data = StaffUserSerializer(
            rows, many=True, context={'request': request}
        ).data
        return Response(
            {
                'count': total,
                'results': data,
                'page': page,
                'page_size': page_size,
            }
        )


class StaffDetailView(APIView):
    """
    GET   /api/auth/staff/{id}/
    PATCH /api/auth/staff/{id}/
    """

    permission_classes = [permissions.IsAuthenticated, IsStaffManager]

    def get_object(self, request, pk):
        try:
            user = User.objects.select_related('department').get(pk=pk)
        except User.DoesNotExist:
            return None
        if not request.user.can_view_staff_user(user):
            return None
        return user

    def get(self, request, pk):
        user = self.get_object(request, pk)
        if not user:
            return Response(
                {'detail': '员工不存在或无权查看'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            StaffUserSerializer(user, context={'request': request}).data
        )

    def patch(self, request, pk):
        user = self.get_object(request, pk)
        if not user:
            return Response(
                {'detail': '员工不存在或无权查看'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not request.user.can_edit_staff_user(user):
            return Response(
                {'detail': '无权编辑该员工（部门管理员仅可管理本部门普通员工）'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = StaffUpdateSerializer(
            data=request.data,
            partial=True,
            context={'request': request, 'target': user},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.update(user, serializer.validated_data)
        user.refresh_from_db()
        write_audit(
            request=request,
            user=request.user,
            action=AuditLog.Action.STAFF_UPDATE,
            object_type='user',
            object_id=user.id,
            detail={
                'target': user.username,
                'fields': list(serializer.validated_data.keys()),
            },
            status_code=200,
        )
        return Response(
            StaffUserSerializer(user, context={'request': request}).data
        )
