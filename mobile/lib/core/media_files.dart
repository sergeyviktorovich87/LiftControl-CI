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
