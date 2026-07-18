from django.urls import path

from . import django_views

urlpatterns = [
    path("health/", django_views.health, name="health"),
    path("users/", django_views.UserListView.as_view(), name="users"),
]
