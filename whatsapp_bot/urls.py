"""
URL configuration for whatsapp_bot project.
"""
from django.contrib import admin
from django.urls import path, include
from core import views

urlpatterns = [
    path('', views.root, name='root'),
    path('admin/', admin.site.urls),
    path('api/', include('core.urls')),
]
