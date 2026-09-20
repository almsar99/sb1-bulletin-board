from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView, PasswordResetCompleteView
from django.urls import path
from django.views.generic import TemplateView

from web import views

app_name = "web"

urlpatterns = [
    path("about/", TemplateView.as_view(template_name="pages/about.html"), name="about"),
    path("", views.AdListView.as_view(), name="ad-list"),
    path("discussions/", views.DiscussionListView.as_view(), name="discussions"),
    path("category/<slug:category>/", views.AdListView.as_view(), name="category"),
    path("ad/new/", views.ad_create, name="ad-create"),
    path("ad/<int:pk>/", views.ad_detail, name="ad-detail"),
    path("ad/<int:pk>/status/", views.ad_status, name="ad-status"),
    path("ad/<int:pk>/edit/", views.ad_edit, name="ad-edit"),
    path("ad/<int:pk>/delete/", views.ad_delete, name="ad-delete"),
    path("review/<int:pk>/delete/", views.review_delete, name="review-delete"),
    path("login/", views.SiteLoginView.as_view(), name="login"),
    path("signup/", views.signup, name="signup"),
    path("signup/check-email/", views.signup_check_email, name="signup-check-email"),
    path(
        "signup/resend-verification/",
        views.resend_verification_email,
        name="resend-verification",
    ),
    path(
        "signup/confirm/<str:token>/",
        views.confirm_signup,
        name="signup-confirm",
    ),
    path("logout/", login_required(LogoutView.as_view()), name="logout"),
    path("account/review/", views.ReviewQueueView.as_view(), name="review-queue"),
    path("account/", views.AccountView.as_view(), name="account"),
    path("password-reset/", views.SitePasswordResetView.as_view(), name="password-reset"),
    path(
        "password-reset/done/",
        views.SitePasswordResetDoneView.as_view(),
        name="password-reset-done",
    ),
    path("password-reset/resend/", views.resend_password_reset_email, name="password-reset-resend"),
    path(
        "password-reset/complete/",
        PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password-reset-complete",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        views.SitePasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
]
