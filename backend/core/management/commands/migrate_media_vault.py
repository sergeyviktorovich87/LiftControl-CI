"""Copy legacy media without changing legacy file names or deleting originals."""
import json
from rest_framework.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from core.models import TicketPhoto, ChatAttachment, PersonalTaskAttachment
from core.media_vault import atomic_media, ingest, link_asset


class Command(BaseCommand):
    help = 'Register and copy existing attachments into the private media vault; rerunnable.'
    def add_arguments(self, parser):
        parser.add_argument('--check-only',action='store_true')

    def handle(self,*args,**options):
        count, missing = 0, 0
        for model,kind,parent,author in [(TicketPhoto,'ticket','ticket','uploaded_by'),(ChatAttachment,'message','message',None),(PersonalTaskAttachment,'task','task',None)]:
            for row in model.objects.select_related(parent,'organization').filter(asset__isnull=True).iterator(chunk_size=100):
                if options['check_only']:
                    count += 1
                    if not row.file.storage.exists(row.file.name): missing += 1
                    continue
                try:
                    with row.file.open('rb') as file:
                        data = file.read(20*1024**2+1)
                    owner = getattr(row,author) if author else row.message.author if kind=='message' else row.task.created_by
                    context = getattr(row,parent)
                    with atomic_media():
                        mime = getattr(row,'content_type','image/jpeg')
                        asset = ingest(owner,SimpleUploadedFile(row.file.name,data,content_type=mime),organization=row.organization,created_at=row.created_at,validate=True)
                        row.asset = asset
                        # The old file path is retained for rollback and old clients.
                        row.save(update_fields=['asset'])
                        from core.models import MediaLink, AuditEvent
                        MediaLink.objects.get_or_create(asset=asset,**{kind:context},defaults={'created_by':owner,'created_at':row.created_at,'category':'RESULT' if kind in ('ticket','task') else 'OTHER'})
                        AuditEvent.objects.create(organization=row.organization,actor=owner,action='media.migrate',object_id=str(asset.pk),details={'source':kind,'id':row.pk})
                    count += 1
                except (OSError,ValueError,ValidationError) as exc:
                    missing += 1
                    self.stderr.write(f'{model.__name__}:{row.pk}: {type(exc).__name__}')
        self.stdout.write(json.dumps({'registered_or_pending':count,'unavailable':missing,'check_only':options['check_only']}))
        if missing:
            raise CommandError('Some existing files could not be verified. Originals are retained; resolve before switching storage.')
