"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from catalog.admin_views import sync_status_view
from catalog.views import (
    CatalogLoginView,
    CatalogLogoutView,
    image_recipe_planner_image_view,
    image_recipe_planner_stream_view,
    image_recipe_planner_view,
    nutrition_search_view,
    profile_view,
    product_search_api,
    recipe_planner_image_view,
    recipe_planner_stream_view,
    recipe_planner_view,
    shopping_list_view,
)

urlpatterns = [
    path("", nutrition_search_view, name="nutrition_search"),
    path("login/", CatalogLoginView.as_view(), name="login"),
    path("logout/", CatalogLogoutView.as_view(), name="logout"),
    path("profile/", profile_view, name="profile"),
    path("image-recipe-planner/", image_recipe_planner_view, name="image_recipe_planner"),
    path("image-recipe-planner/image/", image_recipe_planner_image_view, name="image_recipe_planner_image"),
    path("image-recipe-planner/stream/", image_recipe_planner_stream_view, name="image_recipe_planner_stream"),
    path("recipe-planner/", recipe_planner_view, name="recipe_planner"),
    path("recipe-planner/image/", recipe_planner_image_view, name="recipe_planner_image"),
    path("recipe-planner/stream/", recipe_planner_stream_view, name="recipe_planner_stream"),
    path("shopping-list/", shopping_list_view, name="shopping_list"),
    path("api/products/search/", product_search_api, name="product_search_api"),
    path("admin/sync-status/", admin.site.admin_view(sync_status_view), name="admin_sync_status"),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
