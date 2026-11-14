from django.urls import include,path
from rest_framework.routers import DefaultRouter
from . import views, organizations, mobile_auth, native_push, updates
from . import notification_preferences, mobile_sync, self_push
from .work_orders import TaskViewSet
router = DefaultRouter()
router.register('tasks',TaskViewSet)
mobile_urls = [
    path('mobile/sync/', mobile_sync.synchronize),
    path('mobile/push/', native_push.device),
    path('mobile/push/self/', self_push.feed),
    path('mobile/login/', mobile_auth.MobileLogin.as_view()),
    path('mobile/logout/', mobile_auth.MobileLogout.as_view()),
    path('mobile/sessions/', mobile_auth.MobileSessions.as_view()),
    path('mobile/sessions/<int:session_id>/revoke/', mobile_auth.MobileRevoke.as_view()),
]
for name,view in [('buildings',views.BuildingViewSet),('elevators',views.ElevatorViewSet),('technicians',views.TechnicianViewSet),('tickets',views.TicketViewSet),('maintenance',views.MaintenanceViewSet),('duty',views.DutyViewSet),('duty-swaps',views.DutySwapViewSet),('parts',views.PartViewSet),('chat',views.ChatViewSet),('documents',views.DocumentViewSet)]:
    router.register(name,view)
urlpatterns = [path('organizations/',organizations.organizations),path('organizations/switch/',organizations.switch_organization),path('organizations/join/',organizations.join_organization),path('organizations/invites/',organizations.organization_invites),path('organizations/invites/<int:invite_id>/revoke/',organizations.revoke_invite),path('organizations/members/',organizations.organization_members),path('organizations/members/create/',organizations.create_employee_account),path('organizations/members/<int:member_id>/',organizations.update_member),path('push/public-key/',views.push_public_key),path('push/subscription/',views.push_subscription),path('health/',views.health),path('me/',views.me),path('logout/',views.sign_out),path('dashboard/',views.dashboard),path('audit/',views.audit_events),path('',include(router.urls))]
urlpatterns = mobile_urls + urlpatterns
urlpatterns += [path('notification-preferences/',notification_preferences.preferences)]
urlpatterns += [path('updates/', updates.catalogue),
                path('updates/<str:kind>/download/', updates.download_release)]
from . import media_api
from . import maintenance_pdf
urlpatterns += [path('maintenance/<int:elevator_id>/journal/pdf/', maintenance_pdf.maintenance_pdf)]
urlpatterns += [path('media/',media_api.catalogue),path('media/stats/',media_api.statistics),
    path('media/uploads/',media_api.upload),path('media/<uuid:asset_id>/links/',media_api.attach),
    path('media/<uuid:asset_id>/<str:variant>/',media_api.download)]


from . import commissioning, notification_inbox
urlpatterns += [
    path('notifications/', notification_inbox.inbox),
    path('notifications/<int:notification_id>/read/', notification_inbox.mark_read),
]
urlpatterns += [
    path('commissioning/', commissioning.projects),
    path('commissioning/<int:pk>/', commissioning.project),
    path('commissioning/<int:pk>/lifts/', commissioning.add_lift),
    path('commissioning/<int:pk>/lifts/<int:lift_id>/assign/', commissioning.assign),
    path('commissioning/<int:pk>/lifts/<int:lift_id>/progress/', commissioning.update_progress),
    path('commissioning/<int:pk>/lift-requests/', commissioning.request_lift),
    path('commissioning/<int:pk>/lift-requests/<int:request_id>/decision/', commissioning.decide_lift),
    path('commissioning/<int:pk>/expenses/', commissioning.add_expense),
    path('commissioning/<int:pk>/expenses/<int:expense_id>/receipt/', commissioning.receipt),
    path('commissioning/<int:pk>/expenses/<int:expense_id>/upload/', commissioning.upload_receipt),
    path('commissioning/<int:pk>/expenses/<int:expense_id>/reimburse/', commissioning.reimburse),
    path('commissioning/<int:pk>/needs/', commissioning.add_need),
    path('commissioning/<int:pk>/needs/<int:need_id>/resolve/', commissioning.resolve_need),
    path('commissioning/<int:pk>/extra-work/', commissioning.add_extra),
    path('commissioning/<int:pk>/extra-work/<int:extra_id>/decision/', commissioning.decide_extra),
    path('commissioning/<int:pk>/messages/', commissioning.send_message),
    path('commissioning/<int:pk>/archive/', commissioning.archive_project),
]
