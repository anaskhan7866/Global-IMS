"""
URL configuration for core_erp project.

Routes:
- /admin/ -> Django Admin Interface
- / -> DashboardHomeView (Financial & MRP Dashboard)
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls', namespace='dashboard')),
    path('', include('inventory.urls')),
]
