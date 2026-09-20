"""Представления объявлений."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from ads.discussions import validate_discussion
from ads.models import Ad, Review
from ads.workflow import create_ad, update_ad
from users.models import User


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "first_name", "last_name")
        read_only_fields = fields


class CategorySerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()
    is_discussion = serializers.BooleanField()


class AdSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    author = AuthorSerializer(read_only=True)

    class Meta:
        model = Ad
        fields = (
            "id",
            "title",
            "price",
            "description",
            "image",
            "category",
            "category_display",
            "status",
            "submitted_at",
            "author",
            "created_at",
        )
        read_only_fields = ("id", "author", "created_at", "submitted_at")

    def create(self, validated_data):
        return create_ad(author=validated_data.pop("author"), data=validated_data)

    def update(self, instance, validated_data):
        return update_ad(pk=instance.pk, actor=self.context["request"].user, changes=validated_data)


class AnswerSerializer(serializers.ModelSerializer):
    author = AuthorSerializer(read_only=True)

    class Meta:
        model = Review
        fields = ("id", "text", "author", "kind", "parent", "created_at")
        read_only_fields = fields


class ReviewSerializer(serializers.ModelSerializer):
    answers = AnswerSerializer(many=True, read_only=True)
    author = AuthorSerializer(read_only=True)

    class Meta:
        model = Review
        fields = ("id", "text", "author", "ad", "kind", "parent", "answers", "created_at")
        read_only_fields = ("id", "author", "ad", "created_at")

    def get_fields(self):
        fields = super().get_fields()
        ad = self.context.get("ad")
        fields["parent"].queryset = self.context.get(
            "parent_queryset", Review.objects.filter(ad=ad) if ad else Review.objects.none()
        )
        return fields

    def validate(self, attrs):
        instance = self.instance
        try:
            validate_discussion(
                ad_id=self.context["ad"].pk,
                kind=attrs.get("kind", instance.kind if instance else "review"),
                parent=attrs.get("parent", instance.parent if instance else None),
                instance=instance,
            )
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs

    def update(self, instance, validated_data):
        if not validated_data:
            return instance
        for field, value in validated_data.items():
            setattr(instance, field, value)
        try:
            # update_fields запрещает fallback INSERT после конкурентного DELETE.
            instance.save(update_fields=validated_data)
        except Review.NotUpdated as error:
            raise NotFound from error
        return instance


class DiscussionSerializer(ReviewSerializer):
    ad_title = serializers.CharField(source="ad.title", read_only=True)

    class Meta(ReviewSerializer.Meta):
        fields = (*ReviewSerializer.Meta.fields, "ad_title")
