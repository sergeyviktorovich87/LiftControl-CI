import 'dart:convert';
import 'package:flutter/services.dart';
import 'api.dart';
import 'storage.dart';

abstract class PendingMediaFiles {
  Future<Json> stage(String scope, String id, Uint8List bytes);
  Future<Uint8List> read(String scope, String id);
  Future<void> remove(String scope, String id);
}

class DeviceMediaFiles implements PendingMediaFiles {
  static const channel = MethodChannel('ru.liftcontrol.app/offline');
  @override
  Future<Json> stage(String scope, String id, Uint8List bytes) async =>
      Map<String, dynamic>.from(
        await channel.invokeMethod<dynamic>('mediaStage', {
              'scope': scope,
              'id': id,
              'bytes': bytes,
            })
            as Map,
      );
  @override
  Future<Uint8List> read(String scope, String id) async => (await channel
      .invokeMethod<Uint8List>('mediaRead', {'scope': scope, 'id': id}))!;
  @override
  Future<void> remove(String scope, String id) =>
      channel.invokeMethod<void>('mediaRemove', {'scope': scope, 'id': id});
}

class DownloadCache {
  static const _channel = DeviceMediaFiles.channel;

  Future<({Uint8List bytes, String etag})?> read(
    String scope,
    String resource,
  ) async {
    final row = await _channel.invokeMapMethod<String, dynamic>(
      'downloadRead',
      {'scope': scope, 'resource': resource},
    );
    if (row == null) return null;
    return (bytes: row['bytes'] as Uint8List, etag: row['etag'] as String);
  }

  Future<void> write(
    String scope,
    String resource,
    Uint8List bytes,
    String etag,
    int limit,
  ) => _channel.invokeMethod<void>('downloadWrite', {
    'scope': scope,
    'resource': resource,
    'bytes': bytes,
    'etag': etag,
    'limit': limit,
  });

  Future<void> remove(String scope, String resource) =>
      _channel.invokeMethod<void>('downloadRemove', {
        'scope': scope,
        'resource': resource,
      });

  Future<void> clear(String scope) =>
      _channel.invokeMethod<void>('downloadClear', {'scope': scope});
}

class StoredMediaFiles implements PendingMediaFiles {
  StoredMediaFiles(this.store);
  final SecretStore store;
  String key(String scope, String id) => 'media_pending_${scope}_$id';
  @override
  Future<Json> stage(String scope, String id, Uint8List bytes) async {
    if (bytes.isEmpty || bytes.length > 20 * 1024 * 1024) {
      throw const ApiError('Файл — до 20 МБ.');
    }
    await store.write(key(scope, id), base64Encode(bytes));
    return {'file_id': id, 'size': bytes.length};
  }

  @override
  Future<Uint8List> read(String scope, String id) async =>
      base64Decode((await store.read(key(scope, id)))!);
  @override
  Future<void> remove(String scope, String id) => store.delete(key(scope, id));
}
