import 'dart:typed_data';
import 'package:flutter_test/flutter_test.dart';
import 'package:liftcontrol_mobile/core/api.dart';
import 'package:liftcontrol_mobile/core/storage.dart';
import 'offline_test.dart'
    show OfflineServer, controller, login, FailingQueueStore;

void main() {
  test(
    'Media bytes survive restart and logout and remain isolated by organization',
    () async {
      final store = MemoryStore(), server = OfflineServer()..mediaApi = true;
      var c = controller(store, server);
      await login(c);
      server.offline = true;
      await c.refresh();
      await c.queueMedia('chat', [
        (bytes: Uint8List.fromList([1, 2, 3]), name: 'evidence.png'),
      ], body: 'Фото без связи');
      await c.refresh();
      final key = c.scope,
          row = c.pendingOperations.single,
          id = row['command']['body']['file_id'] as String;
      expect(await c.pendingMedia(key, id), [1, 2, 3]);
      expect(c.list('chat').single['delivery_state'], contains('телефоне'));
      c.dispose();
      c = controller(store, server);
      await c.initialize();
      expect(c.pendingCount, 1);
      expect(await c.pendingMedia(key, id), [1, 2, 3]);
      await c.selectOrganization(2);
      expect(c.pendingCount, 0);
      await expectLater(c.pendingMedia(key, id), throwsA(isA<ApiError>()));
      await c.selectOrganization(1);
      await c.logout();
      expect(
        store.values.keys.any((k) => k.startsWith('media_pending_')),
        true,
      );
      c.dispose();
      server.offline = false;
      c = controller(store, server);
      await login(c);
      expect(server.writes, 1);
      expect(c.pendingCount, 0, reason: c.log.export());
      expect(c.list('chat').single['body'], 'Фото без связи');
      expect(
        store.values.keys.any((k) => k.startsWith('media_pending_')),
        false,
      );
      c.dispose();
    },
  );
  test(
    'Losing an upload response preserves the same operation and original until confirmed',
    () async {
      final store = MemoryStore(), server = OfflineServer()..mediaApi = true;
      final c = controller(store, server);
      await login(c);
      server.offline = true;
      await c.refresh();
      await c.queueMedia('library', [
        (bytes: Uint8List.fromList([9, 8, 7]), name: 'photo.png'),
      ]);
      await c.refresh();
      final id = c.pendingOperations.single['id'];
      server.offline = false;
      server.loseResponse = true;
      await c.refresh();
      expect(c.pendingCount, 1);
      expect(server.writes, 1);
      expect(
        store.values.keys.any((k) => k.startsWith('media_pending_')),
        true,
      );
      await c.refresh();
      expect(c.pendingCount, 0);
      expect(server.writes, 1);
      expect(server.commands.map((r) => r['id']).toSet(), {id});
      expect(
        store.values.keys.any((k) => k.startsWith('media_pending_')),
        false,
      );
      c.dispose();
    },
  );
  test(
    'A failed durable queue commit removes staged bytes and never reports saved',
    () async {
      final store = FailingQueueStore(),
          server = OfflineServer()..mediaApi = true;
      final c = controller(store, server);
      await login(c);
      store.failWrites = true;
      await expectLater(
        c.queueMedia('library', [
          (bytes: Uint8List.fromList([1]), name: 'x.png'),
        ]),
        throwsStateError,
      );
      expect(c.pendingCount, 0);
      expect(server.writes, 0);
      expect(
        store.values.keys.any((k) => k.startsWith('media_pending_')),
        false,
      );
      c.dispose();
    },
  );
  test(
    'Empty library uploads and oversized files never enter the outbox',
    () async {
      final store = MemoryStore(), server = OfflineServer()..mediaApi = true;
      final c = controller(store, server);
      await login(c);
      await expectLater(c.queueMedia('library', []), throwsA(isA<ApiError>()));
      await expectLater(
        c.queueMedia('library', [
          (bytes: Uint8List(21 * 1024 * 1024), name: 'large.mp4'),
        ]),
        throwsA(isA<ApiError>()),
      );
      expect(c.pendingCount, 0);
      expect(server.writes, 0);
      c.dispose();
    },
  );
}
