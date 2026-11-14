import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter/services.dart';

abstract class SecretStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> clear();
  Future<void> delete(String key);
}

class DeviceStore implements SecretStore {
  final FlutterSecureStorage _storage = const FlutterSecureStorage();
  static const _database = MethodChannel('ru.liftcontrol.app/offline');
  bool _local(String key) =>
      ['cache_', 'resources_', 'drafts_'].any(key.startsWith);
  @override
  Future<String?> read(String key) async {
    if (!_local(key)) return _storage.read(key: key);
    final saved = await _database.invokeMethod<String>('read', {'key': key});
    if (saved != null) return saved;
    // Migrate the existing encrypted cache without losing reports on update.
    final previous = await _storage.read(key: key);
    if (previous != null) {
      await write(key, previous);
      await _storage.delete(key: key);
    }
    return previous;
  }

  @override
  Future<void> write(String key, String value) => _local(key)
      ? _database.invokeMethod<void>('write', {'key': key, 'value': value})
      : _storage.write(key: key, value: value);
  @override
  Future<void> clear() async {
    await _storage.deleteAll();
    await _database.invokeMethod<void>('clearCache');
  }

  @override
  Future<void> delete(String key) async {
    if (_local(key)) await _database.invokeMethod<void>('delete', {'key': key});
    await _storage.delete(key: key);
  }
}

class MemoryStore implements SecretStore {
  final Map<String, String> values = {};
  @override
  Future<String?> read(String key) async => values[key];
  @override
  Future<void> write(String key, String value) async {
    values[key] = value;
  }

  @override
  Future<void> clear() async {
    values.removeWhere(
      (key, _) =>
          !key.startsWith('outbox_') && !key.startsWith('media_pending_'),
    );
  }

  @override
  Future<void> delete(String key) async {
    values.remove(key);
  }
}

String storageScope(Uri server, int user, int organization) =>
    '${base64Url.encode(utf8.encode(server.origin))}_${user}_$organization';
