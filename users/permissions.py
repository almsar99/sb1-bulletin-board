"""Права доступа."""

from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwnerOrAdmin(BasePermission):
    """Изменять объект может его автор или администратор, читать — все разрешённые."""

    owner_field = "author"

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.is_admin:
            return True
        owner = getattr(obj, getattr(view, "owner_field", self.owner_field), None)
        return owner == request.user
