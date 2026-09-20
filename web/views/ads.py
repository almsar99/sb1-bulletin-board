"""Каталог, объявления, отзывы и очередь модерации."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import ListView

from ads.models import Ad, AdCategory, AdStatus, Review
from ads.workflow import create_ad, update_ad
from messaging.forms import MessageForm
from messaging.models import Thread
from web.forms import AdForm, ReviewForm


class AdListView(ListView):
    model = Ad
    template_name = "ads/ad_list.html"
    context_object_name = "ads"
    paginate_by = 4
    catalog_only = True

    def get_queryset(self):
        queryset = Ad.objects.visible_to(self.request.user).select_related("author").order_by("-created_at", "-id")
        if self.catalog_only:
            queryset = queryset.filter(status=AdStatus.PUBLISHED)
        category = self.kwargs.get("category")
        if category:
            if category not in AdCategory.values:
                raise Http404
            queryset = queryset.filter(category=category)
        search = self.request.GET.get("search", "").strip()
        return queryset.filter(title__icontains=search) if search else queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active_category"] = self.kwargs.get("category", "")
        context["category_label"] = dict(AdCategory.choices).get(context["active_category"], "")
        return context


class DiscussionListView(LoginRequiredMixin, ListView):
    template_name = "ads/discussions.html"
    context_object_name = "questions"
    paginate_by = 20

    def get_queryset(self):
        return Review.objects.filter(
            kind="question", parent__isnull=True, ad__in=Ad.objects.visible_to(self.request.user)
        ).select_related("ad", "author")

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "active_category": "discussions"}


class ReviewQueueView(AdListView):
    template_name = "accounts/review_queue.html"
    catalog_only = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_admin:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(status=AdStatus.DRAFT)
            .order_by(F("submitted_at").desc(nulls_last=True), "-created_at", "-id")
        )


def require_owner(request, obj):
    if obj.author_id != request.user.pk and not request.user.is_admin:
        raise PermissionDenied


def get_ad_detail_context(request, ad, *, review_form=None, message_form=None):
    kind = request.GET.get("kind", "review")
    if kind not in ("review", "question"):
        raise Http404
    if review_form is None and request.user.is_authenticated:
        review_form = ReviewForm(ad=ad, kind=kind)
    reviews = Paginator(
        ad.reviews.filter(kind=kind, parent__isnull=True).select_related("author").prefetch_related("answers__author"),
        20,
    ).get_page(request.GET.get("page"))
    return {
        "ad": ad,
        "review_form": review_form,
        "reviews": reviews,
        "page_obj": reviews,
        "kind": kind,
        "private_message_form": message_form if message_form is not None else MessageForm(),
        "private_thread": (
            Thread.objects.filter(ad=ad, initiator=request.user).first()
            if request.user.is_authenticated and request.user.pk != ad.author_id
            else None
        ),
    }


@require_http_methods(["GET", "POST"])
def ad_detail(request, pk):
    ad = get_object_or_404(Ad.objects.visible_to(request.user).select_related("author"), pk=pk)
    form = None
    if request.method == "POST":
        kind = request.GET.get("kind", "review")
        if kind not in ("review", "question"):
            raise Http404
        if not request.user.is_authenticated:
            return redirect_to_login(request.path)
        form = ReviewForm(request.POST, ad=ad, kind=kind)
        if form.is_valid():
            review = form.save(commit=False)
            review.author = request.user
            review.save()
            messages.success(request, "Сообщение опубликовано.")
            suffix = "?kind=question#reviews" if kind == "question" or review.parent_id else "#reviews"
            return redirect(reverse("web:ad-detail", args=[pk]) + suffix)
    return render(request, "ads/ad_detail.html", get_ad_detail_context(request, ad, review_form=form))


@login_required
@require_http_methods(["GET", "POST"])
def ad_create(request):
    form = AdForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        ad = create_ad(author=request.user, data=form.cleaned_data, via_web=True)
        messages.success(
            request, "Объявление отправлено на рассмотрение. В каталоге оно появится после подтверждения."
        )
        return redirect("web:ad-detail", pk=ad.pk)
    return render(request, "ads/ad_form.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def ad_edit(request, pk):
    ad = get_object_or_404(Ad.objects.visible_to(request.user), pk=pk)
    require_owner(request, ad)
    form = AdForm(request.POST if request.method == "POST" else None, request.FILES or None, instance=ad)
    if request.method == "POST" and form.is_valid():
        update_ad(pk=ad.pk, actor=request.user, changes=form.cleaned_data)
        messages.success(
            request,
            "Изменения сохранены." if request.user.is_admin else "Изменения сохранены и отправлены на рассмотрение.",
        )
        return redirect("web:ad-detail", pk=ad.pk)
    return render(request, "ads/ad_form.html", {"form": form, "ad": ad})


@login_required
@require_http_methods(["GET", "POST"])
def ad_delete(request, pk):
    ad = get_object_or_404(Ad.objects.visible_to(request.user), pk=pk)
    require_owner(request, ad)
    if request.method == "POST":
        ad.delete()
        messages.success(request, "Объявление удалено.")
        return redirect("web:account")
    return render(request, "ads/ad_confirm_delete.html", {"ad": ad})


@login_required
@require_POST
def review_delete(request, pk):
    visible_ads = Ad.objects.visible_to(request.user)
    review = get_object_or_404(Review.objects.accessible_to(request.user), pk=pk)
    require_owner(request, review)
    ad_id = review.ad_id
    ad_is_visible = visible_ads.filter(pk=ad_id).exists()
    review.delete()
    messages.success(request, "Отзыв удалён.")
    if not ad_is_visible:
        return redirect("web:ad-list")
    return redirect(reverse("web:ad-detail", args=[ad_id]) + "#reviews")


@login_required
@require_POST
def ad_status(request, pk):
    ad = get_object_or_404(Ad.objects.visible_to(request.user), pk=pk)
    require_owner(request, ad)
    status = request.POST.get("status")
    if status not in AdStatus.values:
        return render(request, "errors/400.html", status=400)
    update_ad(pk=ad.pk, actor=request.user, changes={"status": status}, status_action=True)
    messages.success(request, "Состояние объявления изменено.")
    return redirect("web:ad-detail", pk=ad.pk)
