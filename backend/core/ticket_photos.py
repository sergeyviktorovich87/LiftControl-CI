from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError
from .access import org
from .models import Ticket, TicketPhoto, Organization
from .ticket_flow import can_finish_ticket
from .media_vault import atomic_media, ingest, link_asset


def save_photo(user, ticket, upload):
    if not can_finish_ticket(user,ticket):
        raise PermissionDenied('Добавлять фотографии может только исполнитель заявки в работе.')
    with atomic_media():
        Organization.objects.select_for_update(no_key=True).get(pk=org(user).pk)
        locked = Ticket.objects.select_for_update(no_key=True).get(pk=ticket.pk)
        if not can_finish_ticket(user,locked):
            raise PermissionDenied('Заявка уже закрыта или переназначена.')
        if locked.photos.filter(deleted_at__isnull=True).count() >= 8:
            raise ValidationError('В заявке допускается до 8 фотографий.')
        asset = ingest(user,upload,video=False)
        photo = TicketPhoto.objects.create(organization=org(user),ticket=locked,uploaded_by=user,asset=asset,file=asset.file.name)
        link_asset(user,asset,'ticket',locked,'RESULT',check=False)
        from .ticket_flow import add_event
        add_event(locked,user,'photo_added',before=locked.status,after=locked.status)
    return photo
