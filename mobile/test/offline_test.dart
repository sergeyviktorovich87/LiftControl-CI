import 'dart:convert';
import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:liftcontrol_mobile/main.dart';
import 'package:liftcontrol_mobile/ui/tickets.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:liftcontrol_mobile/core/api.dart';
import 'package:liftcontrol_mobile/core/controller.dart';
import 'package:liftcontrol_mobile/core/demo.dart';
import 'package:liftcontrol_mobile/core/diagnostics.dart';
import 'package:liftcontrol_mobile/core/storage.dart';

class OfflineServer {
  bool offline = false, loseResponse = false, mediaApi = false;
  final mediaMessages = <int, List<Json>>{1: [], 2: []};
  int? reject;
  int writes = 0, nextId = 200;
  Completer<void>? syncGate;
  final syncStarted = Completer<void>();
  final receipts = <String, Json>{};
  final commands = <Json>[];
  final tickets = <int, List<Json>>{
    1: records(demoSnapshot(1)['tickets']),
    2: [],
  };
  final organizations = [
    {'id': 1, 'name': 'Организация Альфа', 'role': 'MECHANIC'},
    {'id': 2, 'name': 'Организация Бета', 'role': 'DISPATCHER'},
  ];
  late final client = MockClient((r) async {
    if (offline) throw http.ClientException('Offline');
    final route = r.url.path.replaceFirst('/api/', '');
    final org = int.tryParse(r.headers['X-Organization-ID'] ?? '1') ?? 1;
    dynamic data;
    if (route == 'mobile/login/') {
      data = {
        'token': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'user': {'id': 7, 'name': 'Mechanic'},
        'organizations': organizations,
        'expires_at': DateTime.now()
            .toUtc()
            .add(const Duration(days: 30))
            .toIso8601String(),
      };
    } else if (route == 'mobile/sync/') {
      if (!syncStarted.isCompleted) syncStarted.complete();
      if (syncGate != null) await syncGate!.future;
      final command = Map<String, dynamic>.from(jsonDecode(r.body));
      commands.add(command);
      if (reject != null) {
        return json({'detail': 'Назначение изменилось'}, reject!);
      }
      final id = command['id'] as String;
      data = receipts[id];
      if (data == null) {
        writes++;
        final body = Map<String, dynamic>.from(command['body']);
        Json ticket;
        if (command['type'] == 'create') {
          ticket = {
            'id': ++nextId,
            ...body,
            'address': 'Address',
            'elevator_label': 'Lift 1',
            'created_at': command['occurred_at'],
            'status': 'NEW',
            'assigned_to': 1,
            'can_accept': true,
          };
          tickets[org]!.add(ticket);
        } else {
          ticket = tickets[org]!.firstWhere(
            (t) => t['id'] == command['ticket'],
          );
          if (command['type'] == 'complete') {
            ticket['status'] = 'DONE';
            ticket['repair'] = body['work_description'];
          }
          if (command['type'] == 'accept') {
            ticket['status'] = 'IN_PROGRESS';
            ticket['can_complete'] = true;
          }
        }
        data = {
          'operation_id': id,
          'ticket': {...ticket},
          'result': <String, dynamic>{},
        };
        receipts[id] = Map<String, dynamic>.from(data);
      }
      if (loseResponse) {
        loseResponse = false;
        throw http.ClientException('Response lost after commit');
      }
    } else if (route == 'media/uploads/') {
      final multipart = utf8.decode(r.bodyBytes, allowMalformed: true);
      String field(String key) => RegExp(
        'name="$key"(?:\r\n[^\r\n]+)*\r\n\r\n([\\s\\S]*?)\r\n--',
      ).firstMatch(multipart)!.group(1)!;
      final id = field('upload_id');
      commands.add({
        'id': id,
        'body': {'kind': field('kind')},
      });
      data = receipts[id];
      if (data == null) {
        writes++;
        final message = field('kind') == 'chat' ? ++nextId : null;
        data = {
          'operation_id': id,
          'state': 'READY',
          'message': message,
          'asset': {
            'id': id,
            'size': 3,
            'name': field('name'),
            'url': '/api/media/$id/download/',
            'content_type': 'image/png',
          },
        };
        receipts[id] = Map<String, dynamic>.from(data);
        if (message != null) {
          mediaMessages[org]!.add({
            'id': message,
            'body': field('body'),
            'attachments': [data['asset']],
          });
        }
      }
      if (loseResponse) {
        loseResponse = false;
        throw http.ClientException('Response lost after commit');
      }
    } else if (route == 'chat/' && mediaApi) {
      data = mediaMessages[org];
    } else if (route == 'mobile/logout/') {
      data = {'ok': true};
    } else if (route == 'tickets/') {
      data = tickets[org];
    } else {
      final snapshot = demoSnapshot(org);
      snapshot['me']['organizations'] = organizations;
      if (mediaApi) {
        snapshot['me']['media_api'] = 1;
      }
      data = snapshot[route.replaceAll('/', '')] ?? [];
    }
    return json(data);
  });
  http.Response json(dynamic data, [int status = 200]) => http.Response(
    jsonEncode(data),
    status,
    headers: {'content-type': 'application/json; charset=utf-8'},
  );
}

class FailingQueueStore extends MemoryStore {
  bool failWrites = false;
  @override
  Future<void> write(String key, String value) async {
    if (failWrites && key.startsWith('outbox_')) throw StateError('Disk full');
    await super.write(key, value);
  }
}

LiftController controller(MemoryStore store, OfflineServer server) =>
    LiftController(
      store: store,
      log: Diagnostics(MemoryStore()),
      transport: server.client,
    );
Future<void> login(LiftController c) async {
  await c.initialize();
  await c.login('https://example.ru', 'mechanic', 'password');
}

void main() {
  test(
    'Offline create is durable, visible immediately, survives restart and sends once',
    () async {
      final store = MemoryStore(), server = OfflineServer();
      var c = controller(store, server);
      await login(c);
      server.offline = true;
      await c.refresh();
      expect(c.canWrite, false);
      expect(c.canCreateTicket, true);
      final local = await c.request(
        'tickets/',
        method: 'POST',
        data: {'elevator': 1, 'title': 'Offline fault', 'priority': 'NORMAL'},
        expectedOrganization: 1,
      );
      expect(local['id'], lessThan(0));
      expect(c.pendingCount, 1);
      expect(c.list('tickets').any((t) => t['title'] == 'Offline fault'), true);
      c.dispose();
      c = controller(store, server);
      await c.initialize();
      expect(c.pendingCount, 1);
      expect(c.list('tickets').any((t) => t['title'] == 'Offline fault'), true);
      server.offline = false;
      await c.refresh();
      await c.refresh();
      expect(c.pendingCount, 0);
      expect(server.writes, 1);
      expect(
        c
            .list('tickets')
            .where((t) => t['title'] == 'Offline fault')
            .single['id'],
        greaterThan(0),
      );
      c.dispose();
    },
  );
  test(
    'Saving returns from the local database while an apparently online server hangs',
    () async {
      final store = MemoryStore(), server = OfflineServer();
      final c = controller(store, server);
      await login(c);
      server.syncGate = Completer<void>();
      final local = await c
          .request(
            'tickets/',
            method: 'POST',
            data: {'elevator': 1, 'title': 'Server cannot answer'},
          )
          .timeout(const Duration(seconds: 1));
      await server.syncStarted.future.timeout(const Duration(seconds: 1));
      expect(local['id'], lessThan(0));
      expect(c.pendingCount, 1);
      expect(server.writes, 0);
      expect(
        c.list('tickets').any((t) => t['title'] == 'Server cannot answer'),
        true,
      );
      server.syncGate!.complete();
      await c.refresh();
      expect(c.pendingCount, 0);
      expect(server.writes, 1);
      c.dispose();
    },
  );
  test(
    'Lost HTTP response retains same operation ID and never duplicates a server record',
    () async {
      final store = MemoryStore(), server = OfflineServer();
      final c = controller(store, server);
      await login(c);
      server.offline = true;
      await c.refresh();
      server.offline = false;
      server.loseResponse = true;
      await c.request(
        'tickets/',
        method: 'POST',
        data: {'elevator': 1, 'title': 'Ambiguous response'},
      );
      await c.syncPending();
      expect(c.pendingCount, 1);
      expect(server.writes, 1);
      final id = c.pendingOperations.single['id'];
      await c.refresh();
      expect(c.pendingCount, 0);
      expect(server.writes, 1);
      expect(server.commands.map((r) => r['id']).toSet(), {id});
      c.dispose();
    },
  );
  test(
    'Offline completion and photo are queued in order and retained after rejection',
    () async {
      final store = MemoryStore(), server = OfflineServer();
      final c = controller(store, server);
      await login(c);
      server.offline = true;
      await c.refresh();
      await c.uploadPhoto(103, base64Decode('aGVsbG8='), 'photo.jpg', 1);
      await c.request(
        'tickets/103/complete/',
        method: 'POST',
        data: {'work_description': 'Work offline'},
      );
      expect(c.pendingOperations.map((r) => r['command']['type']), [
        'photo',
        'complete',
      ]);
      expect(c.localTicket(103)['status'], 'DONE');
      expect(c.localTicket(103)['can_cancel'], false);
      server.offline = false;
      server.reject = 403;
      await c.refresh();
      expect(c.failedCount, 1);
      expect(c.pendingCount, 2);
      expect(server.writes, 0);
      expect(c.pendingOperations.first['error'], isNotEmpty);
      c.dispose();
    },
  );
  test(
    'A revoked or expired session never erases unsent data and a new login restores it',
    () async {
      final store = MemoryStore(), server = OfflineServer();
      var c = controller(store, server);
      await login(c);
      server.offline = true;
      await c.refresh();
      await c.request(
        'tickets/',
        method: 'POST',
        data: {'elevator': 1, 'title': 'Preserve through logout'},
      );
      await c.logout();
      expect(c.loggedIn, false);
      expect(store.values.keys.any((k) => k.startsWith('outbox_')), true);
      c.dispose();
      server.offline = false;
      c = controller(store, server);
      await login(c);
      expect(server.writes, 1);
      expect(c.pendingCount, 0);
      c.dispose();
    },
  );
  test(
    'Switching organizations does not submit the previous organization queue',
    () async {
      final store = MemoryStore(), server = OfflineServer();
      final c = controller(store, server);
      await login(c);
      server.offline = true;
      await c.refresh();
      await c.request(
        'tickets/',
        method: 'POST',
        data: {'elevator': 1, 'title': 'Only first employer'},
      );
      await c.selectOrganization(2);
      expect(c.pendingCount, 0);
      expect(c.list('tickets'), isEmpty);
      server.offline = false;
      await c.refresh();
      expect(server.writes, 0);
      await c.selectOrganization(1);
      expect(server.writes, 1);
      expect(c.pendingCount, 0);
      c.dispose();
    },
  );
  test(
    'Storage failure never reports an operation as saved or sends it',
    () async {
      final store = FailingQueueStore(), server = OfflineServer();
      final c = controller(store, server);
      await login(c);
      store.failWrites = true;
      await expectLater(
        c.request(
          'tickets/',
          method: 'POST',
          data: {'elevator': 1, 'title': 'Disk failure'},
        ),
        throwsStateError,
      );
      expect(c.pendingCount, 0);
      expect(server.writes, 0);
      c.dispose();
    },
  );
  testWidgets(
    'Offline UI creates both lifts with visible assignees and opens the durable queue',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final store = MemoryStore(), server = OfflineServer();
      late LiftController c;
      await tester.runAsync(() async {
        c = controller(store, server);
        await login(c);
        server.offline = true;
        await c.refresh();
      });
      await tester.pumpWidget(LiftApp(controller: c, initialize: false));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Создать заявку').hitTestable(),
        150,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Создать заявку'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Выбрать адрес и лифт'));
      await tester.pumpAndSettle();
      await tester.tap(find.textContaining('ПРИМЕР-001').last);
      await tester.pumpAndSettle();
      expect(find.text('Ответственный механик'), findsOneWidget);
      await tester.tap(find.text('Также лифт ПРИМЕР-002'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('ticket_title')),
        'Оба лифта не работают',
      );
      await tester.scrollUntilVisible(
        find.text('Создать заявку').hitTestable(),
        150,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Создать заявку'));
      await tester.pumpAndSettle();
      for (var attempt = 0; attempt < 20 && c.pendingCount < 2; attempt++) {
        await tester.pump(const Duration(milliseconds: 100));
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)),
        );
      }
      await tester.pumpAndSettle();
      expect(
        c.pendingCount,
        2,
        reason:
            'canCreate=${c.canCreateTicket} error=${c.error} log=${c.log.export()} text=${tester.widgetList<Text>(find.byType(Text)).map((t) => t.data).toList()}',
      );
      expect(
        c.pendingOperations
            .map((r) => r['command']['body']['elevator'])
            .toSet(),
        {1, 2},
      );
      expect(
        c.pendingOperations
            .map((r) => r['command']['body']['assigned_to'])
            .toSet(),
        {1, 2},
      );
      await tester.tap(find.text('Очередь'));
      await tester.pumpAndSettle();
      expect(find.text('Очередь отправки'), findsOneWidget);
      expect(find.text('Оба лифта не работают'), findsNWidgets(2));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
    },
  );
  testWidgets(
    'An offline comment is immediately visible in the ticket history',
    (tester) async {
      final store = MemoryStore(), server = OfflineServer();
      late LiftController c;
      await tester.runAsync(() async {
        c = controller(store, server);
        await login(c);
        server.offline = true;
        await c.refresh();
        await c.request(
          'tickets/103/comment/',
          method: 'POST',
          data: {'body': 'Комментарий без интернета'},
        );
      });
      await tester.pumpWidget(
        MaterialApp(home: TicketPage(controller: c, id: 103, organization: 1)),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Комментарий без интернета'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Комментарий без интернета'), findsOneWidget);
      expect(c.pendingCount, 1);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
    },
  );
}
