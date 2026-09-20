"""Поиск, диапазон цен и устойчивый порядок между страницами."""

from django_filters import rest_framework as filters
from rest_framework.filters import OrderingFilter

from ads.models import Ad


class AdFilter(filters.FilterSet):
    search = filters.CharFilter(field_name="title", lookup_expr="icontains")
    price_min = filters.NumberFilter(field_name="price", lookup_expr="gte", min_value=0)
    price_max = filters.NumberFilter(field_name="price", lookup_expr="lte", min_value=0)

    class Meta:
        model = Ad
        fields = ("search", "price_min", "price_max", "category")


class StableOrderingFilter(OrderingFilter):
    def get_ordering(self, request, queryset, view):
        ordering = list(super().get_ordering(request, queryset, view) or ["-created_at"])
        if not any(field.lstrip("-") == "id" for field in ordering):
            ordering.append("-id" if ordering[0].startswith("-") else "id")
        return ordering
