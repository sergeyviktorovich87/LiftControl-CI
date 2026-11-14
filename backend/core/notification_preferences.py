"""A person's choices are scoped to one employer and checked again before delivery."""
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .access import MemberAccess, org
from .models import NotificationPreference

FIELDS = ('ordinary','urgent','entrapment','updates','tasks')


def category(priority, kind='created'):
    if kind not in ('created','reassigned'):
        return 'updates'
    return 'entrapment' if priority == 'ENTRAPMENT' else 'urgent' if priority in ('HIGH','EMERGENCY') else 'ordinary'


def allows(user_id, organization_id, group):
    row = NotificationPreference.objects.filter(user_id=user_id, organization_id=organization_id).first()
    return row is None or bool(getattr(row, group))


class PreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = FIELDS


@api_view(['GET','PATCH'])
@permission_classes([MemberAccess])
def preferences(request):
    organization = org(request.user)
    if request.method == 'GET':
        row = NotificationPreference.objects.filter(user=request.user, organization=organization).first()
        return Response(PreferenceSerializer(row).data if row else {k:True for k in FIELDS})
    serializer = PreferenceSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    row, _ = NotificationPreference.objects.update_or_create(user=request.user, organization=organization,
        defaults=serializer.validated_data)
    return Response(PreferenceSerializer(row).data)
