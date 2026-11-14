import 'dart:async';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';
import 'sync_queue.dart';

class CommissioningPage extends StatefulWidget {
  const CommissioningPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<CommissioningPage> createState() => _CommissioningPageState();
}

class _CommissioningPageState extends State<CommissioningPage> {
  List<Json> _projects = [];
  Json? _detail;
  List<Json> _members = [];
  bool _busy = false;
  String _error = '';
  LiftController get c => widget.controller;
  int get org => c.organization!;
  bool get canManage => c.me['can_manage_access'] == true;
  int? _readGeneration;

  @override
  void initState() {
    super.initState();
    c.addListener(_afterRefresh);
    unawaited(_load());
  }

  @override
  void dispose() {
    c.removeListener(_afterRefresh);
    super.dispose();
  }

  void _afterRefresh() {
    if (!c.online || c.refreshing || _busy || _readGeneration == c.syncRevision)
      return;
    _readGeneration = c.syncRevision;
    unawaited(_load());
  }

  Future<void> _load() async {
    if (!mounted || c.organization == null) return;
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      final rows = records(
        await c.resource('commissioning/', expectedOrganization: org),
      );
      final selected = _detail?['id'];
      final data = selected == null
          ? null
          : await c.resource(
              'commissioning/$selected/',
              expectedOrganization: org,
            );
      List<Json> members = [];
      if (canManage) {
        try {
          members =
              records(
                    await c.request(
                      'organizations/members/',
                      expectedOrganization: org,
                    ),
                  )
                  .where(
                    (m) =>
                        m['active'] == true &&
                        m['can_commissioning'] == true &&
                        ['ADJUSTER', 'MECHANIC'].contains(m['role']),
                  )
                  .toList();
        } catch (_) {
          c.log.record('commissioning_member_list_failed');
        }
      }
      if (!mounted) return;
      setState(() {
        _projects = rows;
        _detail = data is Map ? Map<String, dynamic>.from(data) : null;
        _members = members;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
      c.log.record('commissioning_load_failed');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _open(int id) async {
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      final result = await c.resource(
        'commissioning/$id/',
        expectedOrganization: org,
      );
      if (mounted)
        setState(() => _detail = Map<String, dynamic>.from(result as Map));
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _post(String path, Json body) async {
    if (!c.canWrite || _busy) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Для изменения нужна связь с сервером.'),
          ),
        );
      }
      return;
    }
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      await c.request(
        path,
        method: 'POST',
        data: body,
        expectedOrganization: org,
      );
      if (!mounted) return;
      setState(() => _busy = false);
      await _load();
    } catch (e) {
      c.log.record('commissioning_mutation_failed');
      if (mounted)
        setState(() {
          _error = '$e';
          _busy = false;
        });
    }
  }

  Future<Json?> _form(
    String title,
    Map<String, String> labels, {
    Map<String, String> defaults = const {},
    bool expense = false,
  }) async {
    final inputs = {
      for (final entry in labels.entries)
        entry.key: TextEditingController(text: defaults[entry.key] ?? ''),
    };
    final formKey = GlobalKey<FormState>();
    XFile? receipt;
    int? expenseLift;
    DateTime spent = c.orgNow;
    final result = await showDialog<Json>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, update) => AlertDialog(
          title: Text(title),
          content: Form(
            key: formKey,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (final entry in labels.entries)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: TextFormField(
                        controller: inputs[entry.key],
                        maxLength: entry.key == 'description' ? 2000 : 300,
                        keyboardType: ['amount', 'price'].contains(entry.key)
                            ? const TextInputType.numberWithOptions(
                                decimal: true,
                              )
                            : TextInputType.text,
                        decoration: InputDecoration(labelText: entry.value),
                        validator: (value) =>
                            value == null || value.trim().isEmpty
                            ? 'Заполните поле'
                            : null,
                      ),
                    ),
                  if (expense) ...[
                    DropdownButtonFormField<int>(
                      initialValue: expenseLift,
                      decoration: const InputDecoration(labelText: 'Лифт'),
                      items: [
                        const DropdownMenuItem(
                          value: 0,
                          child: Text('Весь объект'),
                        ),
                        for (final l in records(
                          _detail!['lifts'],
                        ).where((l) => l['can_edit'] == true))
                          DropdownMenuItem(
                            value: l['id'] as int,
                            child: Text(text(l, 'label')),
                          ),
                      ],
                      onChanged: (value) => update(() => expenseLift = value),
                    ),
                    TextButton.icon(
                      icon: const Icon(Icons.event),
                      label: Text(
                        'Дата • ${dateLabel(spent.toIso8601String().substring(0, 10), 0)}',
                      ),
                      onPressed: () async {
                        final date = await showDatePicker(
                          context: ctx,
                          initialDate: spent,
                          firstDate: DateTime(2000),
                          lastDate: DateTime(2100),
                        );
                        if (date != null && ctx.mounted)
                          update(() => spent = date);
                      },
                    ),
                    TextButton.icon(
                      icon: const Icon(Icons.camera_alt_outlined),
                      label: Text(
                        receipt == null
                            ? 'Прикрепить фото чека'
                            : 'Заменить фото чека',
                      ),
                      onPressed: () async {
                        try {
                          final photo = await _pickReceipt(ctx);
                          if (photo != null && ctx.mounted)
                            update(() => receipt = photo);
                        } catch (error) {
                          if (ctx.mounted) showError(ctx, error);
                        }
                      },
                    ),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed: () {
                if (formKey.currentState!.validate()) {
                  Navigator.pop(ctx, <String, dynamic>{
                    for (final entry in inputs.entries)
                      entry.key: entry.value.text.trim(),
                    if (expenseLift != null && expenseLift != 0)
                      'lift': expenseLift,
                    if (expense)
                      'spent_on': spent.toIso8601String().substring(0, 10),
                    'receipt': ?receipt,
                  });
                }
              },
              child: const Text('Сохранить'),
            ),
          ],
        ),
      ),
    );
    for (final controller in inputs.values) {
      controller.dispose();
    }
    return result;
  }

  Future<void> _createProject() async {
    final body = await _form('Новый объект наладки', {
      'name': 'Название объекта',
      'address': 'Адрес',
    });
    if (body != null) await _post('commissioning/', body);
  }

  Future<void> _addLift() async {
    final p = _detail!;
    final body = await _form('Добавить лифт', {'label': 'Лифт / номер'});
    if (body != null) await _post('commissioning/${p['id']}/lifts/', body);
  }

  Future<void> _assign(Json lift) async {
    int? value;
    final chosen = await showDialog<int>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, update) => AlertDialog(
          title: Text('Исполнитель: ${lift['label']}'),
          content: DropdownButtonFormField<int>(
            value: value,
            items: [
              for (final member in _members)
                DropdownMenuItem(
                  value: member['user'] as int,
                  child: Text(text(member, 'name')),
                ),
            ],
            onChanged: (v) => update(() => value = v),
            decoration: const InputDecoration(labelText: 'Механик / наладчик'),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed: value == null ? null : () => Navigator.pop(ctx, value),
              child: const Text('Назначить'),
            ),
          ],
        ),
      ),
    );
    if (chosen != null) {
      await _post(
        'commissioning/${_detail!['id']}/lifts/${lift['id']}/assign/',
        {'user': chosen},
      );
    }
  }

  Future<void> _progress(Json lift) async {
    final project = _detail!['id'] as int;
    var value = (lift['progress'] as num).toDouble();
    final note = TextEditingController();
    final result = await showDialog<Json>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, update) => AlertDialog(
          title: Text('Готовность: ${lift['label']}'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('${value.round()}%'),
              Slider(
                min: 0,
                max: 100,
                divisions: 100,
                value: value,
                onChanged: (v) => update(() => value = v),
              ),
              TextField(
                controller: note,
                maxLines: 3,
                maxLength: 4000,
                onChanged: (_) => update(() {}),
                decoration: const InputDecoration(
                  labelText: 'Что сделано (обязательно)',
                ),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed:
                  value.round() == lift['progress'] || note.text.trim().isEmpty
                  ? null
                  : () => Navigator.pop(ctx, {
                      'revision': lift['revision'],
                      'progress': value.round(),
                      'description': note.text.trim(),
                    }),
              child: const Text('Зафиксировать'),
            ),
          ],
        ),
      ),
    );
    note.dispose();
    if (result != null) {
      if (c.canRecordCommissioning) {
        await _queue(project, 'progress', {...result, 'lift': lift['id']});
      } else {
        await _post(
          'commissioning/$project/lifts/${lift['id']}/progress/',
          result,
        );
      }
    }
  }

  Future<void> _addExpense() async {
    final project = _detail!['id'] as int;
    final body = await _form('Личный расход', {
      'amount': 'Сумма, ₽',
      'description': 'За что оплачено',
    }, expense: true);
    if (body == null) return;
    final photo = body.remove('receipt') as XFile?;
    body['amount'] = (body['amount'] as String).replaceAll(',', '.');
    if (c.canRecordCommissioning) {
      await _queue(project, 'expense', body, receipt: photo);
    } else {
      await _post('commissioning/$project/expenses/', body);
    }
  }

  Future<XFile?> _pickReceipt(BuildContext context) async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.camera_alt_outlined),
              title: const Text('Сфотографировать чек'),
              onTap: () => Navigator.pop(ctx, ImageSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('Выбрать фото'),
              onTap: () => Navigator.pop(ctx, ImageSource.gallery),
            ),
          ],
        ),
      ),
    );
    if (source == null) return null;
    return ImagePicker().pickImage(
      source: source,
      maxWidth: 1920,
      maxHeight: 1920,
      imageQuality: 80,
    );
  }

  Future<void> _queue(
    int project,
    String action,
    Json body, {
    XFile? receipt,
  }) async {
    if (!mounted || _busy) return;
    final expectedOrganization = org;
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      if (receipt != null && await receipt.length() > 10 * 1024 * 1024) {
        throw const ApiError('Фото чека больше 10 МБ.');
      }
      await c.queueCommissioning(
        project,
        action,
        body,
        receipt: await receipt?.readAsBytes(),
        expectedOrganization: expectedOrganization,
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Сохранено на телефоне · ожидает отправки'),
          ),
        );
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
        _afterRefresh();
      }
    }
  }

  Future<void> _receipt(Json expense) async {
    final project = _detail!['id'] as int;
    try {
      final photo = await _pickReceipt(context);
      if (!mounted || photo == null) return;
      if (c.canRecordCommissioning) {
        await _queue(project, 'receipt', {
          'expense': expense['id'],
        }, receipt: photo);
      } else {
        await c.uploadCommissioningReceipt(
          project,
          expense['id'] as int,
          await photo.readAsBytes(),
          photo.name,
          expectedOrganization: org,
        );
        if (mounted) await _load();
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _showReceipt(Json expense) async {
    try {
      final bytes = await c.download(
        '/api/commissioning/${_detail!['id']}/expenses/${expense['id']}/receipt/',
        org,
      );
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Чек'),
          content: InteractiveViewer(
            child: Image.memory(bytes, fit: BoxFit.contain),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Закрыть'),
            ),
          ],
        ),
      );
    } catch (error) {
      if (mounted) showError(context, error);
    }
  }

  Future<void> _writeMessage(int project) async {
    final body = await _form('Написать в чат', {'text': 'Сообщение'});
    if (body == null) return;
    if (c.canRecordCommissioning) {
      await _queue(project, 'message', body);
    } else {
      await _post('commissioning/$project/messages/', body);
    }
  }

  Future<void> _askPost(
    String title,
    String path,
    Map<String, String> fields,
  ) async {
    final body = await _form(title, fields);
    if (body != null) await _post(path, body);
  }

  Widget _section(String title, List<Widget> children) => Card(
    child: Padding(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 10),
          ...children,
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final p = _detail;
    final allowed = c.canWrite && !_busy && p?['archived'] != true;
    final canQueue =
        !_busy &&
        p?['archived'] != true &&
        (c.canRecordCommissioning || c.canWrite);
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  'Пусконаладка',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
              if (c.pendingCount > 0)
                IconButton(
                  tooltip: 'Очередь отправки · ${c.pendingCount}',
                  icon: Badge(
                    label: Text('${c.pendingCount}'),
                    child: const Icon(Icons.cloud_upload_outlined),
                  ),
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                      builder: (_) => SyncQueuePage(controller: c),
                    ),
                  ),
                ),
              IconButton(
                tooltip: 'Обновить',
                onPressed: _busy ? null : _load,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
        ),
        if (_error.isNotEmpty)
          Padding(
            padding: const EdgeInsets.all(12),
            child: Text(
              _error,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        if (!c.online)
          const Padding(
            padding: EdgeInsets.all(8),
            child: Text(
              'Нет сети. Прогресс, расходы и сообщения сохраняются на телефоне.',
            ),
          ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(12),
            children: [
              if (p == null) ...[
                if (canManage)
                  FilledButton.icon(
                    onPressed: c.canWrite && !_busy ? _createProject : null,
                    icon: const Icon(Icons.add),
                    label: const Text('Создать объект'),
                  ),
                for (final project in _projects)
                  Card(
                    child: ListTile(
                      title: Text(text(project, 'name')),
                      subtitle: Text(
                        '${project['address']} · ${project['lift_count']} лифтов',
                      ),
                      trailing: Text('${project['progress']}%'),
                      onTap: () => _open(project['id'] as int),
                    ),
                  ),
                if (_projects.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(24),
                    child: Text('Назначенных объектов пока нет.'),
                  ),
              ] else ...[
                OutlinedButton.icon(
                  onPressed: () => setState(() => _detail = null),
                  icon: const Icon(Icons.arrow_back),
                  label: const Text('Все объекты'),
                ),
                const SizedBox(height: 10),
                _section(text(p, 'name'), [
                  Text(text(p, 'address')),
                  Text('Общая готовность: ${p['progress']}%'),
                  if (p['archived'] == true)
                    const Text('Архив · только просмотр'),
                  if (canManage && allowed)
                    TextButton.icon(
                      onPressed: _addLift,
                      icon: const Icon(Icons.add),
                      label: const Text('Добавить лифт'),
                    ),
                ]),
                for (final lift in records(p['lifts'] ?? []))
                  _section('${lift['label']} · ${lift['progress']}%', [
                    Text('Заводской номер: ${lift['factory_number']}'),
                    Text(
                      'Исполнители: ${records(lift['assignees'] ?? []).map((m) => m['name']).join(', ')}',
                    ),
                    if (canQueue && lift['can_edit'] == true)
                      FilledButton.tonal(
                        onPressed: () => _progress(lift),
                        child: const Text('Изменить процент и записать работу'),
                      ),
                    if (allowed && canManage)
                      OutlinedButton(
                        onPressed: () => _assign(lift),
                        child: const Text('Назначить наладчика'),
                      ),
                    for (final h in records(lift['history'] ?? []))
                      ListTile(
                        dense: true,
                        title: Text(
                          '${h['before']}% → ${h['after']}% · ${h['author']}',
                        ),
                        subtitle: Text(text(h, 'description')),
                      ),
                  ]),
                _section('Потребности', [
                  for (final need in records(p['needs'] ?? []))
                    ListTile(
                      dense: true,
                      title: Text(
                        '${need['blocking'] == true ? '⛔ ' : ''}${need['description']}',
                      ),
                      subtitle: Text(
                        need['resolved'] == true ? 'Закрыта' : 'Ожидает',
                      ),
                      trailing: canManage && allowed && need['resolved'] != true
                          ? IconButton(
                              icon: const Icon(Icons.check),
                              tooltip: 'Закрыть',
                              onPressed: () => _post(
                                'commissioning/${p['id']}/needs/${need['id']}/resolve/',
                                {},
                              ),
                            )
                          : null,
                    ),
                  if (allowed)
                    OutlinedButton.icon(
                      onPressed: () => _askPost(
                        'Потребность',
                        'commissioning/${p['id']}/needs/',
                        {'description': 'Что требуется'},
                      ),
                      icon: const Icon(Icons.add),
                      label: const Text('Добавить потребность'),
                    ),
                ]),
                _section('Дополнительные работы', [
                  for (final extra in records(p['extras'] ?? []))
                    ListTile(
                      dense: true,
                      title: Text(text(extra, 'description')),
                      subtitle: Text(
                        '${extra['price']} ₽ · ${extra['status']}',
                      ),
                      trailing:
                          canManage && allowed && extra['status'] == 'PENDING'
                          ? Wrap(
                              children: [
                                IconButton(
                                  onPressed: () => _post(
                                    'commissioning/${p['id']}/extra-work/${extra['id']}/decision/',
                                    {'approve': true},
                                  ),
                                  icon: const Icon(Icons.check),
                                  tooltip: 'Согласовать',
                                ),
                                IconButton(
                                  onPressed: () => _post(
                                    'commissioning/${p['id']}/extra-work/${extra['id']}/decision/',
                                    {'approve': false},
                                  ),
                                  icon: const Icon(Icons.close),
                                  tooltip: 'Отклонить',
                                ),
                              ],
                            )
                          : null,
                    ),
                  if (allowed)
                    OutlinedButton.icon(
                      onPressed: () => _askPost(
                        'Дополнительная работа',
                        'commissioning/${p['id']}/extra-work/',
                        {'description': 'Работа', 'price': 'Стоимость, ₽'},
                      ),
                      icon: const Icon(Icons.add),
                      label: const Text('Предложить работу'),
                    ),
                ]),
                _section(
                  canManage ? 'Расходы сотрудников' : 'Мои личные расходы',
                  [
                    const Text('Чужие расходы наладчикам недоступны.'),
                    for (final expense in records(p['expenses'] ?? []))
                      ListTile(
                        dense: true,
                        title: Text(
                          '${expense['amount']} ₽ · ${expense['description']}',
                        ),
                        subtitle: Text(
                          [
                            expense['author'],
                            expense['spent_on'],
                            if (expense['has_receipt'] == true)
                              'Чек прикреплён',
                          ].whereType<String>().join(' · '),
                        ),
                        onTap: expense['has_receipt'] == true
                            ? () => _showReceipt(expense)
                            : null,
                        trailing:
                            expense['has_receipt'] != true &&
                                !canManage &&
                                canQueue
                            ? IconButton(
                                icon: const Icon(Icons.camera_alt_outlined),
                                tooltip: 'Прикрепить чек',
                                onPressed: () => _receipt(expense),
                              )
                            : canManage &&
                                  allowed &&
                                  expense['reimbursed_at'] == null
                            ? TextButton(
                                onPressed: () => _post(
                                  'commissioning/${p['id']}/expenses/${expense['id']}/reimburse/',
                                  {},
                                ),
                                child: const Text('Возместить'),
                              )
                            : null,
                      ),
                    if (!canManage && canQueue)
                      FilledButton.tonal(
                        onPressed: _addExpense,
                        child: const Text('Добавить личный расход'),
                      ),
                  ],
                ),
                _section('Запрос новых лифтов', [
                  for (final r in records(p['lift_requests'] ?? []))
                    ListTile(
                      dense: true,
                      title: Text(text(r, 'label')),
                      subtitle: Text(text(r, 'status')),
                      trailing: canManage && allowed && r['status'] == 'PENDING'
                          ? Wrap(
                              children: [
                                IconButton(
                                  icon: const Icon(Icons.check),
                                  tooltip: 'Одобрить',
                                  onPressed: () => _post(
                                    'commissioning/${p['id']}/lift-requests/${r['id']}/decision/',
                                    {'approve': true},
                                  ),
                                ),
                                IconButton(
                                  icon: const Icon(Icons.close),
                                  tooltip: 'Отклонить',
                                  onPressed: () => _post(
                                    'commissioning/${p['id']}/lift-requests/${r['id']}/decision/',
                                    {'approve': false},
                                  ),
                                ),
                              ],
                            )
                          : null,
                    ),
                  if (!canManage && allowed)
                    OutlinedButton(
                      onPressed: () => _askPost(
                        'Запросить лифт',
                        'commissioning/${p['id']}/lift-requests/',
                        {'label': 'Обозначение лифта'},
                      ),
                      child: const Text('Запросить добавление лифта'),
                    ),
                ]),
                _section('Чат объекта', [
                  const Text('Сообщение не является согласованием допработ.'),
                  for (final m in records(p['messages'] ?? []))
                    ListTile(
                      dense: true,
                      title: Text(text(m, 'author')),
                      subtitle: Text(text(m, 'text')),
                    ),
                  if (canQueue)
                    OutlinedButton(
                      onPressed: () => _writeMessage(p['id'] as int),
                      child: const Text('Новое сообщение'),
                    ),
                ]),
                if (canManage && allowed)
                  TextButton(
                    onPressed: () => _askPost(
                      'Архивировать объект',
                      'commissioning/${p['id']}/archive/',
                      {'reason': 'Причина архивации'},
                    ),
                    child: const Text('Архивировать объект'),
                  ),
              ],
            ],
          ),
        ),
      ],
    );
  }
}
