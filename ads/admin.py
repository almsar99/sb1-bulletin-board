from django import forms
from django.contrib import admin
from django.utils.html import format_html

from ads.discussions import validate_discussion
from ads.models import Ad, AdStatus, Review
from ads.workflow import update_ad


@admin.register(Ad)
class AdAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "category", "price", "status", "created_at")
    list_filter = ("status", "category", "created_at")
    search_fields = ("title", "description", "author__email")
    list_select_related = ("author",)
    autocomplete_fields = ("author",)
    readonly_fields = ("image_preview", "submitted_at")
    actions = ("publish", "archive", "make_draft")

    @admin.display(description="Фотография")
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" alt="Фотография объявления" style="max-width:240px;max-height:180px">', obj.image.url
            )
        return "Без фотографии"

    @admin.action(description="Опубликовать выбранные объявления")
    def publish(self, request, queryset):
        self.change_status(request, queryset, AdStatus.PUBLISHED)

    @admin.action(description="Снять выбранные объявления с публикации")
    def archive(self, request, queryset):
        self.change_status(request, queryset, AdStatus.ARCHIVED)

    @admin.action(description="Вернуть выбранные объявления на рассмотрение")
    def make_draft(self, request, queryset):
        self.change_status(request, queryset, AdStatus.DRAFT)

    def change_status(self, request, queryset, status):
        for pk in queryset.values_list("pk", flat=True):
            update_ad(pk=pk, actor=request.user, changes={"status": status}, status_action=True)

    def save_model(self, request, obj, form, change):
        if change:
            saved = update_ad(
                pk=obj.pk, actor=request.user, changes={field: form.cleaned_data[field] for field in form.changed_data}
            )
            obj.status, obj.submitted_at = saved.status, saved.submitted_at
        else:
            if obj.status == AdStatus.DRAFT:
                from django.utils import timezone

                obj.submitted_at = timezone.now()
            super().save_model(request, obj, form, change)


class DiscussionAdminForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = "__all__"

    def clean(self):
        data = super().clean()
        if data.get("ad") and data.get("kind"):
            validate_discussion(
                ad_id=data["ad"].pk, kind=data["kind"], parent=data.get("parent"), instance=self.instance
            )
        return data


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    form = DiscussionAdminForm
    list_display = ("text", "kind", "ad", "author", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("text", "ad__title", "author__email")
    list_select_related = ("ad", "author")
    autocomplete_fields = ("ad", "author")
