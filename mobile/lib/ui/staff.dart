import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';

class StaffPage extends StatefulWidget {
  const StaffPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<StaffPage> createState() => _StaffPageState();
}

class _StaffPageState extends State<StaffPage> {
  late final int _org;
  List<Json> _members = [];
  String _error = '', _query = '';
  bool _archive = false;
  LiftController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    _org = c.organization!;
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      if (c.me['can_manage_access'] == true && !c.demo) {
        final result = await c.request(
          'organizations/members/',
          expectedOrganization: _org,
        );
        if (mounted) setState(() => _members = records(result));
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  Future<void> _invite(Json tech) async {
    try {
      final result = await c.request(
        'organizations/invites/',
        method: 'POST',
        data: {'role': 'MECHANIC', 'technician': tech['id']},
        expectedOrganization: _org,
      );
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Приглашение в организацию'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Для человека, у которого уже есть аккаунт. Код действует 7 дней и используется один раз.',
              ),
              const SizedBox(height: 12),
              SelectableText(result['code'] as String),
            ],
          ),
          actions: [
            TextButton.icon(
              onPressed: () async {
                await Clipboard.setData(
                  ClipboardData(text: result['code'] as String),
                );
                if (context.mounted) Navigator.pop(context);
              },
              icon: const Icon(Icons.copy),
              label: const Text('Копировать'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Закрыть'),
            ),
          ],
        ),
      );
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _create({Json? existing}) async {
    await Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) =>
            EmployeeForm(controller: c, members: _members, existing: existing),
      ),
    );
    await c.refresh();
    await _load();
  }

  Future<void> _access(Json member) async {
    try {
      await c.request(
        'organizations/members/${member['id']}/',
        method: 'PATCH',
        data: {'active': member['active'] != true},
        expectedOrganization: _org,
      );
      await _load();
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  Future<void> _commissioningAccess(Json member, bool value) async {
    try {
      await c.request(
        'organizations/members/${member['id']}/',
        method: 'PATCH',
        data: {'can_commissioning': value},
        expectedOrganization: _org,
      );
      await _load();
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: c,
    organization: _org,
    builder: (context) {
      final manage = c.me['can_manage_access'] == true;
      final people = c
          .list('technicians')
          .where(
            (t) =>
                (_archive || t['active'] == true) &&
                text(
                  t,
                  'full_name',
                ).toLowerCase().contains(_query.toLowerCase()),
          );
      return Scaffold(
        appBar: AppBar(
          title: const Text('Сотрудники'),
          actions: [
            if (manage)
              IconButton(
                tooltip: 'Добавить сотрудника',
                onPressed: c.canWrite ? () => _create() : null,
                icon: const Icon(Icons.person_add_outlined),
              ),
          ],
        ),
        body: RefreshIndicator(
          onRefresh: () async {
            await c.refresh();
            await _load();
          },
          child: ListView(
            padding: const EdgeInsets.all(18),
            children: [
              if (manage)
                FilledButton.icon(
                  onPressed: c.canWrite ? () => _create() : null,
                  icon: const Icon(Icons.person_add_outlined),
                  label: const Text('Добавить сотрудника'),
                ),
              const SizedBox(height: 12),
              TextField(
                decoration: const InputDecoration(
                  labelText: 'Поиск сотрудника',
                  prefixIcon: Icon(Icons.search),
                ),
                onChanged: (v) => setState(() => _query = v),
              ),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Показывать архив'),
                value: _archive,
                onChanged: (v) => setState(() => _archive = v),
              ),
              if (_error.isNotEmpty) Text(_error),
              for (final tech in people) ...[
                Builder(
                  builder: (context) {
                    final member = _members
                        .where((m) => m['technician'] == tech['id'])
                        .firstOrNull;
                    return Sheet(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            text(tech, 'full_name'),
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          Text(
                            member == null
                                ? 'Без учётной записи'
                                : '${roleNames[member['role']] ?? member['role']} · ${member['active'] == true ? 'Доступ включён' : 'Доступ отключён'}',
                          ),
                          Text(
                            '${c.list('elevators').where((e) => e['assigned_technician'] == tech['id'] && e['active'] == true).length} закреплённых лифтов',
                          ),
                          if (tech['active'] != true) const Text('В архиве'),
                          if (manage && c.canWrite && tech['active'] == true)
                            Wrap(
                              spacing: 8,
                              runSpacing: 4,
                              children: [
                                if (member == null)
                                  TextButton(
                                    onPressed: () => _create(existing: tech),
                                    child: const Text('Создать логин'),
                                  ),
                                if (member == null)
                                  TextButton(
                                    onPressed: () => _invite(tech),
                                    child: const Text('Пригласить по коду'),
                                  ),
                                if (member != null &&
                                    member['user'] != c.me['id'])
                                  TextButton(
                                    onPressed: () => _access(member),
                                    child: Text(
                                      member['active'] == true
                                          ? 'Отключить доступ'
                                          : 'Вернуть доступ',
                                    ),
                                  ),
                                if (member?['user'] != c.me['id'])
                                  TextButton(
                                    onPressed: () async {
                                      await archiveRecord(
                                        context,
                                        c,
                                        'technicians',
                                        tech,
                                        _org,
                                      );
                                      await _load();
                                    },
                                    child: const Text('Удалить из списка'),
                                  ),
                              ],
                            ),
                          if (manage && member != null)
                            SwitchListTile(
                              contentPadding: EdgeInsets.zero,
                              title: const Text('Пусконаладка'),
                              value: member['can_commissioning'] == true,
                              onChanged:
                                  c.canWrite && member['user'] != c.me['id']
                                  ? (value) =>
                                        _commissioningAccess(member, value)
                                  : null,
                            ),
                        ],
                      ),
                    );
                  },
                ),
                const SizedBox(height: 12),
              ],
              if (people.isEmpty)
                const EmptyState(
                  'Сотрудники не найдены',
                  'Измените поиск или включите показ архива.',
                ),
              if (manage)
                for (final member in _members.where(
                  (m) => m['role'] == 'ADJUSTER' && m['technician'] == null,
                ))
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Sheet(
                      child: SwitchListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(text(member, 'name')),
                        subtitle: const Text(
                          'Внешний наладчик · доступ к пусконаладке',
                        ),
                        value: member['can_commissioning'] == true,
                        onChanged: c.canWrite
                            ? (value) => _commissioningAccess(member, value)
                            : null,
                      ),
                    ),
                  ),
            ],
          ),
        ),
      );
    },
  );
}

class EmployeeForm extends StatefulWidget {
  const EmployeeForm({
    super.key,
    required this.controller,
    required this.members,
    this.existing,
  });
  final LiftController controller;
  final List<Json> members;
  final Json? existing;
  @override
  State<EmployeeForm> createState() => _EmployeeFormState();
}

class _EmployeeFormState extends State<EmployeeForm> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController(),
      _phone = TextEditingController(),
      _login = TextEditingController(),
      _password = TextEditingController();
  late final int _org;
  int? _existing;
  String _role = 'MECHANIC';
  bool _busy = false, _obscure = true;
  @override
  void initState() {
    super.initState();
    _org = widget.controller.organization!;
    _existing = widget.existing?['id'] as int?;
    _name.text = text(widget.existing ?? {}, 'full_name', '');
    _phone.text = text(widget.existing ?? {}, 'phone', '');
  }

  @override
  void dispose() {
    for (final field in [_name, _phone, _login, _password]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (_busy || !_form.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      await widget.controller.request(
        'organizations/members/create/',
        method: 'POST',
        expectedOrganization: _org,
        data: {
          'full_name': _name.text.trim(),
          'phone': _phone.text.trim(),
          'username': _login.text.trim(),
          'password': _password.text,
          'role': _role,
          'technician': _existing,
        },
      );
      _password.clear();
      await widget.controller.refresh();
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: widget.controller,
    organization: _org,
    builder: (context) {
      final c = widget.controller;
      final cards = c
          .list('technicians')
          .where(
            (t) =>
                t['active'] == true &&
                !widget.members.any((m) => m['technician'] == t['id']),
          );
      return Scaffold(
        appBar: AppBar(title: const Text('Добавить сотрудника')),
        body: Form(
          key: _form,
          child: ListView(
            padding: const EdgeInsets.all(18),
            children: [
              const Text(
                'Сотрудник и доступ к выбранной организации создаются вместе. Для совместительства существующего аккаунта используйте приглашение.',
              ),
              const SizedBox(height: 18),
              DropdownButtonFormField<int>(
                initialValue: _existing ?? 0,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Карточка сотрудника',
                ),
                items: [
                  const DropdownMenuItem(
                    value: 0,
                    child: Text('Новый сотрудник'),
                  ),
                  for (final tech in cards)
                    DropdownMenuItem(
                      value: tech['id'] as int,
                      child: Text(
                        text(tech, 'full_name'),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: _busy
                    ? null
                    : (value) => setState(() {
                        _existing = value == 0 ? null : value;
                        final tech = cards
                            .where((t) => t['id'] == _existing)
                            .firstOrNull;
                        if (tech != null) {
                          _name.text = text(tech, 'full_name');
                          _phone.text = text(tech, 'phone', '');
                        }
                      }),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _name,
                maxLength: 200,
                decoration: const InputDecoration(labelText: 'ФИО сотрудника'),
                validator: (v) =>
                    (v?.trim().length ?? 0) < 2 ? 'Укажите ФИО' : null,
              ),
              const SizedBox(height: 10),
              TextFormField(
                controller: _phone,
                maxLength: 50,
                keyboardType: TextInputType.phone,
                decoration: const InputDecoration(labelText: 'Телефон'),
              ),
              const SizedBox(height: 10),
              TextFormField(
                controller: _login,
                maxLength: 150,
                autocorrect: false,
                enableSuggestions: false,
                decoration: const InputDecoration(labelText: 'Логин'),
                validator: (v) =>
                    (v?.trim().length ?? 0) < 3 ? 'Минимум 3 символа' : null,
              ),
              const SizedBox(height: 10),
              TextFormField(
                controller: _password,
                obscureText: _obscure,
                autocorrect: false,
                enableSuggestions: false,
                decoration: InputDecoration(
                  labelText: 'Пароль (от 12 символов)',
                  suffixIcon: IconButton(
                    onPressed: () => setState(() => _obscure = !_obscure),
                    icon: Icon(
                      _obscure
                          ? Icons.visibility_outlined
                          : Icons.visibility_off_outlined,
                    ),
                  ),
                ),
                validator: (v) =>
                    (v?.length ?? 0) < 12 ? 'Минимум 12 символов' : null,
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<String>(
                initialValue: _role,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Роль в организации',
                ),
                items: [
                  for (final entry in roleNames.entries)
                    if (entry.key != 'ADMIN' || c.me['role'] == 'ADMIN')
                      DropdownMenuItem(
                        value: entry.key,
                        child: Text(entry.value),
                      ),
                ],
                onChanged: _busy ? null : (v) => setState(() => _role = v!),
              ),
              const SizedBox(height: 24),
              FilledButton(
                onPressed:
                    c.canWrite && !_busy && c.me['can_manage_access'] == true
                    ? _save
                    : null,
                child: Text(
                  _busy ? 'Создание…' : 'Создать сотрудника и доступ',
                ),
              ),
            ],
          ),
        ),
      );
    },
  );
}

Future<bool> archiveRecord(
  BuildContext context,
  LiftController c,
  String type,
  Json item,
  int organization,
) async {
  final reason = TextEditingController();
  int? replacement;
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => StatefulBuilder(
      builder: (context, setState) => AlertDialog(
        title: Text(
          type == 'technicians'
              ? 'Удалить сотрудника из списка?'
              : 'Удалить лифт из списка?',
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'Запись перейдёт в архив. История заявок и работ сохранится.',
              ),
              const SizedBox(height: 12),
              TextField(
                controller: reason,
                minLines: 2,
                maxLines: 5,
                maxLength: 1000,
                decoration: const InputDecoration(
                  labelText: 'Причина (от 5 символов)',
                ),
                onChanged: (_) => setState(() {}),
              ),
              if (type == 'technicians')
                DropdownButtonFormField<int>(
                  isExpanded: true,
                  decoration: const InputDecoration(
                    labelText: 'Передать лифты и будущие смены',
                  ),
                  items: [
                    const DropdownMenuItem(
                      value: 0,
                      child: Text('Нет назначений'),
                    ),
                    for (final tech in c.list('technicians'))
                      if (tech['active'] == true && tech['id'] != item['id'])
                        DropdownMenuItem(
                          value: tech['id'] as int,
                          child: Text(text(tech, 'full_name')),
                        ),
                  ],
                  onChanged: (v) => replacement = v == 0 ? null : v,
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: reason.text.trim().length >= 5
                ? () => Navigator.pop(context, true)
                : null,
            child: const Text('В архив'),
          ),
        ],
      ),
    ),
  );
  final textReason = reason.text.trim();
  reason.dispose();
  if (confirmed != true) return false;
  try {
    await c.request(
      '$type/${item['id']}/archive/',
      method: 'POST',
      expectedOrganization: organization,
      data: {'reason': textReason, 'replacement': replacement},
    );
    await c.refresh();
    return true;
  } catch (e) {
    if (context.mounted) showError(context, e);
    return false;
  }
}
