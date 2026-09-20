"""Объявления сервиса."""

from django.conf import settings
from django.db import models
from django.db.models import Q

from ads.validators import validate_ad_image


class AdCategory(models.TextChoices):
    SCRIPTS = "scripts", "Скрипты и код"
    FREELANCE = "freelance", "Фриланс"
    COURSES = "courses", "Курсы и обучение"
    SOLUTIONS = "solutions", "Готовые решения"
    HARDWARE = "hardware", "Железо"


class AdStatus(models.TextChoices):
    DRAFT = "draft", "На рассмотрении"
    PUBLISHED = "published", "Опубликовано"
    ARCHIVED = "archived", "Снято с публикации"


class AdQuerySet(models.QuerySet):
    def visible_to(self, user):
        if user.is_authenticated:
            if user.is_admin:
                return self
            return self.filter(Q(status=AdStatus.PUBLISHED) | Q(author=user))
        return self.filter(status=AdStatus.PUBLISHED)


class Ad(models.Model):
    objects = AdQuerySet.as_manager()
    status = models.CharField("состояние", max_length=16, choices=AdStatus.choices, default=AdStatus.PUBLISHED)
    category = models.CharField("рубрика", max_length=16, choices=AdCategory.choices, default=AdCategory.SCRIPTS)
    title = models.CharField("название", max_length=200)
    price = models.PositiveIntegerField("цена")
    description = models.TextField("описание")
    image = models.ImageField(
        "фотография", upload_to="ads/%Y/%m/", blank=True, null=True, validators=[validate_ad_image]
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ads")
    created_at = models.DateTimeField("дата создания", auto_now_add=True)
    submitted_at = models.DateTimeField("дата отправки на рассмотрение", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "объявление"
        verbose_name_plural = "объявления"

    def __str__(self):
        return self.title


class DiscussionKind(models.TextChoices):
    REVIEW = "review", "Отзыв"
    QUESTION = "question", "Вопрос"


class ReviewQuerySet(models.QuerySet):
    def accessible_to(self, user):
        visible = Q(ad__in=Ad.objects.visible_to(user))
        if user.is_authenticated:
            # Скрытие объявления не лишает автора права управлять своим отзывом.
            visible |= Q(author=user)
        return self.filter(visible)


class Review(models.Model):
    objects = ReviewQuerySet.as_manager()
    kind = models.CharField("вид", max_length=16, choices=DiscussionKind.choices, default=DiscussionKind.REVIEW)
    parent = models.ForeignKey(
        "self", verbose_name="ответ на", null=True, blank=True, on_delete=models.CASCADE, related_name="answers"
    )
    text = models.TextField("текст")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    ad = models.ForeignKey(Ad, on_delete=models.CASCADE, related_name="reviews")
    created_at = models.DateTimeField("дата создания", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "отзыв"
        verbose_name_plural = "отзывы"

    def __str__(self):
        return self.text[:80]
