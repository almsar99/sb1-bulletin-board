from rest_framework.pagination import PageNumberPagination


class AdPagination(PageNumberPagination):
    page_size = 4
