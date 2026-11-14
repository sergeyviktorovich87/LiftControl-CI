import 'dart:convert';
import 'storage.dart';

/// An allowlisted ring buffer. Never accepts a body, URL, token or exception text.
class Diagnostics {
  Diagnostics(this.store);
  final SecretStore store;
  final List<Map<String, Object>> _events = [];
  Future<void> _pending = Future.value();
  static const build = String.fromEnvironment(
    'BUILD_COMMIT',
    defaultValue: 'local',
  );
  static const version = String.fromEnvironment('APP_VERSION', defaultValue: 'не определена');

  Future<void> restore() async {
    try {
      final value = await store.read('diagnostics');
      if (value != null) {
        final list = jsonDecode(value) as List;
        for (final row in list.take(200)) {
          _events.add(Map<String, Object>.from(row as Map));
        }
      }
    } catch (_) {
      /* A corrupt report must never prevent login. */
    }
  }

  void record(String event, {String? route, int? status, int? elapsed}) {
    final row = <String, Object>{
      'at': DateTime.now().toUtc().toIso8601String(),
      'event': event,
    };
    if (route != null) {
      row['route'] = route
          .split('?')
          .first
          .replaceAll(RegExp(r'\b\d+\b'), ':id');
    }
    if (status != null) row['status'] = status;
    if (elapsed != null) row['ms'] = elapsed;
    _events.add(row);
    if (_events.length > 200) _events.removeAt(0);
    final payload = jsonEncode(_events);
    _pending = _pending
        .then((_) => store.write('diagnostics', payload))
        .catchError((Object _) {});
  }

  Future<void> flush() => _pending;
  Future<void> clear() async {
    await flush();
    _events.clear();
    await store.delete('diagnostics');
  }

  String export({Map<String, dynamic>? device, List<dynamic>? nativeEvents}) =>
      const JsonEncoder.withIndent('  ').convert({
        'application': 'LiftControl Android',
        'version': device?['version'] ?? version,
        'commit': build,
        'platform': 'Android',
        'events': _events,
        'device': ?device,
        'native_events': ?nativeEvents,
      });
}
