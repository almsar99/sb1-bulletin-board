from django.contrib import admin

from messaging.models import Message, Thread


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(Thread)
class ThreadAdmin(ReadOnlyAdmin):
    list_display = ("id", "ad", "initiator", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("ad__title", "initiator__email", "ad__author__email")
    list_select_related = ("ad", "initiator")


@admin.register(Message)
class MessageAdmin(ReadOnlyAdmin):
    list_display = ("id", "thread", "author", "created_at", "read_at")
    list_filter = ("created_at",)
    search_fields = ("thread__ad__title", "thread__initiator__email", "thread__ad__author__email")
    list_select_related = ("thread__ad", "author")
