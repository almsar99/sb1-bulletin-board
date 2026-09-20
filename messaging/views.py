from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from ads.models import Ad, AdStatus
from messaging.forms import MessageForm
from messaging.models import Message, Thread
from messaging.rate_limit import ERROR, allow_send
from users.models import User


def send(thread, author, text):
    message = Message.objects.create(thread=thread, author=author, text=text)
    Thread.objects.filter(pk=thread.pk).update(updated_at=message.created_at)


@login_required
@require_POST
def start(request, pk):
    form = MessageForm(request.POST)
    with transaction.atomic():
        ad = get_object_or_404(Ad.objects.select_for_update(), pk=pk, status=AdStatus.PUBLISHED)
        if ad.author_id == request.user.pk:
            raise Http404
        if form.is_valid():
            get_object_or_404(User.objects.select_for_update(), pk=request.user.pk)
            thread = Thread.objects.filter(ad=ad, initiator=request.user).first()
            if allow_send(request.user.pk, new_thread=thread is None):
                if thread is None:
                    thread = Thread.objects.create(ad=ad, initiator=request.user)
                thread = Thread.objects.select_for_update().get(pk=thread.pk)
                send(thread, request.user, form.cleaned_data["text"])
                return redirect("messaging:detail", pk=thread.pk)
            form.add_error(None, ERROR)
    from web.views.ads import get_ad_detail_context

    ad = get_object_or_404(Ad.objects.visible_to(request.user).select_related("author"), pk=pk)
    return render(request, "ads/ad_detail.html", get_ad_detail_context(request, ad, message_form=form))


@login_required
@require_http_methods(["GET"])
def thread_list(request):
    latest = Message.objects.filter(thread=OuterRef("pk")).order_by("-created_at", "-pk")
    unread = Message.objects.filter(thread=OuterRef("pk"), read_at__isnull=True).exclude(author=request.user)
    threads = (
        Thread.objects.for_user(request.user)
        .select_related("ad__author", "initiator")
        .annotate(latest_text=Subquery(latest.values("text")[:1]), has_unread=Exists(unread))
    )
    page = Paginator(threads, 20).get_page(request.GET.get("page"))
    rows = [{"thread": thread, "other": thread.other_participant(request.user)} for thread in page]
    return render(request, "messaging/list.html", {"rows": rows, "page_obj": page})


@login_required
@require_http_methods(["GET", "POST"])
def detail(request, pk):
    thread = get_object_or_404(Thread.objects.for_user(request.user).select_related("ad__author", "initiator"), pk=pk)
    form = MessageForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            get_object_or_404(User.objects.select_for_update(), pk=request.user.pk)
            thread = get_object_or_404(Thread.objects.for_user(request.user).select_for_update(of=("self",)), pk=pk)
            if allow_send(request.user.pk):
                send(thread, request.user, form.cleaned_data["text"])
                return redirect("messaging:detail", pk=pk)
            form.add_error(None, ERROR)
    paginator = Paginator(thread.messages.select_related("author"), 50)
    page = paginator.get_page(request.GET.get("page", paginator.num_pages))
    items = list(page)
    # Отмечаем только сообщения из этого ответа; поступившие одновременно остаются непрочитанными.
    unread_ids = [item.pk for item in items if item.author_id != request.user.pk and item.read_at is None]
    Message.objects.filter(pk__in=unread_ids, read_at__isnull=True).update(read_at=timezone.now())
    return render(
        request,
        "messaging/detail.html",
        {
            "thread": thread,
            "other": thread.other_participant(request.user),
            "items": items,
            "page_obj": page,
            "form": form,
        },
    )
