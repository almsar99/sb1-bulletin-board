from django.urls import path

from messaging import views

app_name = "messaging"
urlpatterns = [
    path("messages/", views.thread_list, name="list"),
    path("messages/<int:pk>/", views.detail, name="detail"),
    path("ad/<int:pk>/message/", views.start, name="start"),
]
