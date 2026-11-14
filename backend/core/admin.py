from django.contrib import admin
from django import forms
from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserCreationForm
from .models import Building, Elevator, Maintenance, Technician, Ticket, Membership, Organization, AuditEvent

@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ('address', 'name', 'management_company')
    search_fields = ('address', 'name', 'management_company')

@admin.register(Elevator)
class ElevatorAdmin(admin.ModelAdmin):
    list_display = ('factory_number', 'building', 'entrance', 'manufacturer', 'model', 'assigned_technician', 'active')
    list_filter = ('active', 'elevator_type', 'manufacturer')
    search_fields = ('factory_number', 'building__address', 'model', 'manufacturer')

@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'organization', 'linked_login', 'role', 'phone', 'active')
    list_filter = ('organization', 'role', 'active')
    @admin.display(description='Логин')
    def linked_login(self, obj):
        linked = Membership.objects.filter(technician=obj).select_related('user').first()
        return linked.user.username if linked else 'Нет учётной записи'
    search_fields = ('full_name', 'phone')

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    # The admin must not bypass append-only audited ticket state transitions.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
    list_display = ('id', 'elevator', 'title', 'priority', 'status', 'assigned_to', 'created_at')
    list_filter = ('priority', 'status')
    search_fields = ('title', 'description', 'elevator__factory_number', 'elevator__building__address')

@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    list_display = ('planned_date', 'kind', 'elevator', 'technician', 'completed_at')
    list_filter = ('kind', 'planned_date')


from .models import DutyShift, PartRequest, ChatMessage, Document, TicketPhoto, TicketEvent, TicketComment, PushSubscription, PushDelivery
admin.site.register(Organization)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    fields = ('organization', 'role', 'technician', 'active')
    verbose_name = 'Доступ к организации'
    verbose_name_plural = 'Доступ к организациям'


class EmployeeUserCreationForm(UserCreationForm):
    organization = forms.ModelChoiceField(queryset=Organization.objects.all(), label='Организация')
    role = forms.ChoiceField(choices=Membership.Role.choices, label='Роль в организации')
    technician = forms.ModelChoiceField(
        queryset=Technician.objects.all(), required=False,
        label='Существующая карточка сотрудника',
        help_text='Для сотрудников из импорта. Если не выбрана, новая карточка создаётся автоматически.',
    )
    first_name = forms.CharField(max_length=150, label='Имя')
    last_name = forms.CharField(max_length=150, label='Фамилия')
    phone = forms.CharField(max_length=50, required=False, label='Телефон')

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('username', 'first_name', 'last_name', 'email')

    def clean(self):
        data = super().clean()
        organization = data.get('organization')
        technician = data.get('technician')
        if technician and organization and technician.organization_id != organization.pk:
            self.add_error('technician', 'Карточка принадлежит другой организации.')
        if technician and Membership.objects.filter(technician=technician).exists():
            self.add_error('technician', 'Эта карточка уже связана с учётной записью.')
        return data


class LiftUserAdmin(DjangoUserAdmin):
    add_form = EmployeeUserCreationForm
    add_fieldsets = (
        (None, {'fields': (
            'username', 'password1', 'password2', 'first_name', 'last_name',
            'email', 'organization', 'role', 'technician', 'phone',
        )}),
    )
    inlines = (MembershipInline,)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change:
            return
        organization = form.cleaned_data['organization']
        selected = form.cleaned_data.get('technician')
        requested_role = form.cleaned_data['role']
        if selected is None:
            staff_role = {
                'MECHANIC': Technician.Role.MECHANIC,
                'DISPATCHER': Technician.Role.DISPATCHER,
            }.get(requested_role, Technician.Role.ENGINEER)
            selected = Technician.objects.create(
                organization=organization,
                full_name=f'{obj.last_name} {obj.first_name}'.strip(),
                phone=form.cleaned_data.get('phone', ''),
                role=staff_role,
            )
        member = Membership(user=obj, organization=organization,
                            technician=selected, role=requested_role)
        member.full_clean()
        member.save()
        AuditEvent.objects.create(
            organization=organization, actor=request.user,
            action='organization.employee_created', object_id=str(member.pk),
            details={'user': obj.pk, 'technician': selected.pk, 'role': requested_role},
        )


User = get_user_model()
admin.site.unregister(User)
admin.site.register(User, LiftUserAdmin)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'role', 'technician', 'active')
    list_filter = ('organization', 'role', 'active')
    search_fields = ('user__username', 'technician__full_name')


class HistoryAdmin(admin.ModelAdmin):
    def has_add_permission(self,request):
        return False
    def has_change_permission(self,request,obj=None):
        return False
    def has_delete_permission(self,request,obj=None):
        return False

for model in (DutyShift, PartRequest, ChatMessage, Document, TicketPhoto, AuditEvent, TicketEvent, TicketComment, PushDelivery):
    admin.site.register(model,HistoryAdmin)
