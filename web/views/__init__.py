"""Публичные обработчики сайта; реализация разделена по назначению."""

from web.views.accounts import (
    AccountView,
    SiteLoginView,
    SitePasswordResetConfirmView,
    SitePasswordResetDoneView,
    SitePasswordResetView,
    confirm_signup,
    resend_password_reset_email,
    resend_verification_email,
    signup,
    signup_check_email,
)
from web.views.ads import (
    AdListView,
    DiscussionListView,
    ReviewQueueView,
    ad_create,
    ad_delete,
    ad_detail,
    ad_edit,
    ad_status,
    review_delete,
)
from web.views.errors import page_not_found_view, permission_denied_view, server_error_view

__all__ = [
    "AccountView",
    "AdListView",
    "DiscussionListView",
    "ReviewQueueView",
    "SiteLoginView",
    "SitePasswordResetConfirmView",
    "SitePasswordResetDoneView",
    "SitePasswordResetView",
    "ad_create",
    "ad_delete",
    "ad_detail",
    "ad_edit",
    "ad_status",
    "confirm_signup",
    "page_not_found_view",
    "permission_denied_view",
    "resend_password_reset_email",
    "resend_verification_email",
    "review_delete",
    "server_error_view",
    "signup",
    "signup_check_email",
]
