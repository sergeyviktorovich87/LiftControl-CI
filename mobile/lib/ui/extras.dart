import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:flutter/services.dart';
import '../core/api.dart';
import '../core/controller.dart';
import '../core/device_bridge.dart';
import 'common.dart';
import 'journal.dart';
import 'platform_bridge.dart';
import 'tickets.dart';
import 'staff.dart';
import 'device_settings.dart';
import 'chat.dart';
import 'tasks.dart';
import 'media_library.dart';
import 'media_widgets.dart';
import 'commissioning.dart';
import 'notifications.dart';

class MorePage extends StatelessWidget {
  const MorePage({super.key, required this.controller});
  final LiftController controller;
  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(18),
    children: [
      const SectionTitle('Уведомления'),
      _link(
        context,
        Icons.notifications_outlined,
        'Центр уведомлений',
        'История, прочитанные и новые, переход к записи',
        NotificationsPage(controller: controller),
      ),
      const SizedBox(height: 16),
      if (controller.me['commissioning_only'] != true) ...[
        const SectionTitle('Рабочие разделы'),
        _link(
          context,
          Icons.photo_library_outlined,
          'Медиаматериалы',
          'Фото и видео организации',
          MediaPage(controller: controller),
        ),
        const SizedBox(height: 12),
        _link(
          context,
          Icons.assignment_ind_outlined,
          'Личные задания',
          'Задания, назначенные лично вам',
          TasksPage(controller: controller),
        ),
        const SizedBox(height: 12),
        _link(
          context,
          Icons.build_outlined,
          'Журнал ТО',
          'Обслуживание, осмотры и ремонт',
          MaintenancePage(controller: controller),
        ),
        const SizedBox(height: 12),
        _link(
          context,
          Icons.chat_bubble_outline,
          'Чат бригады',
          'Сообщения выбранной организации',
          ChatPage(controller: controller),
        ),
        const SizedBox(height: 12),
        _link(
          context,
          Icons.inventory_2_outlined,
          'Запчасти',
          'Заявки на материалы',
          PartsPage(controller: controller),
        ),
        const SizedBox(height: 22),
        if (controller.organization != null) ...[
          _link(
            context,
            Icons.people_outline,
            'Сотрудники',
            'Команда, аккаунты и приглашения',
            StaffPage(controller: controller),
          ),
          const SizedBox(height: 12),
        ],
      ],
      if (controller.me['can_commissioning'] == true &&
          controller.me['commissioning_only'] != true) ...[
        const SectionTitle('Пусконаладка'),
        _link(
          context,
          Icons.precision_manufacturing_outlined,
          'Пусконаладка',
          'Объекты, лифты, прогресс, допработы, личные расходы',
          CommissioningPage(
            key: ValueKey(controller.organization),
            controller: controller,
          ),
        ),
        const SizedBox(height: 12),
      ],
      const SectionTitle('Аккаунт'),
      _link(
        context,
        Icons.settings_outlined,
        'Профиль и настройки',
        'Тема, устройства и диагностические логи',
        SettingsPage(controller: controller),
      ),
      const SizedBox(height: 12),
      FutureBuilder<Json>(
        future: DeviceBridge.status(),
        builder: (context, snapshot) {
          final version = snapshot.data?['version']?.toString();
          final code = snapshot.data?['version_code']?.toString();
          return Card(
            child: ListTile(
              key: const Key('software-version'),
              leading: const Icon(Icons.info_outline),
              title: const Text('Версия ПО'),
              subtitle: Text(
                version == null || version.isEmpty
                    ? 'Android: версия недоступна'
                    : 'Android $version · сборка ${code ?? '—'}',
              ),
              trailing: controller.hasUpdate
                  ? const Icon(Icons.system_update_alt)
                  : const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                  builder: (_) => SettingsPage(controller: controller),
                ),
              ),
            ),
          );
        },
      ),
    ],
  );
  Widget _link(
    BuildContext context,
    IconData icon,
    String title,
    String subtitle,
    Widget page,
  ) => Card(
    child: ListTile(
      contentPadding: const EdgeInsets.all(16),
      leading: Icon(icon, color: Theme.of(context).colorScheme.primary),
      title: Text(title, style: const TextStyle(fontWeight: FontWeight.w700)),
      subtitle: Text(subtitle),
      trailing: const Icon(Icons.chevron_right),
      onTap: () => Navigator.push(
        context,
        MaterialPageRoute<void>(builder: (_) => page),
      ),
    ),
  );
}

const partNames = {
  'NEW': 'Новая',
  'APPROVED': 'Одобрена',
  'ORDERED': 'Заказана',
  'RECEIVED': 'Получена',
  'ISSUED': 'Выдана',
  'CANCELED': 'Отменена',
};

class PartsPage extends StatelessWidget {
  const PartsPage({super.key, required this.controller});
  final LiftController controller;
  @override
  Widget build(BuildContext context) {
    final org = controller.organization!;
    return ScopePage(
      controller: controller,
      organization: org,
      builder: (context) => Scaffold(
        appBar: AppBar(
          title: const Text('Запчасти'),
          actions: [
            IconButton(
              tooltip: 'Новая заявка на запчасть',
              onPressed: controller.canWrite
                  ? () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => PartForm(controller: controller),
                      ),
                    )
                  : null,
              icon: const Icon(Icons.add),
            ),
          ],
        ),
        body: RefreshIndicator(
          onRefresh: controller.refresh,
          child: ListView(
            padding: const EdgeInsets.all(18),
            physics: const AlwaysScrollableScrollPhysics(),
            children: [
              if (controller.list('parts').isEmpty)
                const EmptyState(
                  'Запросов пока нет',
                  'Механик может запросить запчасть для конкретного лифта.',
                  icon: Icons.inventory_2_outlined,
                ),
              for (final p in controller.list('parts').reversed)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Sheet(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '${text(p, 'part')} · ${p['quantity']} шт.',
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 8),
                        Text(text(p, 'address')),
                        Text('Лифт ${text(p, 'elevator_label')}'),
                        const SizedBox(height: 8),
                        Text(partNames[p['status']] ?? text(p, 'status')),
                        if (text(p, 'notes', '').isNotEmpty) ...[
                          const SizedBox(height: 6),
                          Text(text(p, 'notes')),
                        ],
                        if (p['can_edit'] == true)
                          TextButton.icon(
                            onPressed: controller.canWrite
                                ? () => Navigator.push(
                                    context,
                                    MaterialPageRoute<void>(
                                      builder: (_) => PartForm(
                                        controller: controller,
                                        item: p,
                                      ),
                                    ),
                                  )
                                : null,
                            icon: const Icon(Icons.edit_outlined),
                            label: const Text('Редактировать'),
                          ),
                        TextButton.icon(
                          onPressed: () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => ChatPage(
                                controller: controller,
                                quoteKind: 'part',
                                quoteId: p['id'] as int,
                                quoteTitle: text(p, 'part'),
                              ),
                            ),
                          ),
                          icon: const Icon(Icons.chat_bubble_outline),
                          label: const Text('Обсудить в чате'),
                        ),
                        if (records(p['photos'] ?? []).isNotEmpty)
                          ExpansionTile(
                            tilePadding: EdgeInsets.zero,
                            title: Text(
                              'Фотографии (${records(p['photos']).length})',
                            ),
                            children: [
                              for (final photo in records(p['photos']))
                                PrivateMedia(
                                  key: ValueKey(photo['id']),
                                  controller: controller,
                                  organization: org,
                                  attachment: photo,
                                ),
                            ],
                          ),
                        if (controller.me['can_manage'] == true)
                          TextButton(
                            onPressed: controller.canWrite
                                ? () async {
                                    final status = await showDialog<String>(
                                      context: context,
                                      builder: (context) => SimpleDialog(
                                        title: const Text('Статус запчасти'),
                                        children: [
                                          for (final entry in partNames.entries)
                                            SimpleDialogOption(
                                              onPressed: () => Navigator.pop(
                                                context,
                                                entry.key,
                                              ),
                                              child: Text(entry.value),
                                            ),
                                        ],
                                      ),
                                    );
                                    if (status == null) return;
                                    try {
                                      await controller.request(
                                        'parts/${p['id']}/',
                                        method: 'PATCH',
                                        data: {'status': status},
                                        expectedOrganization: org,
                                      );
                                      await controller.refresh();
                                    } catch (e) {
                                      if (context.mounted) {
                                        showError(context, e);
                                      }
                                    }
                                  }
                                : null,
                            child: const Text('Изменить статус'),
                          ),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class PartForm extends StatefulWidget {
  const PartForm({super.key, required this.controller, this.item});
  final LiftController controller;
  final Json? item;
  @override
  State<PartForm> createState() => _PartFormState();
}

class _PartFormState extends State<PartForm> {
  final _form = GlobalKey<FormState>();
  final _part = TextEditingController(),
      _quantity = TextEditingController(text: '1'),
      _notes = TextEditingController();
  Json? _elevator;
  late int _org;
  bool _busy = false;
  int? _savedId;
  final List<XFile> _picked = [];
  late final List<Json> _photos;
  bool get _frozen =>
      ['ORDERED', 'RECEIVED', 'ISSUED'].contains(widget.item?['status']);
  bool get _canEdit => widget.item == null || widget.item?['can_edit'] == true;
  @override
  void initState() {
    super.initState();
    _org = widget.controller.organization!;
    final item = widget.item;
    _savedId = item?['id'] as int?;
    _part.text = text(item ?? {}, 'part');
    _quantity.text = '${item?['quantity'] ?? 1}';
    _notes.text = text(item ?? {}, 'notes');
    _photos = records(
      item?['photos'] ?? [],
    ).map((v) => Map<String, dynamic>.from(v)).toList();
    for (final lift in widget.controller.list('elevators')) {
      if (lift['id'] == item?['elevator']) {
        _elevator = lift;
        break;
      }
    }
  }

  @override
  void dispose() {
    _part.dispose();
    _quantity.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_busy || !_canEdit || _frozen || !_form.currentState!.validate())
      return;
    if (_elevator == null) {
      showError(context, const ApiError('Выберите лифт.'));
      return;
    }
    setState(() => _busy = true);
    try {
      if (_savedId == null || widget.item != null) {
        final result = await widget.controller.request(
          _savedId == null ? 'parts/' : 'parts/$_savedId/',
          method: _savedId == null ? 'POST' : 'PATCH',
          data: {
            'elevator': _elevator!['id'],
            'part': _part.text.trim(),
            'quantity': int.parse(_quantity.text),
            'notes': _notes.text.trim(),
          },
          expectedOrganization: _org,
        );
        _savedId = result['id'] as int;
      }
      for (final image in List<XFile>.from(_picked)) {
        await widget.controller.uploadMedia(
          'parts/$_savedId/photos/',
          [(bytes: await image.readAsBytes(), name: image.name)],
          _org,
          field: 'file',
        );
        _picked.remove(image);
      }
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
    builder: (context) => Scaffold(
      appBar: AppBar(
        title: Text(
          widget.item == null ? 'Запросить запчасть' : 'Редактировать запчасть',
        ),
      ),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            OutlinedButton.icon(
              onPressed: _busy || !_canEdit || _frozen
                  ? null
                  : () async {
                      final e = await pickElevator(context, widget.controller);
                      if (e != null && mounted) setState(() => _elevator = e);
                    },
              icon: const Icon(Icons.elevator_outlined),
              label: Text(
                _elevator == null
                    ? 'Выбрать лифт'
                    : '${text(_elevator!, 'address')} · ${liftLabel(_elevator!)}',
              ),
            ),
            const SizedBox(height: 16),
            TextFormField(
              controller: _part,
              enabled: !_busy && _canEdit && !_frozen,
              maxLength: 250,
              decoration: const InputDecoration(labelText: 'Название запчасти'),
              validator: (v) =>
                  (v?.trim().length ?? 0) < 2 ? 'Укажите запчасть' : null,
            ),
            const SizedBox(height: 14),
            TextFormField(
              controller: _quantity,
              enabled: !_busy && _canEdit && !_frozen,
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(labelText: 'Количество'),
              validator: (v) {
                final n = int.tryParse(v ?? '');
                return n == null || n < 1 || n > 32767
                    ? 'Количество от 1 до 32767'
                    : null;
              },
            ),
            const SizedBox(height: 16),
            TextFormField(
              controller: _notes,
              enabled: !_busy && _canEdit && !_frozen,
              minLines: 3,
              maxLines: 6,
              maxLength: 4000,
              decoration: const InputDecoration(
                labelText: 'Артикул, размеры, примечание',
              ),
            ),
            const SizedBox(height: 16),
            const Text('Фотографии маркировки и запчасти'),
            for (final photo in _photos) ...[
              PrivateMedia(
                key: ValueKey(photo['id']),
                controller: widget.controller,
                organization: _org,
                attachment: photo,
              ),
              if (_canEdit && !_frozen)
                TextButton.icon(
                  onPressed: _busy
                      ? null
                      : () async {
                          setState(() => _busy = true);
                          try {
                            await widget.controller.request(
                              'parts/$_savedId/photos/${photo['id']}/',
                              method: 'DELETE',
                              expectedOrganization: _org,
                            );
                            if (mounted)
                              setState(
                                () => _photos.removeWhere(
                                  (p) => p['id'] == photo['id'],
                                ),
                              );
                            await widget.controller.refresh();
                          } catch (e) {
                            if (context.mounted) showError(context, e);
                          } finally {
                            if (mounted) setState(() => _busy = false);
                          }
                        },
                  icon: const Icon(Icons.delete_outline),
                  label: const Text('Удалить фотографию'),
                ),
            ],
            for (final picked in _picked)
              InputChip(
                label: Text(picked.name),
                onDeleted: _busy
                    ? null
                    : () => setState(() => _picked.remove(picked)),
              ),
            if (_canEdit && !_frozen)
              OutlinedButton.icon(
                onPressed: _busy
                    ? null
                    : () async {
                        try {
                          final files = await ImagePicker().pickMultiImage(
                            maxWidth: 2560,
                            maxHeight: 2560,
                            imageQuality: 82,
                          );
                          if (files.length + _picked.length + _photos.length >
                              8) {
                            throw const ApiError('Не более восьми фотографий.');
                          }
                          for (final f in files) {
                            if (await f.length() > 10 * 1024 * 1024) {
                              throw const ApiError('Фото больше 10 МБ.');
                            }
                          }
                          if (mounted) setState(() => _picked.addAll(files));
                        } catch (e) {
                          if (context.mounted) showError(context, e);
                        }
                      },
                icon: const Icon(Icons.add_a_photo_outlined),
                label: const Text('Добавить фотографии'),
              ),
            if (_frozen)
              const Text(
                'Заказана поставщику: состав и фото нельзя переписывать.',
              ),
            const SizedBox(height: 16),
            if (_canEdit && !_frozen)
              FilledButton(
                onPressed: widget.controller.canWrite && !_busy ? _save : null,
                child: Text(
                  _busy
                      ? 'Сохранение…'
                      : widget.item == null
                      ? 'Отправить запрос'
                      : 'Сохранить изменения',
                ),
              ),
            if (_savedId != null) ...[
              const SizedBox(height: 12),
              OutlinedButton.icon(
                icon: const Icon(Icons.chat_bubble_outline),
                label: const Text('Обсудить в чате'),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) => ChatPage(
                      controller: widget.controller,
                      quoteKind: 'part',
                      quoteId: _savedId,
                      quoteTitle: _part.text,
                    ),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    ),
  );
}

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key, required this.controller});
  final LiftController controller;
  Future<void> _logout(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Выйти из аккаунта?'),
        content: const Text(
          'Данные и черновики этого аккаунта будут удалены с телефона.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Остаться'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Выйти'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final revoked = await controller.logout();
    try {
      await PlatformBridge.clearPrivateFiles();
    } catch (_) {
      controller.log.record('private_file_cleanup_failed');
    }
    if (context.mounted) {
      Navigator.popUntil(context, (r) => r.isFirst);
      if (!revoked) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Выход на телефоне выполнен. Сервер не подтвердил отзыв сеанса.',
            ),
          ),
        );
      }
    }
  }

  Future<void> _logs(BuildContext context) async {
    await controller.log.flush();
    Json? device;
    List<dynamic>? nativeEvents;
    try {
      device = await DeviceBridge.status();
      nativeEvents =
          jsonDecode(
                await DeviceBridge.channel.invokeMethod<String>(
                      'nativeDiagnostics',
                    ) ??
                    '[]',
              )
              as List;
    } catch (_) {
      /* Older clients and non-Android test hosts have no native bridge. */
    }
    final report = controller.log.export(
      device: device,
      nativeEvents: nativeEvents,
    );
    if (!context.mounted) return;
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Диагностический отчёт'),
        content: SizedBox(
          width: 560,
          child: SingleChildScrollView(
            child: SelectableText(
              report,
              style: const TextStyle(fontFamily: 'monospace', fontSize: 11),
            ),
          ),
        ),
        actions: [
          TextButton.icon(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: report));
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Отчёт скопирован')),
                );
              }
            },
            icon: const Icon(Icons.copy),
            label: const Text('Копировать'),
          ),
          TextButton.icon(
            onPressed: () async {
              try {
                await PlatformBridge.shareReport(report);
              } catch (e) {
                if (context.mounted) showError(context, e);
              }
            },
            icon: const Icon(Icons.share_outlined),
            label: const Text('Поделиться'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Закрыть'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: const Text('Профиль и настройки')),
      body: ListView(
        padding: const EdgeInsets.all(18),
        children: [
          Sheet(
            child: Row(
              children: [
                CircleAvatar(
                  radius: 27,
                  backgroundColor: pale,
                  foregroundColor: forest,
                  child: Text(
                    text(controller.me, 'name', 'П').substring(0, 1),
                    style: const TextStyle(fontWeight: FontWeight.w800),
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        text(controller.me, 'name'),
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      Text(
                        '${roleNames[controller.me['role']] ?? ''} · ${text(controller.me, 'organization', 'Организация не выбрана')}',
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          Sheet(
            child: DropdownButtonFormField<String>(
              initialValue: controller.appearance,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Тема оформления'),
              items: [
                for (final option in const {
                  'light': 'Светлая',
                  'dark': 'Тёмная',
                  'auto': 'Как в системе',
                  'blue': 'Синяя',
                  'graphite': 'Графитовая',
                  'green': 'Зелёная',
                  'purple': 'Фиолетовая',
                }.entries)
                  DropdownMenuItem(
                    value: option.key,
                    child: Text(option.value),
                  ),
              ],
              onChanged: (value) {
                if (value != null) unawaited(controller.setAppearance(value));
              },
            ),
          ),
          const SizedBox(height: 16),
          if (controller.demo)
            const Sheet(child: Text('Системные уведомления'))
          else
            NotificationSettings(controller: controller),
          const SizedBox(height: 16),
          if (!controller.demo) UpdatesSettings(controller: controller),
          const SizedBox(height: 16),
          if (!controller.demo && controller.organization != null) ...[
            Sheet(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  DropdownButtonFormField<int>(
                    initialValue: controller.cacheLimit,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Кэш скачанных файлов',
                    ),
                    items: [
                      for (final size in LiftController.cacheLimits)
                        DropdownMenuItem(value: size, child: Text('$size МБ')),
                    ],
                    onChanged: (size) {
                      if (size != null)
                        unawaited(controller.setCacheLimit(size));
                    },
                  ),
                  TextButton(
                    onPressed: () async {
                      try {
                        await controller.clearDownloadedFiles();
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text('Кэш скачанных файлов очищен'),
                            ),
                          );
                        }
                      } catch (error) {
                        if (context.mounted) showError(context, error);
                      }
                    },
                    child: const Text('Очистить скачанные файлы'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
          ],
          Sheet(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Подключение',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 8),
                SelectableText(
                  controller.demo
                      ? 'Демонстрационный режим'
                      : controller.serverText,
                ),
                const SizedBox(height: 8),
                if (controller.me['can_manage_access'] == true)
                  TextButton.icon(
                    onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => StaffPage(controller: controller),
                      ),
                    ),
                    icon: const Icon(Icons.people_outline),
                    label: const Text('Управление сотрудниками'),
                  ),
                if (!controller.demo)
                  TextButton.icon(
                    onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                        builder: (_) => SessionsPage(controller: controller),
                      ),
                    ),
                    icon: const Icon(Icons.devices_outlined),
                    label: const Text('Устройства и сеансы'),
                  ),
                if (!controller.demo)
                  TextButton.icon(
                    onPressed: controller.online
                        ? () async {
                            final code = await askText(
                              context,
                              title: 'Добавить организацию',
                              label: 'Код приглашения',
                              minimum: 20,
                              maximum: 100,
                              action: 'Присоединиться',
                            );
                            if (code != null) {
                              try {
                                await controller.join(code);
                                if (context.mounted) {
                                  Navigator.popUntil(context, (r) => r.isFirst);
                                }
                              } catch (e) {
                                if (context.mounted) showError(context, e);
                              }
                            }
                          }
                        : null,
                    icon: const Icon(Icons.business_outlined),
                    label: const Text('Добавить организацию по приглашению'),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          Sheet(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Тестовая версия',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 8),
                FutureBuilder<Json>(
                  future: DeviceBridge.status(),
                  builder: (context, snapshot) {
                    final version = snapshot.data?['version']?.toString();
                    final code = snapshot.data?['version_code']?.toString();
                    return Text(
                      version == null || version.isEmpty
                          ? 'Android: версия недоступна'
                          : 'Android $version · сборка ${code ?? '—'}',
                    );
                  },
                ),
                const SizedBox(height: 8),
                const Text(
                  'Диагностика хранится на телефоне. В отчёт не включаются пароли, ключи входа, фотографии и тексты заявок.',
                  style: TextStyle(fontSize: 12),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () => _logs(context),
                  icon: const Icon(Icons.bug_report_outlined),
                  label: const Text('Диагностический отчёт'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          OutlinedButton.icon(
            onPressed: () => _logout(context),
            icon: const Icon(Icons.logout),
            label: Text(
              controller.demo ? 'Выйти из демо' : 'Выйти из аккаунта',
            ),
          ),
          const SizedBox(height: 20),
        ],
      ),
    ),
  );
}

class SessionsPage extends StatefulWidget {
  const SessionsPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<SessionsPage> createState() => _SessionsPageState();
}

class _SessionsPageState extends State<SessionsPage> {
  List<Json> _rows = [];
  String _error = '';
  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final rows = await widget.controller.request('mobile/sessions/');
      if (mounted) {
        setState(() {
          _rows = records(rows);
          _error = '';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Устройства и сеансы')),
    body: ListView(
      padding: const EdgeInsets.all(18),
      children: [
        if (_error.isNotEmpty) Text(_error),
        for (final row in _rows)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Sheet(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${text(row, 'device_name')}${row['current'] == true ? ' · это устройство' : ''}',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Последняя активность: ${dateLabel(row['last_seen_at'], widget.controller.utcOffset)}',
                  ),
                  if (row['current'] != true)
                    TextButton(
                      onPressed: () async {
                        try {
                          await widget.controller.request(
                            'mobile/sessions/${row['id']}/revoke/',
                            method: 'POST',
                            data: {},
                          );
                          await _load();
                        } catch (e) {
                          if (context.mounted) showError(context, e);
                        }
                      },
                      child: const Text('Завершить этот сеанс'),
                    ),
                ],
              ),
            ),
          ),
        const SizedBox(height: 12),
        const Text(
          'Сеанс действует до 30 дней. Смена пароля завершает вход во всех Android-устройствах.',
          style: TextStyle(fontSize: 12),
        ),
      ],
    ),
  );
}
