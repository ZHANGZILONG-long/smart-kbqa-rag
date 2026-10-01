from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import LoginSerializer, UserSerializer


class LoginView(TokenObtainPairView):
    """
    POST /api/auth/login/
    body: {"username": "...", "password": "..."}
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer


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
