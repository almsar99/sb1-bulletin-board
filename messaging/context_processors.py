from django.db.models import Q

from messaging.models import Message


def unread_messages(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    count = (
        Message.objects.filter(
            Q(thread__initiator=request.user) | Q(thread__ad__author=request.user), read_at__isnull=True
        )
        .exclude(author=request.user)
        .count()
    )
    return {"unread_message_count": count}
