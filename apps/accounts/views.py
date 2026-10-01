from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import Department
from .serializers import (
    DepartmentSerializer,
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
)


class LoginView(TokenObtainPairView):
    """
    POST /api/auth/login/
    body: {"username": "...", "password": "..."}
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer


class RegisterView(APIView):
    """
    POST /api/auth/register/
    body: {
      "username": "...",
      "password": "...",
      "password_confirm": "...",
      "email": "",
      "department_id": null
    }
    成功后直接返回 JWT，便于注册后自动登录。
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
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
        return Response({'detail': '已退出登录。'}, status=status.HTTP_200_OK)
