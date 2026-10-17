from django.urls import path
from . import views
urlpatterns=[
path('',views.home,name='home'),path('login/',views.sign_in,name='login'),path('logout/',views.sign_out,name='logout'),path('password/',views.password_change,name='password'),
path('tasks/',views.task_list,name='tasks'),path('tasks/new/',views.task_create,name='task_create'),path('tasks/<int:pk>/',views.task_detail,name='task_detail'),path('tasks/<int:pk>/edit/',views.task_edit,name='task_edit'),
path('task-reviews/',views.task_reviews,name='task_reviews'),
path('tasks/<int:pk>/delete/',views.task_remove,name='task_delete'),
path('tasks/<int:pk>/decision/',views.task_decision,name='task_decision'),
path('points/',views.points,name='points'),
path('wallet/',views.wallet,name='wallet'),path('records/',views.records,name='records'),path('analysis/',views.analysis,name='analysis'),
path('manage/',views.management,name='management'),path('manage/<str:section>/',views.management,name='manage_section'),path('benefits/',views.benefits,name='benefits'),
path('assets/<uuid:pk>/',views.asset,name='asset'),path('notifications/<int:pk>/ack/',views.acknowledge,name='acknowledge'),path('motion/',views.motion,name='motion'),path('site-settings/',views.site_settings,name='site_settings'),
path('oidc/login/',views.oidc_login,name='oidc_login'),path('oidc/callback/',views.oidc_callback,name='oidc_callback')]
