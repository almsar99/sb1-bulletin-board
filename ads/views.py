"""Обработчики объявлений."""

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from ads.filters import AdFilter, StableOrderingFilter
from ads.models import Ad, AdCategory, DiscussionKind, Review
from ads.paginators import AdPagination
from ads.serializers import AdSerializer, CategorySerializer, DiscussionSerializer, ReviewSerializer
from users.permissions import IsOwnerOrAdmin


@extend_schema_view(
    list=extend_schema(summary="Список объявлений", description="Открытый список: четыре объявления на странице."),
    create=extend_schema(summary="Создать объявление"),
    retrieve=extend_schema(summary="Просмотр объявления"),
    update=extend_schema(summary="Заменить объявление"),
    partial_update=extend_schema(summary="Изменить объявление"),
    destroy=extend_schema(summary="Удалить объявление"),
)
@extend_schema(tags=["Объявления"])
class AdViewSet(ModelViewSet):
    queryset = Ad.objects.select_related("author").order_by("-created_at", "-id")
    serializer_class = AdSerializer
    pagination_class = AdPagination
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]
    filter_backends = [DjangoFilterBackend, StableOrderingFilter]
    filterset_class = AdFilter
    ordering_fields = ["price", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return super().get_queryset().visible_to(self.request.user)

    def get_permissions(self):
        if self.action == "list":
            return [AllowAny()]
        return [IsAuthenticated(), IsOwnerOrAdmin()]

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)


@extend_schema(exclude=True)
class AdListAliasViewSet(AdViewSet):
    pass


@extend_schema_view(
    list=extend_schema(summary="Отзывы объявления"),
    create=extend_schema(summary="Добавить отзыв"),
    retrieve=extend_schema(summary="Просмотр отзыва"),
    partial_update=extend_schema(summary="Изменить отзыв"),
    destroy=extend_schema(summary="Удалить отзыв"),
)
@extend_schema(tags=["Отзывы"])
class ReviewViewSet(ModelViewSet):
    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filter_backends = []

    def get_ad(self):
        if not hasattr(self, "_ad"):
            queryset = Ad.objects.all()
            if self.action not in ("retrieve", "partial_update", "destroy"):
                queryset = queryset.visible_to(self.request.user)
            self._ad = get_object_or_404(queryset, pk=self.kwargs["ad_id"])
        return self._ad

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Review.objects.none()
        accessible = Review.objects.accessible_to(self.request.user)
        queryset = (
            accessible.filter(ad=self.get_ad())
            .select_related("author", "parent")
            .prefetch_related(Prefetch("answers", queryset=accessible.select_related("author")))
        )
        if self.action == "list":
            kind = self.request.query_params.get("kind", DiscussionKind.REVIEW)
            if kind not in DiscussionKind.values:
                raise ValidationError({"kind": "Неизвестный вид записи."})
            queryset = queryset.filter(kind=kind, parent__isnull=True)
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["ad"] = self.get_ad()
            context["parent_queryset"] = Review.objects.accessible_to(self.request.user).filter(ad=self.get_ad())
        return context

    def perform_create(self, serializer):
        serializer.save(author=self.request.user, ad=self.get_ad())

    def perform_update(self, serializer):
        serializer.save()
        # DRF очищает prefetch у исходного объекта после изменения. Ответ снова
        # собираем с теми же ограничениями, чтобы не раскрыть скрытые ответы.
        serializer.instance = get_object_or_404(self.get_queryset(), pk=serializer.instance.pk)


@extend_schema(tags=["Объявления"], summary="Список рубрик")
class CategoryListView(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = CategorySerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        return [{"value": value, "label": label, "is_discussion": False} for value, label in AdCategory.choices] + [
            {"value": "discussions", "label": "Обсуждение", "is_discussion": True}
        ]


@extend_schema(tags=["Отзывы"], summary="Лента вопросов")
class DiscussionListView(ListAPIView):
    serializer_class = DiscussionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = []

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Review.objects.none()
        return (
            Review.objects.filter(
                kind=DiscussionKind.QUESTION, parent__isnull=True, ad__in=Ad.objects.visible_to(self.request.user)
            )
            .select_related("ad", "author")
            .prefetch_related("answers__author")
        )
