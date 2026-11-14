"""Landscape technical-maintenance journal for a single authorized lift."""
import io
import logging
import os
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .access import MemberAccess, org
from .models import Elevator, Maintenance

logger = logging.getLogger(__name__)
_FONT = 'LiftControl-DejaVu'
_FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def _font():
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(os.getenv('LIFTCONTROL_PDF_FONT', _FONT_PATH))
        if not font_path.is_file():
            raise RuntimeError('Cyrillic PDF font is not installed on the server')
        pdfmetrics.registerFont(TTFont(_FONT, str(font_path)))


def _date(value, key):
    if not value:
        return None
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({key: 'Ожидается дата ГГГГ-ММ-ДД.'})


def _paragraph(value, style):
    return Paragraph(escape(str(value or '—')).replace('\n', '<br/>'), style)


def _pdf(lift, rows, organization, start, end):
    _font()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), topMargin=15*mm, bottomMargin=16*mm,
        leftMargin=12*mm, rightMargin=12*mm,
        title='Журнал технического обслуживания лифта',
        author='LiftControl',
    )
    width = doc.width - 12
    title_style = ParagraphStyle('LCTitle',fontName=_FONT,fontSize=14,leading=18,alignment=TA_CENTER,spaceAfter=9)
    normal = ParagraphStyle('LCText',fontName=_FONT,fontSize=8,leading=11,alignment=TA_LEFT,spaceAfter=3)
    head = ParagraphStyle('LCHead',parent=normal,fontSize=7.5,leading=10,alignment=TA_CENTER)
    cells = ParagraphStyle('LCCell',parent=normal,fontSize=7.5,leading=10,spaceAfter=0)
    cover_text = ParagraphStyle('LCCover', parent=normal, fontSize=11, leading=16)
    cover = Table([
        [_paragraph('Специализированная организация', cover_text), _paragraph(organization.name, cover_text)],
        [_paragraph('Адрес объекта', cover_text), _paragraph(lift.building.address, cover_text)],
        [_paragraph('Наименование и тип объекта', cover_text),
         _paragraph(' · '.join(filter(None, ('Лифт', lift.get_elevator_type_display(), lift.model))), cover_text)],
        [_paragraph('Заводской номер объекта', cover_text), _paragraph(lift.factory_number, cover_text)],
    ], colWidths=[width * .36, width * .64], hAlign='CENTER')
    cover.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (1, 0), (1, -1), .5, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story = [
        Spacer(1, 19*mm),
        _paragraph('Журнал технического обслуживания лифта', title_style),
        Spacer(1, 16*mm),
        cover,
        Spacer(1, 9*mm),
    ]
    if start or end:
        period = ' — '.join(d.strftime('%d.%m.%Y') if d else '…' for d in (start, end))
        story.append(_paragraph(f'Период: {period}', cover_text))
    story.extend([
        PageBreak(),
        _paragraph(f'{lift.building.address} · Лифт {lift.display_number}', normal),
        Spacer(1, 3*mm),
    ])
    headers = ('№ п/п', 'Вид проведённой работы', 'Дата проведения работы',
               'Ф.И.О., должность лица, выполнившего работы',
               'Подпись лица, выполнившего работы',
               'Подпись лица, ответственного за организацию обслуживания и ремонт объекта')
    data = [[_paragraph(x,head) for x in headers],
            [_paragraph(i, head) for i in range(1, 7)]]
    for number, work in enumerate(rows,1):
        tech=work.technician
        tech_name=(tech.full_name+' / '+tech.get_role_display()) if tech else 'Не указан'
        local=timezone.localtime(work.completed_at,ZoneInfo(organization.timezone)).strftime('%d.%m.%Y')
        description = '\n'.join(filter(None, (work.get_kind_display(), work.notes)))
        data.append([
            _paragraph(number,cells),_paragraph(description,cells),
            _paragraph(local,cells),_paragraph(tech_name,cells),'','',
        ])
    if not rows:
        data.extend([[''] * 6 for _ in range(10)])
    proportions = (.052, .234, .132, .238, .144, .2)
    col_widths=[width*p for p in proportions]
    table=Table(data,colWidths=col_widths,repeatRows=2,splitInRow=1,hAlign='LEFT')
    table.setStyle(TableStyle([
        ('GRID',(0,0),(-1,-1),0.4,colors.black),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('TOPPADDING',(0,0),(-1,-1),7),
        ('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('LEFTPADDING',(0,0),(-1,-1),5),
        ('RIGHTPADDING',(0,0),(-1,-1),5),
    ]))
    story.append(table)
    remarks = Table([[_paragraph('Для дополнительных замечаний', normal)], [''], ['']],
                    colWidths=[width], rowHeights=[6*mm, 8*mm, 8*mm], hAlign='LEFT')
    remarks.setStyle(TableStyle([('LINEBELOW', (0, 1), (0, -1), .4, colors.black)]))
    story.extend([Spacer(1, 6*mm), remarks])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(_FONT,7.5)
        canvas.drawRightString(landscape(A4)[0]-12*mm,8*mm,str(document.page))
        canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()


@api_view(['GET'])
@permission_classes([MemberAccess])
def maintenance_pdf(request, elevator_id):
    organization = org(request.user)
    lift = get_object_or_404(Elevator.objects.select_related('building'),
                             pk=elevator_id, organization=organization)
    start=_date(request.query_params.get('from'), 'from')
    end=_date(request.query_params.get('to'), 'to')
    if start and end and end < start:
        raise ValidationError({'to': 'Дата окончания раньше даты начала.'})
    local_zone = ZoneInfo(organization.timezone)
    qs=Maintenance.objects.filter(organization=organization, elevator=lift,
                                   completed_at__isnull=False).select_related('technician')
    if start:
        qs=qs.filter(completed_at__gte=datetime.combine(start, time.min, tzinfo=local_zone))
    if end and end < date.max:
        qs=qs.filter(completed_at__lt=datetime.combine(end + timedelta(days=1), time.min, tzinfo=local_zone))
    # Do not let an unbounded export exhaust worker RAM.
    rows=list(qs.order_by('completed_at','id')[:1001])
    if len(rows)>1000:
        raise ValidationError('Слишком много записей. Выберите более короткий период.')
    content=_pdf(lift,rows,organization,start,end)
    response=HttpResponse(content,content_type='application/pdf')
    response['Cache-Control']='private, no-store'
    response['Content-Disposition']=f'attachment; filename="LiftControl-TO-{lift.pk}.pdf"'
    response['X-Content-Type-Options']='nosniff'
    logger.info('maintenance_pdf_generated organization=%s elevator=%s rows=%s',
                organization.pk,lift.pk,len(rows))
    return response
