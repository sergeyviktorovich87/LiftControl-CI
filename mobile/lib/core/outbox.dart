import 'dart:convert';

import 'package:flutter/services.dart';

import 'api.dart';
import 'storage.dart';

abstract class Outbox {
  bool get nativeSync;
  Future<List<Json>> rows(String scope);
  Future<void> enqueue(String scope, List<Json> rows);
  Future<void> acknowledge(String scope, String id);
  Future<void> retry(String scope, String id);
  Future<void> configure(String scope, Json session);
  Future<void> clearAuth();
  Future<void> sync(String scope);
  Future<void> finish(
    String scope,
    String id,
    String state, {
    Json? response,
    String error = '',
  });
}

class DeviceOutbox implements Outbox {
  static const channel = MethodChannel('ru.liftcontrol.app/offline');
  @override
  bool get nativeSync => true;
  @override
  Future<List<Json>> rows(String scope) async => records(
    jsonDecode((await channel.invokeMethod<String>('rows', {'scope': scope}))!),
  );
  @override
  Future<void> enqueue(String scope, List<Json> rows) =>
      channel.invokeMethod<void>('enqueue', {
        'scope': scope,
        'rows': jsonEncode(rows),
      });
  @override
  Future<void> acknowledge(String scope, String id) =>
      channel.invokeMethod<void>('acknowledge', {'scope': scope, 'id': id});
  @override
  Future<void> retry(String scope, String id) =>
      channel.invokeMethod<void>('retry', {'scope': scope, 'id': id});
  @override
  Future<void> configure(String scope, Json session) =>
      channel.invokeMethod<void>('configure', {
        'scope': scope,
        'value': jsonEncode(session),
      });
  @override
  Future<void> clearAuth() => channel.invokeMethod<void>('clearAuth');
  @override
  Future<void> sync(String scope) =>
      channel.invokeMethod<void>('sync', {'scope': scope});
  @override
  Future<void> finish(
    String scope,
    String id,
    String state, {
    Json? response,
    String error = '',
  }) => throw UnsupportedError('Native sync owns receipts');
}

/// The same persistent contract for tests; Android uses encrypted SQLite rows.
class StoredOutbox implements Outbox {
  StoredOutbox(this.store);
  final SecretStore store;
  Future<void> _writes = Future.value();
  @override
  bool get nativeSync => false;
  @override
  Future<List<Json>> rows(String scope) async {
    await _writes;
    return records(jsonDecode(await store.read('outbox_$scope') ?? '[]'));
  }

  Future<void> _change(String scope, void Function(List<Json>) change) {
    final next = _writes.then((_) async {
      final rows = records(
        jsonDecode(await store.read('outbox_$scope') ?? '[]'),
      );
      change(rows);
      await store.write('outbox_$scope', jsonEncode(rows));
    });
    _writes = next.catchError((Object _) {});
    return next;
  }

  @override
  Future<void> enqueue(String scope, List<Json> rows) => _change(scope, (
    saved,
  ) {
    if (saved.where((r) => r['state'] != 'synced').length + rows.length > 500) {
      throw const ApiError(
        'Очередь заполнена. Сначала отправьте сохранённые операции.',
      );
    }
    saved.addAll(rows.map((r) => {...r, 'state': 'queued'}));
  });
  @override
  Future<void> acknowledge(String scope, String id) => _change(
    scope,
    (rows) => rows.removeWhere((r) => r['id'] == id && r['state'] == 'synced'),
  );
  @override
  Future<void> retry(String scope, String id) => _change(scope, (rows) {
    for (final row in rows) {
      if (row['id'] == id && row['state'] == 'failed') {
        row['state'] = 'queued';
        row['error'] = '';
      }
    }
  });
  @override
  Future<void> finish(
    String scope,
    String id,
    String state, {
    Json? response,
    String error = '',
  }) => _change(scope, (rows) {
    for (final row in rows) {
      if (row['id'] == id) {
        row['state'] = state;
        row['error'] = error;
        if (response != null) row['response'] = response;
      }
    }
  });
  @override
  Future<void> configure(String scope, Json session) async {}
  @override
  Future<void> clearAuth() async {}
  @override
  Future<void> sync(String scope) async {}
}
