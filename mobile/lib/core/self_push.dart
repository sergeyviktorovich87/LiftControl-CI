import 'dart:async';

import 'package:http/http.dart' as http;

import 'api.dart';
import 'device_bridge.dart';
import 'diagnostics.dart';
import 'storage.dart';

/// Opportunistic HTTPS fallback while Flutter is running.
///
/// Not a background push daemon: Android Doze can suspend Dart timers.
/// Future foreground-service transport will reuse the same API, cursors and
/// native event deduplication, rather than introducing another message format.
class SelfPushPoller {
  SelfPushPoller({
    required this.server,
    required this.token,
    required this.userId,
    required this.organization,
    required this.store,
    required this.log,
    required this.isCurrent,
    this.transport,
  });

  final Uri server;
  final String token;
  final int userId;
  final int organization;
  final SecretStore store;
  final Diagnostics log;
  final bool Function() isCurrent;
  final http.Client? transport;

  Timer? _timer;
  bool _stopped = false;
  bool _working = false;
  bool get stopped => _stopped;

  String get _scope => storageScope(server, userId, organization);
  String get _ticketKey => 'self_push_ticket_$_scope';
  String get _taskKey => 'self_push_task_$_scope';

  void start() {
    _timer ??= Timer.periodic(const Duration(seconds: 20), (_) {
      unawaited(poll());
    });
    unawaited(poll());
  }

  void stop() {
    _stopped = true;
    _timer?.cancel();
    _timer = null;
  }

  Future<void> poll() async {
    if (_stopped || _working) return;
    if (!isCurrent()) {
      stop();
      return;
    }
    _working = true;
    final client = ApiClient(
      server: server,
      token: token,
      organization: organization,
      log: log,
      transport: transport,
    );
    try {
      final previousTickets = await store.read(_ticketKey);
      final previousTasks = await store.read(_taskKey);
      if (_stopped || !isCurrent()) return;
      final bootstrap = previousTickets == null || previousTasks == null;
      final response = Map<String, dynamic>.from(
        await client.call(
          'mobile/push/self/',
          query: {
            if (bootstrap) 'bootstrap': '1',
            'tickets_after': previousTickets ?? '0',
            'tasks_after': previousTasks ?? '0',
          },
        ) as Map,
      );
      if (_stopped || !isCurrent()) return;
      final events = response['events'];
      if (events is! List ||
          response['tickets_after'] is! int ||
          response['tasks_after'] is! int) {
        throw const ApiError('Неверный ответ собственного канала уведомлений.');
      }
      if (events.length > 100) {
        throw const ApiError('Превышен размер пачки уведомлений.');
      }
      for (final raw in events) {
        if (_stopped || !isCurrent()) return;
        if (raw is! Map) continue;
        final event = Map<String, dynamic>.from(raw);
        // The native bridge shares FCM's event-ID based deduplication.
        await DeviceBridge.showSelfEvent(event);
      }
      if (_stopped || !isCurrent()) return;
      await store.write(_ticketKey, '${response['tickets_after']}');
      await store.write(_taskKey, '${response['tasks_after']}');
      log.record('self_push_poll_ok');
    } on ApiError catch (error) {
      if (error.status == 403 || error.status == 404) {
        stop(); // A server without this opt-in feature must not be hammered.
        log.record('self_push_unavailable');
      } else {
        log.record('self_push_poll_failed');
      }
    } catch (_) {
      log.record('self_push_poll_failed');
    } finally {
      client.close();
      _working = false;
    }
  }
}
