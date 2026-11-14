import 'dart:async';
import 'package:flutter/material.dart';
import '../core/api.dart';
import '../core/controller.dart';
import '../core/device_bridge.dart';
import 'common.dart';

class NotificationSettings extends StatefulWidget {
  const NotificationSettings({super.key, required this.controller});
  final LiftController controller;
  @override
  State<NotificationSettings> createState() => _NotificationSettingsState();
}

class _NotificationSettingsState extends State<NotificationSettings>
    with WidgetsBindingObserver {
  Json _status = {};
  bool _ready = false, _busy = false, _fcmReady = false, _ownReady = false;
  String _error = '';
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    unawaited(_load());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final status = await DeviceBridge.status();
      final config = await widget.controller.request('mobile/push/');
      if (mounted) {
        setState(() {
          _status = status;
          _fcmReady = config['enabled'] == true;
          _ownReady = config['self_hosted'] == true;
          _ready = _fcmReady || _ownReady;
          _error = '';
        });
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _error =
              'Не удалось проверить уведомления. Обновите сервер и повторите проверку.',
        );
      }
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
      await _load();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Sheet(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Системные уведомления',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        Text(
          _ready
              ? 'Уведомления подключаются автоматически после входа.'
              : 'Доставка уведомлений ещё не подключена. Сообщите администратору.',
        ),
        Text(
          _status['notifications_allowed'] == true
              ? 'Android разрешает системные уведомления.'
              : 'Уведомления Android отключены или разрешение ещё не выдано.',
        ),
        Text(
          'Google FCM: ${_fcmReady ? 'настроен' : 'не настроен'}. '
          'LiftControl Push: ${_ownReady ? 'включён' : 'выключен'}.',
        ),
        Text(
          _ownReady
              ? 'Наш канал получает уведомления напрямую, пока приложение активно. Фоновая служба ещё разрабатывается.'
              : (_status['active'] == true
                    ? 'Уведомления FCM подключены.'
                    : 'Подключение FCM будет выполнено автоматически при доступной связи.'),
        ),
        if (_error.isNotEmpty) Text(_error),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            OutlinedButton(
              onPressed: !_busy ? () => _run(DeviceBridge.testPush) : null,
              child: const Text('Проверить звук'),
            ),
            TextButton(
              onPressed: !_busy
                  ? () => _run(DeviceBridge.notificationSettings)
                  : null,
              child: const Text('Настройки Android'),
            ),
          ],
        ),
      ],
    ),
  );
}

class UpdatesSettings extends StatefulWidget {
  const UpdatesSettings({super.key, required this.controller});
  final LiftController controller;
  @override
  State<UpdatesSettings> createState() => _UpdatesSettingsState();
}

class _UpdatesSettingsState extends State<UpdatesSettings> {
  Json _device = {}, _catalogue = {};
  bool _busy = false;
  String _error = '';
  @override
  void initState() {
    super.initState();
    unawaited(_check());
  }

  Future<void> _check() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      final device = await DeviceBridge.status();
      final data = await widget.controller.checkUpdates(force: true);
      if (mounted) {
        setState(() {
          _device = device;
          _catalogue = data ?? {};
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _install(Json release) async {
    setState(() => _busy = true);
    final c = widget.controller;
    try {
      await c.installUpdate(release);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final release = _catalogue['android'] is Map
        ? Map<String, dynamic>.from(_catalogue['android'])
        : null;
    final available =
        release != null &&
        (release['version_code'] as int) >
            ((_device['version_code'] as num?)?.toInt() ?? 0);
    return Sheet(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Обновления', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(
            'Android ${_device['version'] ?? 'не определена'} · сборка ${_device['version_code'] ?? '—'}',
          ),
          if (_catalogue['installed'] is Map)
            Text('Панель и сервер ${_catalogue['installed']['panel']}'),
          Text(
            available
                ? 'Доступна версия ${release['version']}'
                : 'Новых опубликованных обновлений приложения нет.',
          ),
          if (available && text(release, 'notes', '').isNotEmpty)
            Text(text(release, 'notes')),
          if (_error.isNotEmpty) Text(_error),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton(
                onPressed: !_busy && widget.controller.canWrite ? _check : null,
                child: Text(
                  _busy ? 'Проверка / загрузка…' : 'Проверить обновления',
                ),
              ),
              if (available)
                FilledButton(
                  onPressed: !_busy && widget.controller.canWrite
                      ? () => _install(release)
                      : null,
                  child: const Text('Скачать и установить'),
                ),
            ],
          ),
          const Text(
            'Перед установкой проверяются файл, номер версии и подпись приложения. Установку подтверждает Android.',
            style: TextStyle(fontSize: 12),
          ),
        ],
      ),
    );
  }
}
