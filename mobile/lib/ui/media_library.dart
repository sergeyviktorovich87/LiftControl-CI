import 'dart:async';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';
import 'media_widgets.dart';
import 'tickets.dart';
import 'chat.dart';

class MediaPage extends StatefulWidget {
  const MediaPage({
    super.key,
    required this.controller,
    this.elevator,
    this.ticket,
    this.chat = false,
  });
  final LiftController controller;
  final int? elevator, ticket;
  final bool chat;
  @override
  State<MediaPage> createState() => _MediaPageState();
}

class _MediaPageState extends State<MediaPage> {
  LiftController get c => widget.controller;
  late final int _org;
  final _address = TextEditingController();
  List<Json> _items = [];
  Json? _stats;
  int? _next, _author, _elevator, _ticket;
  int _request = 0;
  final Map<int, String> _authors = {};
  String _type = '', _error = '';
  DateTimeRange? _range;
  bool _busy = false;
  @override
  void initState() {
    super.initState();
    _org = c.organization!;
    _elevator = widget.elevator;
    _ticket = widget.ticket;
    unawaited(_load());
  }

  @override
  void dispose() {
    _address.dispose();
    super.dispose();
  }

  Future<void> _load({bool more = false}) async {
    if (more && _busy) {
      return;
    }
    final request = ++_request;
    setState(() {
      _busy = true;
      _error = '';
    });
    final query = <String, String>{
      if (_elevator != null) 'elevator': '$_elevator',
      if (_ticket != null) 'ticket': '$_ticket',
      if (widget.chat) 'source': 'chat',
      if (_type.isNotEmpty) 'type': _type,
      if (_author != null) 'author': '$_author',
      if (_address.text.trim().isNotEmpty) 'address': _address.text.trim(),
      if (more && _next != null) 'offset': '$_next',
      if (_range != null) 'from': _date(_range!.start),
      if (_range != null) 'to': _date(_range!.end),
    };
    final path =
        'media/${query.isEmpty ? '' : '?${Uri(queryParameters: query).query}'}';
    try {
      final data = await c.resource(path, expectedOrganization: _org);
      Json? stats;
      if (c.me['can_manage_access'] == true && c.canWrite) {
        stats = Map<String, dynamic>.from(
          await c.request('media/stats/', expectedOrganization: _org),
        );
      }
      if (mounted && request == _request) {
        setState(() {
          _items = [if (more) ..._items, ...records(data['results'])];
          for (final item in _items) {
            if (item['author'] is int) {
              _authors[item['author'] as int] = '${item['author_name']}';
            }
          }
          _next = data['next'] as int?;
          _stats = stats;
        });
      }
    } catch (e) {
      if (mounted && request == _request) {
        setState(() => _error = e.toString());
      }
    } finally {
      if (mounted && request == _request) {
        setState(() => _busy = false);
      }
    }
  }

  String _date(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  Future<void> _dates() async {
    final value = await showDateRangePicker(
      context: context,
      firstDate: DateTime(2020),
      lastDate: DateTime.now().add(const Duration(days: 1)),
      initialDateRange: _range,
    );
    if (value != null && mounted) {
      setState(() => _range = value);
      await _load();
    }
  }

  Future<void> _pick() async {
    final video = await showModalBottomSheet<bool>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              title: const Text('Фото из галереи'),
              leading: const Icon(Icons.photo_library_outlined),
              onTap: () => Navigator.pop(context, false),
            ),
            ListTile(
              title: const Text('Видео из галереи'),
              leading: const Icon(Icons.videocam_outlined),
              onTap: () => Navigator.pop(context, true),
            ),
          ],
        ),
      ),
    );
    if (video == null || !mounted) {
      return;
    }
    setState(() => _busy = true);
    try {
      final picker = ImagePicker();
      List<XFile> files;
      if (video) {
        final file = await picker.pickVideo(
          source: ImageSource.gallery,
          maxDuration: const Duration(minutes: 3),
        );
        files = file == null ? [] : [file];
      } else {
        files = await picker.pickMultiImage(
          maxWidth: 1920,
          maxHeight: 1920,
          imageQuality: 78,
        );
      }
      if (files.isEmpty) {
        return;
      }
      await c.queueMedia(
        _ticket != null
            ? 'ticket'
            : _elevator != null
            ? 'elevator'
            : 'library',
        [for (final f in files) (bytes: await f.readAsBytes(), name: f.name)],
        target: _ticket ?? _elevator,
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Сохранено на телефоне. Отправится автоматически.'),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        showError(context, e);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
    if (c.canWrite) {
      await _load();
    }
  }

  Future<void> _link(Json asset) async {
    var kind = 'ticket', category = 'OTHER';
    int? selected;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) {
          final choices = c.list(kind == 'ticket' ? 'tickets' : 'elevators');
          return AlertDialog(
            title: const Text('Связать файл'),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  DropdownButtonFormField<String>(
                    initialValue: kind,
                    items: const [
                      DropdownMenuItem(value: 'ticket', child: Text('Заявка')),
                      DropdownMenuItem(value: 'elevator', child: Text('Лифт')),
                    ],
                    onChanged: (v) => update(() {
                      kind = v!;
                      selected = null;
                    }),
                  ),
                  DropdownButtonFormField<int>(
                    key: ValueKey(kind),
                    initialValue: selected,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Запись'),
                    items: [
                      for (final row in choices)
                        DropdownMenuItem(
                          value: row['id'] as int,
                          child: Text(
                            kind == 'ticket'
                                ? '#${row['id']} · ${row['title']}'
                                : '${row['address']} · ${liftLabel(row)}',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (v) => update(() => selected = v),
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: category,
                    items: [
                      for (final value in {
                        'OTHER': 'Прочее',
                        'DEFECT': 'Неисправность',
                        'PROGRESS': 'Ход работ',
                        'RESULT': 'Результат ремонта',
                      }.entries)
                        DropdownMenuItem(
                          value: value.key,
                          child: Text(value.value),
                        ),
                    ],
                    onChanged: (v) => category = v!,
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
                onPressed: selected == null
                    ? null
                    : () => Navigator.pop(context, true),
                child: const Text('Связать'),
              ),
            ],
          );
        },
      ),
    );
    if (confirmed == true && selected != null) {
      try {
        await c.request(
          'media/${asset['id']}/links/',
          method: 'POST',
          data: {'kind': kind, 'target_id': selected, 'category': category},
          expectedOrganization: _org,
        );
        await _load();
      } catch (e) {
        if (mounted) {
          showError(context, e);
        }
      }
    }
  }

  bool get _mayUpload {
    if (c.me['can_manage'] == true) {
      return true;
    }
    if (_ticket != null) {
      return c.localTicket(_ticket!)['can_complete'] == true;
    }
    if (_elevator != null) {
      return c.me['on_duty'] == true ||
          c
              .list('elevators')
              .any(
                (e) =>
                    e['id'] == _elevator &&
                    e['assigned_technician'] == c.me['technician'],
              );
    }
    return true;
  }

  List<Json> get _pending => [
    for (final row in c.pendingOperations)
      if (row['command']['type'] == 'media' &&
          row['command']['body']['file_id'] != null)
        if ((!widget.chat || row['command']['body']['kind'] == 'chat') &&
            (_ticket == null ||
                (row['command']['body']['kind'] == 'ticket' &&
                    row['command']['body']['target_id'] == _ticket)) &&
            (_elevator == null ||
                (row['command']['body']['kind'] == 'elevator' &&
                    row['command']['body']['target_id'] == _elevator) ||
                (row['command']['body']['kind'] == 'ticket' &&
                    c.localTicket(
                          row['command']['body']['target_id'] as int,
                        )['elevator'] ==
                        _elevator)))
          {
            'id': row['id'],
            'pending': true,
            'local_scope': c.scope,
            'file_id': row['command']['body']['file_id'],
            'size': row['command']['body']['size'],
            'name': row['command']['body']['name'],
            'created_at': row['command']['occurred_at'],
            'author_name': c.me['name'] ?? '',
            'content_type': LiftController.mediaType(
              '${row['command']['body']['name']}',
            ),
            'links': <Json>[],
          },
  ];

  String _month(Json item) {
    final date = DateTime.parse(
      item['created_at'] as String,
    ).toUtc().add(Duration(minutes: c.utcOffset));
    const names = [
      'Январь',
      'Февраль',
      'Март',
      'Апрель',
      'Май',
      'Июнь',
      'Июль',
      'Август',
      'Сентябрь',
      'Октябрь',
      'Ноябрь',
      'Декабрь',
    ];
    return '${names[date.month - 1]} ${date.year}';
  }

  List<Widget> _gallery() {
    final result = <Widget>[];
    if (_pending.isNotEmpty) {
      result.add(const SectionTitle('Сохранено на телефоне'));
      result.addAll(_pending.map(_card));
    }
    final groups = <String, List<Json>>{};
    for (final item in _items) {
      final tickets = records(item['links']).where(
        (l) =>
            l['kind'] == 'ticket' &&
            (_elevator == null || l['elevator'] == _elevator),
      );
      final group = widget.elevator != null
          ? (tickets.isEmpty
                ? 'Общие материалы лифта'
                : 'Заявка #${tickets.first['id']}')
          : _month(item);
      (groups[group] ??= []).add(item);
    }
    for (final group in groups.entries) {
      result.add(SectionTitle(group.key));
      result.addAll(group.value.map(_card));
    }
    return result;
  }

  Widget _card(Json item) => Padding(
    padding: const EdgeInsets.only(top: 12),
    child: Sheet(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${dateLabel(item['created_at'], c.utcOffset)} · ${item['author_name']}',
          ),
          Text(
            '${item['name']} · ${((item['size'] as num) / 1024).toStringAsFixed(0)} КБ',
          ),
          PrivateMedia(
            key: ValueKey('${item['id']}_${c.online}'),
            controller: c,
            organization: _org,
            attachment: item,
          ),
          if (item['pending'] == true)
            const Text('Ожидает отправки. Файл сохранён на телефоне.'),
          for (final link in records(item['links']))
            Text(
              '${link['kind'] == 'ticket'
                  ? 'Заявка #${link['id']}'
                  : link['kind'] == 'message'
                  ? 'Сообщение #${link['id']}'
                  : link['kind'] == 'elevator'
                  ? 'Лифт #${link['id']}'
                  : 'Задание #${link['id']}'} · ${link['address']}',
            ),
          if (item['pending'] != true)
            Wrap(
              children: [
                TextButton(
                  onPressed: c.canWrite ? () => _link(item) : null,
                  child: const Text('Связать с заявкой / лифтом'),
                ),
                for (final link in records(item['links']))
                  if (link['kind'] == 'ticket')
                    TextButton(
                      onPressed: () =>
                          openTicket(context, c, link['id'] as int),
                      child: Text('Открыть заявку #${link['id']}'),
                    )
                  else if (link['kind'] == 'message')
                    TextButton(
                      onPressed: () => Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                          builder: (_) => ChatPage(
                            controller: c,
                            messageId: link['id'] as int,
                          ),
                        ),
                      ),
                      child: const Text('Исходное сообщение'),
                    ),
              ],
            ),
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: c,
    organization: _org,
    builder: (context) => Scaffold(
      appBar: AppBar(
        title: const Text('Фото и видео'),
        actions: [
          IconButton(
            tooltip: 'Обновить',
            onPressed: _busy ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_stats != null)
            Text(
              'Учтено: ${((_stats!['total'] as num) / 1024 / 1024).toStringAsFixed(1)} МБ · ${_stats!['count']} файлов. Автоудаление выключено.',
            ),
          if (!c.online)
            const Text('Без связи: сохранённый каталог и очередь телефона.'),
          TextField(
            controller: _address,
            decoration: const InputDecoration(
              labelText: 'Адрес',
              suffixIcon: Icon(Icons.search),
            ),
            onSubmitted: (_) => _load(),
          ),
          Wrap(
            spacing: 8,
            children: [
              DropdownButton<String>(
                value: _type,
                items: const [
                  DropdownMenuItem(value: '', child: Text('Все типы')),
                  DropdownMenuItem(value: 'image', child: Text('Фото')),
                  DropdownMenuItem(value: 'video', child: Text('Видео')),
                ],
                onChanged: (v) {
                  setState(() => _type = v!);
                  unawaited(_load());
                },
              ),
              TextButton(
                onPressed: _dates,
                child: Text(
                  _range == null
                      ? 'Период'
                      : '${_date(_range!.start)} — ${_date(_range!.end)}',
                ),
              ),
              if (_range != null)
                IconButton(
                  tooltip: 'Все даты',
                  onPressed: () {
                    setState(() => _range = null);
                    unawaited(_load());
                  },
                  icon: const Icon(Icons.clear),
                ),
            ],
          ),
          if (widget.elevator == null && widget.ticket == null)
            DropdownButtonFormField<int>(
              initialValue: _elevator,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Лифт'),
              items: [
                const DropdownMenuItem<int>(
                  value: null,
                  child: Text('Все лифты'),
                ),
                for (final e in c.list('elevators'))
                  DropdownMenuItem(
                    value: e['id'] as int,
                    child: Text(
                      '${e['address']} · ${liftLabel(e)}',
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: (v) {
                setState(() {
                  _elevator = v;
                  _ticket = null;
                });
                unawaited(_load());
              },
            ),
          if (widget.ticket == null)
            DropdownButtonFormField<int>(
              key: ValueKey('ticket-filter-$_elevator'),
              initialValue: _ticket,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Заявка'),
              items: [
                const DropdownMenuItem<int>(
                  value: null,
                  child: Text('Все заявки'),
                ),
                for (final t
                    in c
                        .list('tickets')
                        .where(
                          (t) =>
                              _elevator == null || t['elevator'] == _elevator,
                        ))
                  DropdownMenuItem(
                    value: t['id'] as int,
                    child: Text(
                      '#${t['id']} · ${t['title']}',
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: (v) {
                setState(() => _ticket = v);
                unawaited(_load());
              },
            ),
          if (_authors.isNotEmpty)
            DropdownButtonFormField<int>(
              initialValue: _author,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Автор'),
              items: [
                const DropdownMenuItem<int>(
                  value: null,
                  child: Text('Все авторы'),
                ),
                for (final entry in _authors.entries)
                  DropdownMenuItem(value: entry.key, child: Text(entry.value)),
              ],
              onChanged: (v) {
                setState(() => _author = v);
                unawaited(_load());
              },
            ),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed:
                !_busy && c.canRecord && c.me['media_api'] == 1 && _mayUpload
                ? _pick
                : null,
            icon: const Icon(Icons.add_photo_alternate_outlined),
            label: const Text('Добавить фото / видео'),
          ),
          if (c.pendingCount > 0)
            Text(
              'Ожидают отправки: ${c.pendingCount}. Подробности — в очереди отправки.',
            ),
          if (_error.isNotEmpty) Text(_error),
          if (_busy) const LinearProgressIndicator(),
          ..._gallery(),
          if (!_busy && _items.isEmpty && _pending.isEmpty)
            const EmptyState(
              'Материалов пока нет',
              'Фото и видео появятся здесь после отправки на сервер.',
            ),
          if (_next != null)
            TextButton(
              onPressed: _busy ? null : () => _load(more: true),
              child: const Text('Показать ещё'),
            ),
        ],
      ),
    ),
  );
}
