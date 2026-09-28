from bson import ObjectId
from django.contrib.auth import authenticate, login, logout
from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ChildProfile
from .permissions import ParentOwnershipMixin
from .serializers import ChildProfileSerializer, LoginSerializer, SignupSerializer
from .services import create_parent_account


class SignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = create_parent_account(
            serializer.validated_data["email"],
            serializer.validated_data["password"],
        )
        return Response(
            {"id": str(user.pk), "email": user.email},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        login(request, user)
        return Response(
            {"id": str(user.pk), "email": user.email, "role": user.role},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChildListCreateView(ParentOwnershipMixin, generics.ListCreateAPIView):
    queryset = ChildProfile.objects.all()
    serializer_class = ChildProfileSerializer
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer):
        serializer.save(parent=self.get_parent())


class ChildDetailView(ParentOwnershipMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ChildProfile.objects.all()
    serializer_class = ChildProfileSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_object(self):
        pk = self.kwargs.get("pk")
        if not isinstance(pk, str) or not ObjectId.is_valid(pk):
            raise NotFound()
        obj = self.get_queryset().filter(pk=ObjectId(pk)).first()
        if obj is None:
            raise NotFound()
        return obj
