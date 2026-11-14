import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';
import 'media_widgets.dart';
import 'media_library.dart';
import 'tickets.dart';
import 'extras.dart';
import 'dart:async';

class ChatPage extends StatefulWidget {
  const ChatPage({super.key, required this.controller, this.messageId,
    this.quoteKind, this.quoteId, this.quoteTitle});
  final LiftController controller;
  final int? messageId;
  final String? quoteKind;
  final int? quoteId;
  final String? quoteTitle;
  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final _input = TextEditingController();
  late final int _org;
  final _files = <XFile>[];
  bool _busy = false;
  Json? _selected;
  Json? _quote;
  bool get _canRecord => c.me['media_api'] == 1 ? c.canRecord : c.canWrite;
  LiftController get c => widget.controller;
  @override
  void initState() {
    super.initState();
    _org = c.organization!;
    if (widget.quoteKind != null && widget.quoteId != null) {
      _quote = {'kind': widget.quoteKind, 'id': widget.quoteId,
        'title': widget.quoteTitle ?? 'Карточка'};
    }
    if (widget.messageId != null) unawaited(_loadMessage());
  }

  Future<void> _loadMessage() async {
    try {
      final value = await c.resource(
        'chat/${widget.messageId}/',
        expectedOrganization: _org,
      );
      if (mounted) setState(() => _selected = Map<String, dynamic>.from(value));
    } catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  Future<void> _chooseQuote() async {
    final next = await showModalBottomSheet<Json>(
      context: context, showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          const ListTile(title: Text('Цитировать заявку или запчасть')),
          for (final ticket in c.list('tickets'))
            ListTile(
              leading: const Icon(Icons.assignment_outlined),
              title: Text('#${ticket['id']} · ${text(ticket, 'title')}'),
              subtitle: Text(text(ticket, 'address')),
              onTap: () => Navigator.pop(sheetContext, {
                'kind': 'ticket', 'id': ticket['id'], 'title': text(ticket, 'title')}),
            ),
          for (final part in c.list('parts'))
            ListTile(
              leading: const Icon(Icons.inventory_2_outlined),
              title: Text('#${part['id']} · ${text(part, 'part')}'),
              subtitle: Text(text(part, 'address')),
              onTap: () => Navigator.pop(sheetContext, {
                'kind': 'part', 'id': part['id'], 'title': text(part, 'part')}),
            ),
        ]),
      ),
    );
    if (next != null && mounted) setState(() => _quote = next);
  }

  Future<void> _openQuote(Json reference) async {
    final id = reference['id'];
    if (id is! int) return;
    if (reference['kind'] == 'ticket') {
      openTicket(context, c, id);
      return;
    }
    if (reference['kind'] == 'part') {
      try {
        final result = await c.resource('parts/$id/', expectedOrganization: _org);
        if (!mounted) return;
        Navigator.push(context, MaterialPageRoute<void>(
          builder: (_) => PartForm(controller: c, item: Map<String,dynamic>.from(result)),
        ));
      } catch (e) {
        if (mounted) showError(context, e);
      }
    }
  }

  Future<void> _pick() async {
    final source = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (final item in [
              ('photo-camera', 'Сделать фото', Icons.camera_alt_outlined),
              (
                'photo-gallery',
                'Фото из галереи',
                Icons.photo_library_outlined,
              ),
              ('video-camera', 'Снять видео', Icons.videocam_outlined),
              (
                'video-gallery',
                'Видео из галереи',
                Icons.video_library_outlined,
              ),
            ])
              ListTile(
                leading: Icon(item.$3),
                title: Text(item.$2),
                onTap: () => Navigator.pop(context, item.$1),
              ),
          ],
        ),
      ),
    );
    if (source == null || !mounted || !_canRecord) return;
    setState(() => _busy = true);
    try {
      final picker = ImagePicker();
      List<XFile> picked = [];
      if (source == 'photo-gallery') {
        picked = await picker.pickMultiImage(
          maxWidth: 1920,
          maxHeight: 1920,
          imageQuality: 78,
        );
      } else if (source == 'photo-camera') {
        final file = await picker.pickImage(
          source: ImageSource.camera,
          maxWidth: 1920,
          maxHeight: 1920,
          imageQuality: 78,
        );
        if (file != null) picked = [file];
      } else {
        final file = await picker.pickVideo(
          source: source == 'video-camera'
              ? ImageSource.camera
              : ImageSource.gallery,
          maxDuration: const Duration(minutes: 3),
        );
        if (file != null) picked = [file];
      }
      if (picked.isNotEmpty && source.startsWith('photo-')) {
        c.log.record('photo_prepared');
      }
      final next = [..._files, ...picked];
      var size = 0;
      for (final file in next) {
        size += await file.length();
      }
      if (next.length > 5 || size > 20 * 1024 * 1024) {
        throw const ApiError('До 5 вложений и 20 МБ на сообщение.');
      }
      if (mounted && c.organization == _org) {
        setState(() {
          _files.clear();
          _files.addAll(next);
        });
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _send() async {
    if (_busy || _input.text.trim().isEmpty && _files.isEmpty && _quote == null) return;
    setState(() => _busy = true);
    try {
      if (_files.isEmpty) {
        await c.request(
          'chat/',
          method: 'POST',
          data: {'body': _input.text.trim(),
            if (_quote?['kind'] == 'ticket') 'linked_ticket': _quote?['id'],
            if (_quote?['kind'] == 'part') 'linked_part': _quote?['id'],
          },
          expectedOrganization: _org,
        );
      } else {
        final files = [
          for (final file in _files)
            (bytes: await file.readAsBytes(), name: file.name),
        ];
        await c.uploadMedia(
          'chat/',
          files,
          _org,
          fields: {'body': _input.text.trim(),
            if (_quote?['kind'] == 'ticket') 'linked_ticket': '${_quote?['id']}',
            if (_quote?['kind'] == 'part') 'linked_part': '${_quote?['id']}',
          },
        );
      }
      if (mounted) {
        _input.clear();
        setState(() { _files.clear(); _quote = null; });
      }
      if (c.me['media_api'] != 1) await c.refresh();
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
    builder: (context) {
      final rows = widget.messageId == null
          ? c.list('chat')
          : _selected == null
          ? <Json>[]
          : [_selected!];
      return Scaffold(
        appBar: AppBar(
          title: const Text('Чат бригады'),
          actions: [
            IconButton(
              tooltip: 'Вложения чата',
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) => MediaPage(controller: c, chat: true),
                ),
              ),
              icon: const Icon(Icons.photo_library_outlined),
            ),
            if (widget.messageId != null)
              TextButton(
                onPressed: () => Navigator.pushReplacement(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) => ChatPage(controller: c),
                  ),
                ),
                child: const Text('Весь чат'),
              ),
            IconButton(
              tooltip: 'Обновить',
              onPressed: c.refresh,
              icon: const Icon(Icons.sync),
            ),
          ],
        ),
        body: SafeArea(
          child: Column(
            children: [
              Expanded(
                child: rows.isEmpty
                    ? const EmptyState(
                        'Сообщений пока нет',
                        'Чат видят сотрудники этой организации.',
                        icon: Icons.chat_bubble_outline,
                      )
                    : ListView.builder(
                        reverse: true,
                        padding: const EdgeInsets.all(14),
                        itemCount: rows.length,
                        itemBuilder: (context, index) {
                          final row = rows[rows.length - index - 1],
                              own = row['author'] == c.me['id'];
                          return Align(
                            alignment: own
                                ? Alignment.centerRight
                                : Alignment.centerLeft,
                            child: Container(
                              constraints: const BoxConstraints(maxWidth: 560),
                              margin: const EdgeInsets.only(bottom: 12),
                              padding: const EdgeInsets.all(15),
                              decoration: BoxDecoration(
                                color: own
                                    ? Theme.of(context).colorScheme.primary
                                          .withValues(alpha: 0.12)
                                    : Theme.of(context).colorScheme.surface,
                                borderRadius: BorderRadius.circular(18),
                              ),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    text(row, 'author_name'),
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w700,
                                      fontSize: 12,
                                    ),
                                  ),
                                  if (row['delivery_state'] != null)
                                    Text(
                                      '${row['delivery_state']}',
                                      style: const TextStyle(fontSize: 11),
                                    ),
                                  if (row['reference'] is Map)
                                    ListTile(
                                      contentPadding: EdgeInsets.zero,
                                      leading: Icon(row['reference']['kind'] == 'ticket'
                                          ? Icons.assignment_outlined : Icons.inventory_2_outlined),
                                      title: Text('${row['reference']['kind'] == 'ticket' ? 'Заявка' : 'Запчасть'} #${row['reference']['id']} · ${row['reference']['title']}'),
                                      subtitle: Text('${row['reference']['address']} · ${row['reference']['status']}'),
                                      onTap: () => _openQuote(Map<String, dynamic>.from(row['reference'])),
                                    ),
                                  if (text(row, 'body', '').isNotEmpty)
                                    SelectableText(text(row, 'body')),
                                  for (final attachment in records(
                                    row['attachments'] ?? [],
                                  ))
                                    PrivateMedia(
                                      key: ValueKey(attachment['id']),
                                      controller: c,
                                      organization: _org,
                                      attachment: attachment,
                                    ),
                                  const SizedBox(height: 6),
                                  Text(
                                    dateLabel(row['created_at'], c.utcOffset),
                                    style: Theme.of(
                                      context,
                                    ).textTheme.bodySmall,
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
              ),
              if (_quote != null)
                ListTile(
                  leading: const Icon(Icons.link_outlined),
                  title: Text('${_quote?['kind'] == 'ticket' ? 'Заявка' : 'Запчасть'} #${_quote?['id']}'),
                  subtitle: Text(text(_quote!, 'title')),
                  trailing: IconButton(
                    icon: const Icon(Icons.close),
                    tooltip: 'Убрать ссылку',
                    onPressed: _busy ? null : () => setState(() => _quote = null),
                  ),
                ),
              if (_files.isNotEmpty)
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 100),
                  child: SingleChildScrollView(
                    child: Wrap(
                      children: [
                        for (var index = 0; index < _files.length; index++)
                          Padding(
                            padding: const EdgeInsets.only(left: 8),
                            child: InputChip(
                              label: ConstrainedBox(
                                constraints: const BoxConstraints(
                                  maxWidth: 180,
                                ),
                                child: Text(
                                  _files[index].name,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                              onDeleted: _busy
                                  ? null
                                  : () =>
                                        setState(() => _files.removeAt(index)),
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
              if (c.me['field_ops_api'] == 1)
                const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 14),
                  child: Text(
                    'Фото до 10 МБ; видео до 20 МБ и 3 минут.',
                    style: TextStyle(fontSize: 11),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 8, 8, 12),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    IconButton(
                      tooltip: 'Цитировать заявку или запчасть',
                      onPressed: _canRecord && !_busy ? _chooseQuote : null,
                      icon: const Icon(Icons.link_outlined),
                    ),
                    if (c.me['field_ops_api'] == 1)
                      IconButton(
                        tooltip: 'Фото и видео',
                        onPressed: _canRecord && !_busy ? _pick : null,
                        icon: const Icon(Icons.attach_file),
                      ),
                    Expanded(
                      child: TextField(
                        key: const Key('chat_message'),
                        controller: _input,
                        enabled: !_busy,
                        minLines: 1,
                        maxLines: 5,
                        maxLength: 4000,
                        decoration: const InputDecoration(
                          hintText: 'Сообщение',
                          counterText: '',
                        ),
                      ),
                    ),
                    const SizedBox(width: 7),
                    IconButton.filled(
                      tooltip: 'Отправить',
                      onPressed: _canRecord && !_busy ? _send : null,
                      icon: _busy
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.send_outlined),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
    },
  );
}
