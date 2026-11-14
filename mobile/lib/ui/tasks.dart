import 'dart:async';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';
import 'media_widgets.dart';

void openTask(BuildContext context, LiftController c, int id) => Navigator.push(
  context,
  MaterialPageRoute<void>(
    builder: (_) =>
        TaskPage(controller: c, id: id, organization: c.organization!),
  ),
);

class TasksPage extends StatefulWidget {
  const TasksPage({super.key, required this.controller, this.create = false});
  final LiftController controller;
  final bool create;
  @override
  State<TasksPage> createState() => _TasksPageState();
}

class _TasksPageState extends State<TasksPage> {
  late final int _org;
  int _filter = 0;
  @override
  void initState() {
    super.initState();
    _org = widget.controller.organization!;
    if (widget.create) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _create();
      });
    }
  }

  Future<void> _create() => Navigator.push(
    context,
    MaterialPageRoute<void>(
      builder: (_) => CreateTaskPage(controller: widget.controller),
    ),
  );
  @override
  Widget build(BuildContext context) => ScopePage(
    controller: widget.controller,
    organization: _org,
    builder: (context) {
      final c = widget.controller;
      final supported = c.me['field_ops_api'] == 1 || c.demo;
      final rows = c.list('tasks').where((row) {
        final closed = ['DONE', 'CANCELED'].contains(row['status']);
        return _filter == 2 || (_filter == 1 ? closed : !closed);
      }).toList();
      return Scaffold(
        appBar: AppBar(
          title: const Text('Личные задания'),
          actions: [
            if (c.me['can_manage'] == true && supported)
              IconButton(
                tooltip: 'Новое личное задание',
                onPressed: c.canWrite ? _create : null,
                icon: const Icon(Icons.add),
              ),
            IconButton(
              tooltip: 'Обновить',
              onPressed: c.refresh,
              icon: const Icon(Icons.sync),
            ),
          ],
        ),
        body: RefreshIndicator(
          onRefresh: c.refresh,
          child: ListView(
            padding: const EdgeInsets.all(18),
            physics: const AlwaysScrollableScrollPhysics(),
            children: [
              const Text(
                'Задание видят только назначенный механик и руководители организации.',
              ),
              if (!supported)
                const Sheet(
                  child: Text(
                    'Для личных заданий администратору нужно обновить сервер до версии 0.8.',
                  ),
                ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                children: [
                  for (var i = 0; i < 3; i++)
                    ChoiceChip(
                      label: Text(['Активные', 'Завершённые', 'Все'][i]),
                      selected: _filter == i,
                      onSelected: (_) => setState(() => _filter = i),
                    ),
                ],
              ),
              const SizedBox(height: 14),
              if (c.me['can_manage'] == true && supported)
                FilledButton.icon(
                  onPressed: c.canWrite ? _create : null,
                  icon: const Icon(Icons.add),
                  label: const Text('Назначить личное задание'),
                ),
              if (rows.isEmpty)
                const Sheet(
                  child: EmptyState(
                    'Заданий пока нет',
                    'Выберите другой фильтр или обновите список.',
                    icon: Icons.assignment_ind_outlined,
                  ),
                ),
              for (final row in rows)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Sheet(
                    child: ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text('#${row['id']} · ${text(row, 'title')}'),
                      subtitle: Text(
                        '${text(row, 'technician_name')}\n${text(row, 'status') == 'NEW' ? 'Новое' : statusNames[row['status']] ?? text(row, 'status')}${row['due_at'] != null ? ' · До ${dateLabel(row['due_at'], c.utcOffset)}' : ''}',
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => openTask(context, c, row['id'] as int),
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

class TaskPage extends StatefulWidget {
  const TaskPage({
    super.key,
    required this.controller,
    required this.id,
    required this.organization,
  });
  final LiftController controller;
  final int id, organization;
  @override
  State<TaskPage> createState() => _TaskPageState();
}

class _TaskPageState extends State<TaskPage> {
  Json _task = {};
  bool _busy = false, _loading = true;
  String _error = '';
  LiftController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final row = await c.resource(
        'tasks/${widget.id}/',
        expectedOrganization: widget.organization,
      );
      if (mounted) {
        setState(() {
          _task = Map<String, dynamic>.from(row);
          _error = '';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
      await c.refresh();
      await _load();
    } catch (e) {
      if (mounted) showError(context, e);
      await c.refresh();
      await _load();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _transition(String action, {String body = ''}) => _run(() async {
    await c.request(
      'tasks/${widget.id}/$action/',
      method: 'POST',
      data: {'body': body},
      expectedOrganization: widget.organization,
    );
    if (action == 'complete') {
      c.setTaskDraft(widget.id, '', widget.organization);
      await c.flushDrafts();
    }
  });
  Future<void> _complete() async {
    final work = await askText(
      context,
      title: 'Завершить личное задание',
      label: 'Что выполнено',
      initial: c.taskDraft(widget.id),
      onChanged: (value) =>
          c.setTaskDraft(widget.id, value, widget.organization),
      action: 'Завершить',
    );
    await c.flushDrafts();
    if (work != null) await _transition('complete', body: work);
  }

  Future<void> _comment() async {
    final value = await askText(
      context,
      title: 'Комментарий к заданию',
      label: 'Комментарий',
      minimum: 1,
      maximum: 4000,
      action: 'Добавить',
    );
    if (value != null) await _transition('comment', body: value);
  }

  Future<void> _cancel() async {
    final value = await askText(
      context,
      title: 'Отменить личное задание',
      label: 'Причина отмены',
      minimum: 5,
      maximum: 1000,
      action: 'Отменить задание',
    );
    if (value != null) await _transition('cancel', body: value);
  }

  bool get _canPhoto => c.me['media_api'] == 1 ? c.canRecord : c.canWrite;

  Future<void> _photo() async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.camera_alt_outlined),
              title: const Text('Сделать фото'),
              onTap: () => Navigator.pop(context, ImageSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('Фото из галереи'),
              onTap: () => Navigator.pop(context, ImageSource.gallery),
            ),
          ],
        ),
      ),
    );
    if (source == null || !mounted || !_canPhoto) return;
    try {
      final image = await ImagePicker().pickImage(
        source: source,
        maxWidth: 1920,
        maxHeight: 1920,
        imageQuality: 78,
      );
      if (image == null) return;
      if (await image.length() > 10 * 1024 * 1024) {
        throw const ApiError('Фото должно быть не больше 10 МБ.');
      }
      final bytes = await image.readAsBytes();
      if (!mounted || !_canPhoto || c.organization != widget.organization) {
        return;
      }
      await _run(() async {
        await c.uploadMedia(
          'tasks/${widget.id}/attachments/',
          [(bytes: bytes, name: image.name)],
          widget.organization,
          field: 'file',
        );
      });
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: c,
    organization: widget.organization,
    builder: (context) {
      final open = !['DONE', 'CANCELED'].contains(_task['status']);
      return Scaffold(
        appBar: AppBar(
          title: Text('Личное задание #${widget.id}'),
          actions: [
            IconButton(
              tooltip: 'Обновить',
              onPressed: _busy ? null : _load,
              icon: const Icon(Icons.sync),
            ),
          ],
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(18),
                children: [
                  if (_error.isNotEmpty) Sheet(child: Text(_error)),
                  if (_task.isNotEmpty) ...[
                    Sheet(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            text(_task, 'title'),
                            style: Theme.of(context).textTheme.headlineSmall,
                          ),
                          const SizedBox(height: 12),
                          StatusPill(text(_task, 'status')),
                          const SizedBox(height: 12),
                          Text(
                            'Исполнитель: ${text(_task, 'technician_name')}',
                          ),
                          if (text(_task, 'address', '').isNotEmpty)
                            Text(text(_task, 'address')),
                          if (_task['due_at'] != null)
                            Text(
                              'Срок: ${dateLabel(_task['due_at'], c.utcOffset)}',
                            ),
                          const SizedBox(height: 12),
                          SelectableText(text(_task, 'description', '')),
                        ],
                      ),
                    ),
                    const SizedBox(height: 14),
                    if (text(_task, 'result', '').isNotEmpty)
                      Sheet(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const SectionTitle('Выполненные работы'),
                            SelectableText(text(_task, 'result')),
                          ],
                        ),
                      ),
                    if (text(_task, 'cancellation_reason', '').isNotEmpty)
                      Sheet(
                        child: Text(
                          'Причина отмены: ${text(_task, 'cancellation_reason')}',
                        ),
                      ),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        if (_task['can_accept'] == true)
                          FilledButton(
                            onPressed: c.canWrite && !_busy
                                ? () => _transition('accept')
                                : null,
                            child: const Text('Принять задание'),
                          ),
                        if (_task['can_complete'] == true)
                          FilledButton(
                            onPressed: c.canWrite && !_busy ? _complete : null,
                            child: const Text('Завершить с отчётом'),
                          ),
                        if (_task['can_cancel'] == true)
                          OutlinedButton(
                            onPressed: c.canWrite && !_busy ? _cancel : null,
                            child: const Text('Отменить задание'),
                          ),
                        if (open)
                          OutlinedButton.icon(
                            onPressed: c.canWrite && !_busy ? _comment : null,
                            icon: const Icon(Icons.comment_outlined),
                            label: const Text('Комментарий'),
                          ),
                        if (open)
                          OutlinedButton.icon(
                            onPressed: _canPhoto && !_busy ? _photo : null,
                            icon: const Icon(Icons.camera_alt_outlined),
                            label: const Text('Фотоотчёт'),
                          ),
                      ],
                    ),
                    for (final attachment in records(
                      _task['attachments'] ?? [],
                    ))
                      PrivateMedia(
                        key: ValueKey(attachment['id']),
                        controller: c,
                        organization: widget.organization,
                        attachment: attachment,
                      ),
                    for (final row in c.pendingOperations)
                      if (row['command']['type'] == 'media' &&
                          row['command']['body']['kind'] == 'task' &&
                          row['command']['body']['target_id'] == widget.id)
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              'Фото сохранено на телефоне · ожидает отправки',
                            ),
                            PrivateMedia(
                              key: ValueKey(row['id']),
                              controller: c,
                              organization: widget.organization,
                              attachment: {
                                'pending': true,
                                'local_scope': c.scope,
                                'file_id': row['command']['body']['file_id'],
                                'content_type': 'image/jpeg',
                                'size': row['command']['body']['size'],
                              },
                            ),
                          ],
                        ),
                    const SectionTitle('История задания'),
                    for (final row in records(_task['history'] ?? []))
                      Sheet(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '${text(row, 'author_name')} · ${dateLabel(row['created_at'], c.utcOffset)}',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                            const SizedBox(height: 6),
                            Text(
                              text(row, 'body', '').isNotEmpty
                                  ? text(row, 'body')
                                  : const {
                                          'created': 'Назначено задание',
                                          'accepted': 'Принято в работу',
                                          'photo_added': 'Добавлено фото',
                                          'assigned': 'Изменён исполнитель',
                                          'updated': 'Изменены сведения',
                                        }[row['action']] ??
                                        text(row, 'action'),
                            ),
                          ],
                        ),
                      ),
                  ],
                  if (_busy) const LinearProgressIndicator(),
                ],
              ),
      );
    },
  );
}

class CreateTaskPage extends StatefulWidget {
  const CreateTaskPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<CreateTaskPage> createState() => _CreateTaskPageState();
}

class _CreateTaskPageState extends State<CreateTaskPage> {
  final _form = GlobalKey<FormState>(),
      _title = TextEditingController(),
      _description = TextEditingController();
  late final int _org;
  int? _person, _elevator;
  String _priority = 'NORMAL';
  DateTime? _due;
  bool _busy = false;
  LiftController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    _org = c.organization!;
  }

  @override
  void dispose() {
    _title.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _deadline() async {
    final now = c.orgNow;
    final date = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 3),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: const TimeOfDay(hour: 18, minute: 0),
    );
    if (time != null && mounted) {
      setState(
        () => _due = DateTime.utc(
          date.year,
          date.month,
          date.day,
          time.hour,
          time.minute,
        ).subtract(Duration(minutes: c.utcOffset)),
      );
    }
  }

  Future<void> _save() async {
    if (_busy || !_form.currentState!.validate()) return;
    setState(() => _busy = true);
    try {
      await c.request(
        'tasks/',
        method: 'POST',
        data: {
          'title': _title.text.trim(),
          'description': _description.text.trim(),
          'assigned_to': _person,
          'elevator': _elevator,
          'priority': _priority,
          'due_at': _due?.toIso8601String(),
        },
        expectedOrganization: _org,
      );
      await c.refresh();
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: c,
    organization: _org,
    builder: (context) => Scaffold(
      appBar: AppBar(title: const Text('Новое личное задание')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            const Text(
              'Задание увидит назначенный механик и руководители. В общие заявки оно не попадёт.',
            ),
            const SizedBox(height: 18),
            TextFormField(
              controller: _title,
              decoration: const InputDecoration(labelText: 'Название задания'),
              maxLength: 200,
              validator: (value) =>
                  (value?.trim().length ?? 0) < 2 ? 'Опишите задание' : null,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<int>(
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Исполнитель'),
              items: [
                for (final person
                    in c
                        .list('technicians')
                        .where(
                          (t) =>
                              t['active'] == true &&
                              t['can_receive_tasks'] != false,
                        ))
                  DropdownMenuItem(
                    value: person['id'] as int,
                    child: Text(
                      text(person, 'full_name'),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: _busy
                  ? null
                  : (value) => setState(() => _person = value),
              validator: (value) => value == null ? 'Выберите механика' : null,
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<int>(
              initialValue: 0,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Лифт (необязательно)',
              ),
              items: [
                const DropdownMenuItem(
                  value: 0,
                  child: Text('Без привязки к лифту'),
                ),
                for (final lift
                    in c.list('elevators').where((e) => e['active'] == true))
                  DropdownMenuItem(
                    value: lift['id'] as int,
                    child: Text(
                      '${text(lift, 'address')} · ${liftLabel(lift)}',
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: _busy
                  ? null
                  : (value) =>
                        setState(() => _elevator = value == 0 ? null : value),
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              initialValue: _priority,
              decoration: const InputDecoration(labelText: 'Приоритет'),
              items: [
                for (final key in ['NORMAL', 'HIGH', 'LOW'])
                  DropdownMenuItem(
                    value: key,
                    child: Text(priorityNames[key]!),
                  ),
              ],
              onChanged: _busy
                  ? null
                  : (value) => setState(() => _priority = value!),
            ),
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Срок (необязательно)'),
              subtitle: Text(
                _due == null
                    ? 'Не установлен'
                    : dateLabel(_due!.toIso8601String(), c.utcOffset),
              ),
              onTap: _busy ? null : _deadline,
              trailing: _due == null
                  ? const Icon(Icons.calendar_month_outlined)
                  : IconButton(
                      tooltip: 'Убрать срок',
                      onPressed: _busy
                          ? null
                          : () => setState(() => _due = null),
                      icon: const Icon(Icons.close),
                    ),
            ),
            TextFormField(
              controller: _description,
              decoration: const InputDecoration(labelText: 'Описание задания'),
              minLines: 3,
              maxLines: 6,
              maxLength: 4000,
            ),
            const SizedBox(height: 18),
            FilledButton(
              onPressed: c.canWrite && !_busy ? _save : null,
              child: Text(_busy ? 'Назначение…' : 'Назначить задание'),
            ),
          ],
        ),
      ),
    ),
  );
}
