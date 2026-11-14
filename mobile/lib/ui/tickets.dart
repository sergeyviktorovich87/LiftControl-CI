import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';
import 'media_library.dart';
import 'elevators.dart';
import 'chat.dart';

void openTicket(BuildContext context, LiftController c, int id) =>
    Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) =>
            TicketPage(controller: c, id: id, organization: c.organization!),
      ),
    );

class TicketsPage extends StatefulWidget {
  const TicketsPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<TicketsPage> createState() => _TicketsPageState();
}

class _TicketsPageState extends State<TicketsPage> {
  String _search = '';
  int _filter = 0;
  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final rows =
        c.list('tickets').where((t) {
          final closed = ['DONE', 'CANCELED'].contains(t['status']);
          final matches = switch (_filter) {
            0 => !closed,
            1 => !closed && t['assigned_to'] == c.me['technician'],
            2 => closed,
            _ => true,
          };
          return matches &&
              '${t['title']} ${t['address']} ${t['elevator_label']} ${t['id']}'
                  .toLowerCase()
                  .contains(_search.toLowerCase());
        }).toList()..sort((a, b) {
          final criticalA =
              a['priority'] == 'ENTRAPMENT' &&
              !['DONE', 'CANCELED'].contains(a['status']);
          final criticalB =
              b['priority'] == 'ENTRAPMENT' &&
              !['DONE', 'CANCELED'].contains(b['status']);
          if (criticalA != criticalB) return criticalA ? -1 : 1;
          return '${b['created_at']}'.compareTo('${a['created_at']}');
        });
    return RefreshIndicator(
      onRefresh: c.refresh,
      child: ListView(
        padding: const EdgeInsets.all(18),
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          SectionTitle(
            'Заявки',
            trailing: c.me['can_create_ticket'] == true
                ? IconButton(
                    tooltip: 'Создать заявку',
                    onPressed: c.canCreateTicket
                        ? () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => CreateTicketPage(controller: c),
                            ),
                          )
                        : null,
                    icon: const Icon(Icons.add_circle_outline),
                  )
                : null,
          ),
          TextField(
            decoration: const InputDecoration(
              hintText: 'Адрес, лифт или номер заявки',
              prefixIcon: Icon(Icons.search),
            ),
            onChanged: (v) => setState(() => _search = v),
          ),
          const SizedBox(height: 14),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: List.generate(
                4,
                (i) => Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(['Активные', 'Мои', 'Завершённые', 'Все'][i]),
                    selected: _filter == i,
                    onSelected: (_) => setState(() => _filter = i),
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(height: 14),
          Text(
            'Найдено: ${rows.length}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 10),
          if (rows.isEmpty)
            const Sheet(
              child: EmptyState(
                'Заявок нет',
                'Попробуйте другой фильтр или обновите данные.',
              ),
            ),
          for (final t in rows)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: TicketTile(
                t,
                controller: c,
                onTap: () => openTicket(context, c, t['id'] as int),
              ),
            ),
        ],
      ),
    );
  }
}

class TicketPage extends StatefulWidget {
  const TicketPage({
    super.key,
    required this.controller,
    required this.id,
    required this.organization,
  });
  final LiftController controller;
  final int id, organization;
  @override
  State<TicketPage> createState() => _TicketPageState();
}

class _TicketPageState extends State<TicketPage> {
  LiftController get c => widget.controller;
  Json? _loaded;
  Json _history = {};
  bool _busy = false, _loading = true;
  String _error = '';
  int get _ticketId => c.resolvedTicket(widget.id);
  DateTime? _loadedAt;
  void _controllerChanged() {
    if (mounted &&
        c.organization == widget.organization &&
        c.online &&
        !_loading &&
        !_busy &&
        c.syncedAt != _loadedAt) {
      _loadedAt = c.syncedAt;
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    c.removeListener(_controllerChanged);
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    _loadedAt = c.syncedAt;
    c.addListener(_controllerChanged);
    unawaited(_load());
    unawaited(_recoverPhoto());
  }

  Future<void> _load() async {
    if (mounted) setState(() => _loading = true);
    _loadedAt = c.syncedAt;
    if (c.demo ||
        _ticketId < 0 ||
        (!c.online && c.localTicket(_ticketId).isNotEmpty)) {
      setState(() {
        _loading = false;
        _error = '';
      });
      return;
    }
    try {
      final values = await Future.wait([
        c.resource(
          'tickets/$_ticketId/',
          expectedOrganization: widget.organization,
        ),
        c.resource(
          'tickets/$_ticketId/history/',
          expectedOrganization: widget.organization,
        ),
      ]);
      if (mounted) {
        setState(() {
          _loaded = Map<String, dynamic>.from(values[0]);
          _history = Map<String, dynamic>.from(values[1]);
          _error = '';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Json get ticket {
    final local = c.localTicket(_ticketId);
    if (local.isNotEmpty) return local;
    if (_loaded != null) return _loaded!;
    return c
        .list('tickets')
        .firstWhere((t) => t['id'] == widget.id, orElse: () => {});
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) {
        setState(() => _busy = false);
        _controllerChanged();
      }
    }
  }

  Future<void> _transition(String action, Json body) async {
    final result = await c.request(
      'tickets/$_ticketId/$action/',
      method: 'POST',
      data: body,
      expectedOrganization: widget.organization,
    );
    if (mounted) setState(() => _loaded = Map<String, dynamic>.from(result));
  }

  Future<void> _complete() async {
    final work = await askText(
      context,
      title: 'Завершить заявку',
      label: 'Что выполнено',
      initial: c.draft(widget.id),
      onChanged: (v) => c.setDraft(widget.id, v, widget.organization),
      action: 'Завершить',
    );
    await c.flushDrafts();
    if (work == null) return;
    await _run(() async {
      await _transition('complete', {'work_description': work});
      c.setDraft(widget.id, '', widget.organization);
      await c.flushDrafts();
    });
  }

  Future<void> _comment() async {
    final body = await askText(
      context,
      title: 'Комментарий',
      label: 'Текст комментария',
      minimum: 1,
      maximum: 2000,
      action: 'Отправить',
    );
    if (body != null) {
      await _run(() async {
        await c.request(
          'tickets/$_ticketId/comment/',
          method: 'POST',
          data: {'body': body},
          expectedOrganization: widget.organization,
        );
      });
    }
  }

  Future<void> _assign() async {
    final selected = await showModalBottomSheet<int>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            const Padding(
              padding: EdgeInsets.all(18),
              child: Text('Назначить исполнителя'),
            ),
            for (final person
                in c
                    .list('technicians')
                    .where(
                      (t) =>
                          t['active'] == true &&
                          t['can_receive_tasks'] != false,
                    ))
              ListTile(
                title: Text(person['full_name'] as String),
                onTap: () => Navigator.pop(context, person['id'] as int),
              ),
          ],
        ),
      ),
    );
    if (selected == null || !mounted) return;
    final reason = await askText(
      context,
      title: 'Причина переназначения',
      label: 'Почему меняется исполнитель',
      minimum: 5,
      maximum: 1000,
      action: 'Назначить',
    );
    if (reason == null) return;
    await _run(
      () => _transition('assign', {'assigned_to': selected, 'reason': reason}),
    );
  }

  Future<void> _cancel() async {
    final reason = await askText(
      context,
      title: 'Отменить заявку',
      label: 'Причина отмены',
      minimum: 5,
      maximum: 1000,
      action: 'Отменить заявку',
    );
    if (reason != null) {
      await _run(() => _transition('cancel', {'reason': reason}));
    }
  }

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
              title: const Text('Выбрать из галереи'),
              onTap: () => Navigator.pop(context, ImageSource.gallery),
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
    if (source == null || !c.canRecord) return;
    final scope = c.scope, generation = c.generation;
    await c.store.write(
      'pending_photo',
      jsonEncode({'scope': scope, 'ticket': widget.id}),
    );
    try {
      final file = await ImagePicker().pickImage(
        source: source,
        maxWidth: 1920,
        maxHeight: 1920,
        imageQuality: 78,
      );
      if (file != null && c.generation == generation) await _attach(file);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (c.generation == generation) await c.store.delete('pending_photo');
    }
  }

  Future<void> _recoverPhoto() async {
    if (c.demo || c.session == null) return;
    try {
      final pending = await c.store.read('pending_photo');
      if (pending == null) return;
      final data = jsonDecode(pending);
      if (data['scope'] != c.scope || data['ticket'] != widget.id) return;
      final result = await ImagePicker().retrieveLostData();
      await c.store.delete('pending_photo');
      if (result.files?.isNotEmpty == true && mounted && c.canRecord) {
        final attach = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Восстановлено фото'),
            content: Text('Прикрепить выбранное фото к заявке #${widget.id}?'),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Отмена'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Прикрепить'),
              ),
            ],
          ),
        );
        if (attach == true) await _attach(result.files!.first);
      }
    } catch (_) {
      c.log.record('photo_recovery_failed');
    }
  }

  Future<void> _attach(XFile file) async {
    if (await file.length() > 10 * 1024 * 1024) {
      throw const ApiError('Фото должно быть меньше 10 МБ.');
    }
    final bytes = await file.readAsBytes();
    await _run(() async {
      await c.uploadPhoto(widget.id, bytes, file.name, widget.organization);
    });
  }

  Future<void> _deferRepair() async {
    final result = await showDialog<Json>(
      context: context,
      builder: (_) => _LongRepairDialog(today: c.orgNow),
    );
    if (result != null && mounted) {
      await _run(() => _transition('long-repair', result));
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: c,
    organization: widget.organization,
    builder: (context) {
      final t = ticket,
          photos = t['photos'] is List ? records(t['photos']) : <Json>[];
      final comments = _history['comments'] is List
          ? records(_history['comments'])
          : <Json>[];
      final pending = c.pendingOperations
          .where((row) => row['command']['ticket'] == _ticketId)
          .toList();
      for (final row in pending.where(
        (row) => row['command']['type'] == 'comment',
      )) {
        comments.add({
          'id': row['id'],
          'author': c.me['name'],
          'body': row['command']['body']['body'],
          'created_at': row['command']['occurred_at'],
          'sync_state': row['state'],
        });
      }
      for (final row in pending.where(
        (row) => row['command']['type'] == 'photo',
      )) {
        photos.add({
          'id': row['id'],
          'local_file': row['command']['body']['file'],
        });
      }
      final events = _history['events'] is List
          ? records(_history['events'])
          : <Json>[];
      final open = !['DONE', 'CANCELED'].contains(t['status']);
      final canComment =
          open &&
          (t['can_complete'] == true ||
              c.me['on_duty'] == true ||
              c.me['can_manage'] == true);
      return Scaffold(
        appBar: AppBar(
          title: Text(
            _ticketId < 0 ? 'Заявка на телефоне' : 'Заявка #$_ticketId',
          ),
          actions: [
            IconButton(
              tooltip: 'Обсудить заявку в чате',
              icon: const Icon(Icons.chat_bubble_outline),
              onPressed: _ticketId <= 0
                  ? null
                  : () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => ChatPage(
                          controller: c,
                          quoteKind: 'ticket',
                          quoteId: _ticketId,
                          quoteTitle: text(t, 'title'),
                        ),
                      ),
                    ),
            ),
            IconButton(
              tooltip: 'Вложения заявки',
              icon: const Icon(Icons.photo_library_outlined),
              onPressed: _ticketId < 0
                  ? null
                  : () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) =>
                            MediaPage(controller: c, ticket: _ticketId),
                      ),
                    ),
            ),
            IconButton(
              tooltip: 'Обновить',
              onPressed: _loading || _busy ? null : _load,
              icon: const Icon(Icons.sync),
            ),
            if (t['can_cancel'] == true)
              PopupMenuButton<String>(
                enabled: c.canRecord && _ticketId > 0 && !_busy,
                onSelected: (_) => _cancel(),
                itemBuilder: (_) => [
                  const PopupMenuItem(
                    value: 'cancel',
                    child: Text('Отменить с причиной'),
                  ),
                ],
              ),
          ],
        ),
        body: _loading && t.isEmpty
            ? const Center(child: CircularProgressIndicator())
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView(
                  padding: const EdgeInsets.all(18),
                  children: [
                    if (_error.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: Text(
                          _error,
                          style: TextStyle(
                            color: Theme.of(context).colorScheme.error,
                          ),
                        ),
                      ),
                    if (!c.online && !c.demo)
                      const Padding(
                        padding: EdgeInsets.only(bottom: 12),
                        child: Text(
                          'Без связи · изменения сохраняются на телефоне',
                        ),
                      ),
                    if (t['sync_state'] != null) SyncStatus(ticket: t),
                    Sheet(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Wrap(
                            spacing: 10,
                            runSpacing: 8,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              StatusPill(text(t, 'status')),
                              Text(priorityNames[t['priority']] ?? ''),
                            ],
                          ),
                          const SizedBox(height: 16),
                          Text(
                            text(t, 'title'),
                            style: Theme.of(context).textTheme.headlineSmall
                                ?.copyWith(fontWeight: FontWeight.w700),
                          ),
                          const SizedBox(height: 12),
                          Text(
                            text(t, 'address'),
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          const SizedBox(height: 6),
                          Text('Лифт ${text(t, 'elevator_label')}'),
                          TextButton.icon(
                            onPressed: () {
                              final lift = c
                                  .list('elevators')
                                  .where((e) => e['id'] == t['elevator'])
                                  .firstOrNull;
                              if (lift != null) openElevator(context, c, lift);
                            },
                            icon: const Icon(Icons.elevator_outlined),
                            label: const Text('Карточка лифта'),
                          ),
                          if (t['stopped_at'] != null)
                            Text(
                              'Причина остановки: ${text(t, 'stop_reason')}',
                            ),
                          if (t['status'] == 'LONG_REPAIR') ...[
                            Text(text(t, 'long_repair_reason')),
                            if (t['expected_repair_date'] != null)
                              Text(
                                'Ожидаемая дата · ${dateLabel(t['expected_repair_date'], 0, time: false)}',
                              ),
                          ],
                          if (text(t, 'description', '').isNotEmpty) ...[
                            const Divider(),
                            SelectableText(text(t, 'description')),
                          ],
                          const Divider(),
                          Text(
                            'Исполнитель: ${text(t, 'technician_name', 'Не назначен')}',
                          ),
                          if (t['can_assign'] == true)
                            TextButton(
                              onPressed: c.canRecord && !_busy ? _assign : null,
                              child: const Text('Изменить исполнителя'),
                            ),
                          const SizedBox(height: 6),
                          Text(
                            'Создана ${dateLabel(t['created_at'], c.utcOffset)}',
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                          if (t['cancellation_reason'] != null &&
                              text(
                                t,
                                'cancellation_reason',
                                '',
                              ).isNotEmpty) ...[
                            const Divider(),
                            Text('Причина отмены: ${t['cancellation_reason']}'),
                          ],
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                    if (t['can_accept'] == true)
                      FilledButton.icon(
                        onPressed: c.canRecord && _ticketId > 0 && !_busy
                            ? () => _run(() => _transition('accept', {}))
                            : null,
                        icon: const Icon(Icons.play_arrow_outlined),
                        label: Text(
                          _busy ? 'Подтверждение…' : 'Принять заявку',
                        ),
                      ),
                    if (t['can_stop'] == true)
                      OutlinedButton.icon(
                        onPressed: c.canRecord && _ticketId > 0 && !_busy
                            ? () async {
                                final reason = await askText(
                                  context,
                                  title: 'Лифт остановлен',
                                  label:
                                      'Почему не удалось восстановить работу',
                                  minimum: 5,
                                  maximum: 1000,
                                  action: 'Сохранить остановку',
                                );
                                if (reason != null) {
                                  await _run(
                                    () =>
                                        _transition('stop', {'reason': reason}),
                                  );
                                }
                              }
                            : null,
                        icon: const Icon(Icons.stop_circle_outlined),
                        label: const Text('Лифт остановлен'),
                      ),
                    if (t['can_resume'] == true)
                      OutlinedButton.icon(
                        onPressed: c.canRecord && _ticketId > 0 && !_busy
                            ? () => _run(() => _transition('resume', {}))
                            : null,
                        icon: const Icon(Icons.build_outlined),
                        label: const Text('Продолжить ремонт'),
                      ),
                    if (t['can_complete'] == true) ...[
                      OutlinedButton.icon(
                        onPressed: c.canRecord && _ticketId > 0 && !_busy
                            ? _deferRepair
                            : null,
                        icon: const Icon(Icons.schedule),
                        label: const Text('Длительный ремонт'),
                      ),
                      FilledButton.icon(
                        onPressed: c.canRecord && _ticketId > 0 && !_busy
                            ? _complete
                            : null,
                        icon: const Icon(Icons.check_circle_outline),
                        label: const Text('Завершить с отчётом'),
                      ),
                      const SizedBox(height: 10),
                      OutlinedButton.icon(
                        onPressed: !_busy
                            ? () async {
                                await askText(
                                  context,
                                  title: 'Черновик отчёта',
                                  label: 'Выполненные работы',
                                  initial: c.draft(widget.id),
                                  onChanged: (v) => c.setDraft(
                                    widget.id,
                                    v,
                                    widget.organization,
                                  ),
                                  action: 'Готово',
                                );
                                await c.flushDrafts();
                              }
                            : null,
                        icon: const Icon(Icons.edit_note),
                        label: const Text('Черновик отчёта на телефоне'),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'Черновик сохраняется на этом устройстве. Заявка завершится после подтверждения сервера.',
                        style: TextStyle(fontSize: 12),
                      ),
                    ],
                    SectionTitle(
                      'Фотографии',
                      trailing: t['can_complete'] == true
                          ? IconButton(
                              tooltip: 'Прикрепить фото',
                              onPressed:
                                  c.canRecord &&
                                      _ticketId > 0 &&
                                      !_busy &&
                                      photos.length < 8
                                  ? _photo
                                  : null,
                              icon: const Icon(Icons.add_a_photo_outlined),
                            )
                          : null,
                    ),
                    if (photos.isEmpty)
                      const Sheet(child: Text('Фотографий пока нет')),
                    if (photos.isNotEmpty)
                      Wrap(
                        spacing: 10,
                        runSpacing: 10,
                        children: [
                          for (final p in photos)
                            if (p['local_file'] is String)
                              _LocalPhoto(encoded: p['local_file'] as String)
                            else
                              PhotoThumbnail(
                                key: ValueKey('${p['url']}_${c.online}'),
                                controller: c,
                                url: p['url'] as String,
                                organization: widget.organization,
                              ),
                        ],
                      ),
                    SectionTitle(
                      'Комментарии',
                      trailing: canComment
                          ? IconButton(
                              tooltip: 'Добавить комментарий',
                              onPressed: c.canRecord && _ticketId > 0 && !_busy
                                  ? _comment
                                  : null,
                              icon: const Icon(Icons.add_comment_outlined),
                            )
                          : null,
                    ),
                    if (comments.isEmpty)
                      const Sheet(child: Text('Комментариев пока нет')),
                    for (final note in comments)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Sheet(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                text(note, 'author', 'Система'),
                                style: const TextStyle(
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                              const SizedBox(height: 6),
                              SelectableText(text(note, 'body')),
                              if (note['sync_state'] != null)
                                Text(
                                  note['sync_state'] == 'failed'
                                      ? 'Требует внимания · сохранено на телефоне'
                                      : 'Сохранено на телефоне · ожидает отправки',
                                  style: Theme.of(context).textTheme.bodySmall,
                                ),
                              const SizedBox(height: 6),
                              Text(
                                dateLabel(note['created_at'], c.utcOffset),
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ],
                          ),
                        ),
                      ),
                    const SectionTitle('История заявки'),
                    if (events.isEmpty)
                      const Sheet(
                        child: Text('История появится после первого действия'),
                      ),
                    for (final event in events.reversed)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Sheet(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _eventName(text(event, 'action')),
                                style: const TextStyle(
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                              const SizedBox(height: 5),
                              Text(
                                '${text(event, 'actor', 'Система')} · ${dateLabel(event['created_at'], c.utcOffset)}',
                              ),
                              if (text(event, 'reason', '').isNotEmpty) ...[
                                const SizedBox(height: 6),
                                Text(text(event, 'reason')),
                              ],
                            ],
                          ),
                        ),
                      ),
                    if (_busy)
                      const Padding(
                        padding: EdgeInsets.all(12),
                        child: LinearProgressIndicator(),
                      ),
                    const SizedBox(height: 20),
                  ],
                ),
              ),
      );
    },
  );
}

String _eventName(String name) =>
    {
      'CREATED': 'Заявка создана',
      'ACCEPTED': 'Заявка принята',
      'COMPLETED': 'Работа выполнена',
      'STOPPED': 'Лифт остановлен',
      'RESUMED': 'Ремонт продолжен',
      'LONG_REPAIR': 'Длительный ремонт',
      'CANCELED': 'Заявка отменена',
      'REASSIGNED': 'Изменён исполнитель',
      'COMMENTED': 'Добавлен комментарий',
      'PHOTO_ADDED': 'Добавлено фото',
    }[name.toUpperCase()] ??
    name;

class _LongRepairDialog extends StatefulWidget {
  const _LongRepairDialog({required this.today});
  final DateTime today;

  @override
  State<_LongRepairDialog> createState() => _LongRepairDialogState();
}

class _LongRepairDialogState extends State<_LongRepairDialog> {
  final _reason = TextEditingController();
  DateTime? _date;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Длительный ремонт'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _reason,
            maxLength: 1000,
            maxLines: 3,
            decoration: const InputDecoration(labelText: 'Причина'),
            onChanged: (_) => setState(() {}),
          ),
          TextButton.icon(
            icon: const Icon(Icons.event_outlined),
            label: Text(
              _date == null
                  ? 'Ожидаемая дата (необязательно)'
                  : dateLabel(
                      _date!.toIso8601String().substring(0, 10),
                      0,
                      time: false,
                    ),
            ),
            onPressed: () async {
              final selected = await showDatePicker(
                context: context,
                initialDate: _date ?? widget.today,
                firstDate: widget.today,
                lastDate: DateTime(widget.today.year + 5, 12, 31),
              );
              if (selected != null && mounted) setState(() => _date = selected);
            },
          ),
          if (_date != null)
            TextButton(
              onPressed: () => setState(() => _date = null),
              child: const Text('Убрать дату'),
            ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Отмена'),
      ),
      FilledButton(
        onPressed: _reason.text.trim().length < 5
            ? null
            : () => Navigator.pop(context, {
                'reason': _reason.text.trim(),
                if (_date != null)
                  'expected_date': _date!.toIso8601String().substring(0, 10),
              }),
        child: const Text('Сохранить'),
      ),
    ],
  );
}

class _LocalPhoto extends StatelessWidget {
  const _LocalPhoto({required this.encoded});
  final String encoded;
  @override
  Widget build(BuildContext context) {
    final bytes = base64Decode(encoded);
    return SizedBox(
      width: 140,
      child: Column(
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(14),
            child: InkWell(
              onTap: () => showDialog<void>(
                context: context,
                builder: (context) => Dialog(
                  child: InteractiveViewer(
                    child: Image.memory(
                      bytes,
                      errorBuilder: (_, error, stack) =>
                          const Icon(Icons.broken_image_outlined),
                    ),
                  ),
                ),
              ),
              child: Image.memory(
                bytes,
                width: 140,
                height: 140,
                cacheWidth: 360,
                fit: BoxFit.cover,
                errorBuilder: (_, error, stack) => const SizedBox(
                  width: 140,
                  height: 140,
                  child: Icon(Icons.image_outlined),
                ),
              ),
            ),
          ),
          const Text(
            'На телефоне · ожидает отправки',
            style: TextStyle(fontSize: 11),
          ),
        ],
      ),
    );
  }
}

class PhotoThumbnail extends StatefulWidget {
  const PhotoThumbnail({
    super.key,
    required this.controller,
    required this.url,
    required this.organization,
  });
  final LiftController controller;
  final String url;
  final int organization;
  @override
  State<PhotoThumbnail> createState() => _PhotoThumbnailState();
}

class _PhotoThumbnailState extends State<PhotoThumbnail> {
  late final Future<Uint8List> _photo;
  @override
  void initState() {
    super.initState();
    _photo = widget.controller.download(widget.url, widget.organization);
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<Uint8List>(
    future: _photo,
    builder: (context, snap) => SizedBox(
      width: 140,
      height: 140,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(14),
        child: snap.hasData
            ? InkWell(
                onTap: () => showDialog<void>(
                  context: context,
                  builder: (context) => Dialog(
                    child: Stack(
                      children: [
                        InteractiveViewer(child: Image.memory(snap.data!)),
                        Positioned(
                          right: 0,
                          top: 0,
                          child: IconButton(
                            tooltip: 'Закрыть',
                            onPressed: () => Navigator.pop(context),
                            icon: const Icon(Icons.close),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                child: Image.memory(snap.data!, fit: BoxFit.cover),
              )
            : ColoredBox(
                color: Theme.of(context).colorScheme.surface,
                child: Center(
                  child: snap.hasError
                      ? const Icon(Icons.cloud_off_outlined)
                      : const CircularProgressIndicator(),
                ),
              ),
      ),
    ),
  );
}

class CreateTicketPage extends StatefulWidget {
  const CreateTicketPage({super.key, required this.controller, this.elevator});
  final LiftController controller;
  final Json? elevator;
  @override
  State<CreateTicketPage> createState() => _CreateTicketPageState();
}

class _CreateTicketPageState extends State<CreateTicketPage> {
  final _form = GlobalKey<FormState>();
  final _title = TextEditingController(),
      _description = TextEditingController();
  late int _organization;
  Json? _elevator;
  final _additional = <int>{};
  final _assignees = <int, int?>{};
  final _savedTickets = <int, int>{};
  String _priority = 'NORMAL';
  bool _busy = false;
  @override
  void initState() {
    super.initState();
    _organization = widget.controller.organization!;
    _elevator = widget.elevator;
    if (_elevator != null) {
      _assignees[_elevator!['id'] as int] =
          _elevator!['assigned_technician'] as int?;
    }
  }

  @override
  void dispose() {
    _title.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_busy || !_form.currentState!.validate()) return;
    if (_elevator == null) {
      showError(context, const ApiError('Выберите лифт.'));
      return;
    }
    setState(() => _busy = true);
    try {
      final c = widget.controller;
      final selected = [_elevator!['id'] as int, ..._additional];
      Json? ticket;
      for (final lift in selected) {
        if (_savedTickets.containsKey(lift)) continue;
        ticket = Map<String, dynamic>.from(
          await c.request(
            'tickets/',
            method: 'POST',
            data: {
              'elevator': lift,
              'title': _title.text.trim(),
              'description': _description.text.trim(),
              'priority': _priority,
              if (_assignees[lift] != null) 'assigned_to': _assignees[lift],
            },
            expectedOrganization: _organization,
          ),
        );
        _savedTickets[lift] = ticket['id'] as int;
      }
      if (selected.length > 1 && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              'Сохранено заявок: ${_savedTickets.length} · по одной на каждый лифт',
            ),
          ),
        );
        Navigator.pop(context);
        return;
      }
      ticket ??= {'id': _savedTickets.values.first};
      final ticketId = ticket['id'] as int;
      if (mounted) {
        Navigator.pushReplacement(
          context,
          MaterialPageRoute<void>(
            builder: (_) => TicketPage(
              controller: widget.controller,
              id: ticketId,
              organization: _organization,
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: widget.controller,
    organization: _organization,
    builder: (context) => Scaffold(
      appBar: AppBar(title: const Text('Новая заявка')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            Sheet(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('Лифт', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: _busy
                        ? null
                        : () async {
                            final value = await pickElevator(
                              context,
                              widget.controller,
                            );
                            if (value != null && mounted) {
                              setState(() {
                                _elevator = value;
                                _additional.clear();
                                _assignees.clear();
                                _savedTickets.clear();
                                _assignees[value['id'] as int] =
                                    value['assigned_technician'] as int?;
                              });
                            }
                          },
                    icon: const Icon(Icons.elevator_outlined),
                    label: Text(
                      _elevator == null
                          ? 'Выбрать адрес и лифт'
                          : '${text(_elevator!, 'address')} · ${liftLabel(_elevator!)}',
                    ),
                  ),
                  if (_elevator != null) ...[
                    const SizedBox(height: 8),
                    Text(
                      liftDetails(_elevator!),
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    for (final lift
                        in widget.controller
                            .list('elevators')
                            .where(
                              (e) =>
                                  e['active'] != false &&
                                  e['building'] == _elevator!['building'] &&
                                  e['id'] != _elevator!['id'],
                            ))
                      CheckboxListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text('Также лифт ${liftLabel(lift)}'),
                        subtitle: Text(liftDetails(lift)),
                        value: _additional.contains(lift['id']),
                        onChanged: _busy
                            ? null
                            : (v) => setState(() {
                                if (v == true) {
                                  _additional.add(lift['id'] as int);
                                  _assignees[lift['id'] as int] =
                                      lift['assigned_technician'] as int?;
                                } else {
                                  _additional.remove(lift['id']);
                                }
                              }),
                      ),
                    if (_additional.isNotEmpty)
                      const Text(
                        'Для каждого выбранного лифта будет отдельная заявка и история ремонта.',
                      ),
                    for (final liftId in [
                      _elevator!['id'] as int,
                      ..._additional,
                    ])
                      DropdownButtonFormField<int>(
                        key: ValueKey('assignee-$liftId-${_assignees[liftId]}'),
                        initialValue:
                            widget.controller
                                .list('technicians')
                                .any(
                                  (t) =>
                                      t['id'] == _assignees[liftId] &&
                                      t['active'] == true &&
                                      t['can_receive_tasks'] != false,
                                )
                            ? _assignees[liftId]
                            : null,
                        isExpanded: true,
                        decoration: InputDecoration(
                          labelText: _additional.isEmpty
                              ? 'Исполнитель'
                              : 'Исполнитель · лифт ${liftLabel(widget.controller.list('elevators').firstWhere((e) => e['id'] == liftId))}',
                        ),
                        items: widget.controller
                            .list('technicians')
                            .where(
                              (t) =>
                                  t['active'] == true &&
                                  t['can_receive_tasks'] != false,
                            )
                            .map(
                              (t) => DropdownMenuItem<int>(
                                value: t['id'] as int,
                                child: Text(t['full_name'] as String),
                              ),
                            )
                            .toList(),
                        hint: const Text('Не назначен — выберите механика'),
                        onChanged: _busy
                            ? null
                            : (v) => setState(() => _assignees[liftId] = v),
                      ),
                  ],
                  const SizedBox(height: 18),
                  TextFormField(
                    key: const Key('ticket_title'),
                    controller: _title,
                    maxLength: 250,
                    minLines: 1,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'Что случилось',
                    ),
                    validator: (v) => (v?.trim().length ?? 0) < 3
                        ? 'Опишите неисправность'
                        : null,
                  ),
                  const SizedBox(height: 12),
                  TextFormField(
                    controller: _description,
                    maxLength: 4000,
                    minLines: 3,
                    maxLines: 6,
                    decoration: const InputDecoration(
                      labelText: 'Подробности обращения',
                    ),
                  ),
                  const SizedBox(height: 14),
                  DropdownButtonFormField<String>(
                    initialValue: _priority,
                    decoration: const InputDecoration(labelText: 'Приоритет'),
                    items: priorityNames.entries
                        .where(
                          (e) =>
                              e.key != 'ENTRAPMENT' ||
                              widget.controller.me['field_ops_api'] == 1,
                        )
                        .map(
                          (e) => DropdownMenuItem(
                            value: e.key,
                            child: Text(e.value),
                          ),
                        )
                        .toList(),
                    onChanged: _busy
                        ? null
                        : (v) => setState(() => _priority = v!),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            FilledButton(
              onPressed: _busy || !widget.controller.canCreateTicket
                  ? null
                  : _submit,
              child: Text(_busy ? 'Создание…' : 'Создать заявку'),
            ),
            const SizedBox(height: 12),
            const Text(
              'Без интернета заявка сохраняется на телефоне и отправляется автоматически после подключения.',
              style: TextStyle(fontSize: 12),
            ),
          ],
        ),
      ),
    ),
  );
}

Future<Json?> pickElevator(BuildContext context, LiftController c) async {
  var search = '';
  return showModalBottomSheet<Json>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (context) => StatefulBuilder(
      builder: (context, set) {
        final rows = c
            .list('elevators')
            .where(
              (e) => '${e['address']} ${liftLabel(e)} ${e['entrance']}'
                  .toLowerCase()
                  .contains(search.toLowerCase()),
            )
            .toList();
        return SafeArea(
          child: Padding(
            padding: EdgeInsets.fromLTRB(
              18,
              0,
              18,
              MediaQuery.viewInsetsOf(context).bottom + 12,
            ),
            child: SizedBox(
              height: MediaQuery.sizeOf(context).height * 0.66,
              child: Column(
                children: [
                  const SectionTitle('Выбрать лифт'),
                  TextField(
                    autofocus: true,
                    decoration: const InputDecoration(
                      hintText: 'Адрес или номер лифта',
                      prefixIcon: Icon(Icons.search),
                    ),
                    onChanged: (v) => set(() => search = v),
                  ),
                  const SizedBox(height: 12),
                  Expanded(
                    child: ListView.builder(
                      itemCount: rows.length,
                      itemBuilder: (context, i) => ListTile(
                        leading: const Icon(Icons.elevator_outlined),
                        title: Text(text(rows[i], 'address')),
                        subtitle: Text(
                          '${liftLabel(rows[i])} · ${liftDetails(rows[i])}',
                        ),
                        onTap: () => Navigator.pop(context, rows[i]),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    ),
  );
}
