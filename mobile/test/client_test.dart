import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:liftcontrol_mobile/core/api.dart';
import 'package:liftcontrol_mobile/core/controller.dart';
import 'package:liftcontrol_mobile/core/demo.dart';
import 'package:liftcontrol_mobile/core/diagnostics.dart';
import 'package:liftcontrol_mobile/core/storage.dart';

const token = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const organizations = [
  {'id': 1, 'name': 'Организация Альфа', 'role': 'MECHANIC'},
  {'id': 2, 'name': 'Организация Бета', 'role': 'DISPATCHER'},
];

class Server {
  bool fieldOps = true;
  bool offline = false, forbidden = false;
  Completer<void>? delayed;
  List<Map<String, dynamic>>? technicians;
  final requests = <http.Request>[];
  late final client = MockClient((r) async {
    requests.add(r);
    if (offline) throw http.ClientException('Offline');
    final route = r.url.path.replaceFirst('/api/', '');
    final org = int.tryParse(r.headers['X-Organization-ID'] ?? '1') ?? 1;
    if (route == 'mobile/login/') {
      return _json({
        'token': token,
        'expires_at': DateTime.now()
            .toUtc()
            .add(const Duration(days: 30))
            .toIso8601String(),
        'user': {'id': 7, 'name': 'Private account name'},
        'organizations': organizations,
      });
    }
    if (route == 'mobile/logout/') return _json({'ok': true});
    if (route == 'organizations/') {
      return _json({
        'organizations': forbidden ? [organizations[1]] : organizations,
      });
    }
    if (forbidden && org == 1) return _json({'detail': 'No access'}, 403);
    final data = demoSnapshot(org);
    if (!fieldOps) (data['me'] as Map).remove('field_ops_api');
    (data['me'] as Map)['organizations'] = organizations;
    if (route == 'tickets/' && org == 1 && delayed != null) {
      await delayed!.future;
    }
    if (route == 'technicians/' && technicians != null) {
      return _json(technicians);
    }
    return _json(data[route.replaceAll('/', '')] ?? []);
  });
  http.Response _json(dynamic value, [int status = 200]) => http.Response(
    jsonEncode(value),
    status,
    headers: {'content-type': 'application/json; charset=utf-8'},
  );
}

class DelayedStore extends MemoryStore {
  String? blockedKey;
  final started = Completer<void>();
  final release = Completer<void>();

  @override
  Future<String?> read(String key) async {
    final saved = await super.read(key);
    if (key == blockedKey && !started.isCompleted) {
      started.complete();
      await release.future;
    }
    return saved;
  }
}

void main() {
  test('Only HTTPS and loopback tunnels accept credentials', () {
    expect(parseServer('https://example.ru/').host, 'example.ru');
    expect(parseServer('http://127.0.0.1:18081').port, 18081);
    for (final value in [
      'http://example.ru',
      'http://example.ru:18080',
      'https://user:pass@example.ru',
      'https://example.ru/path',
      'https://example.ru/?token=secret',
      'javascript:alert(1)',
      'https://example.ru/#secret',
    ]) {
      expect(() => parseServer(value), throwsA(isA<ApiError>()), reason: value);
    }
  });
  test(
    'Tokens are scoped to the configured origin and organization; redirects are rejected',
    () async {
      final log = Diagnostics(MemoryStore());
      final client = ApiClient(
        server: parseServer('https://example.ru'),
        token: token,
        organization: 1,
        log: log,
        transport: MockClient((r) async {
          expect(r.headers['Authorization'], 'Bearer $token');
          expect(r.headers['X-Organization-ID'], '1');
          expect(r.followRedirects, false);
          return http.Response(
            '',
            302,
            headers: {'location': 'https://other.ru'},
          );
        }),
      );
      await expectLater(client.call('me/'), throwsA(isA<ApiError>()));
      for (final value in [
        'https://evil.ru/api/documents/1/download/',
        '/api/documents/1/download/?organization=2',
        '/private-media/x',
        '/api/tickets/1/../../secret/',
      ]) {
        expect(() => client.resourceUri(value), throwsA(isA<ApiError>()));
      }
      expect(
        client.resourceUri('/api/documents/1/download/?organization=1').host,
        'example.ru',
      );
      expect(
        client.resourceUri('/api/chat/1/attachments/1/?organization=1').host,
        'example.ru',
      );
      expect(
        () => client.resourceUri('/api/tasks/1/attachments/1/?organization=2'),
        throwsA(isA<ApiError>()),
      );
      await log.flush();
      expect(log.export(), isNot(contains(token)));
    },
  );
  test(
    'No secrets, business bodies or logins in persistent diagnostics',
    () async {
      final store = MemoryStore(),
          log = Diagnostics(MemoryStore()),
          server = Server();
      final c = LiftController(
        store: store,
        log: log,
        transport: server.client,
      );
      await c.initialize();
      await c.login('https://example.ru', 'secret-login', 'secret-password');
      await log.flush();
      final report = log.export();
      for (final value in [
        token,
        'secret-login',
        'secret-password',
        'Private account name',
        'Учебное',
        'Не закрываются',
      ]) {
        expect(report, isNot(contains(value)));
      }
      expect(report, contains('login_success'));
      c.dispose();
    },
  );
  test(
    'Offline restart restores scoped data and durably records a transition',
    () async {
      final store = MemoryStore(),
          server = Server(),
          log = Diagnostics(MemoryStore());
      final first = LiftController(
        store: store,
        log: log,
        transport: server.client,
      );
      await first.initialize();
      await first.login('https://example.ru', 'worker', 'password');
      first.setDraft(103, 'My private work report', 1);
      await first.flushDrafts();
      first.dispose();
      server.offline = true;
      final restored = LiftController(
        store: store,
        log: log,
        transport: server.client,
      );
      await restored.initialize();
      expect(restored.list('tickets'), isNotEmpty);
      expect(restored.draft(103), 'My private work report');
      expect(restored.canWrite, false);
      final before = server.requests.length;
      final saved = await restored.request(
        'tickets/103/complete/',
        method: 'POST',
        data: {'work_description': 'Work saved offline'},
      );
      expect(saved['status'], 'DONE');
      expect(restored.pendingCount, 1);
      expect(server.requests.length, before);
      restored.dispose();
    },
  );
  test(
    'Organizations keep different caches and reports; switching never reuses the other snapshot',
    () async {
      final store = MemoryStore(), server = Server();
      final c = LiftController(
        store: store,
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      c.setDraft(103, 'First employer report', 1);
      await c.flushDrafts();
      await c.selectOrganization(2);
      expect(c.list('tickets'), isEmpty);
      expect(c.draft(103), '');
      c.setDraft(103, 'Second employer report', 2);
      await c.flushDrafts();
      await c.selectOrganization(1);
      expect(c.draft(103), 'First employer report');
      expect(c.list('tickets'), isNotEmpty);
      expect(
        storageScope(parseServer('https://other.ru'), 7, 1),
        isNot(c.scope),
      );
      c.dispose();
    },
  );
  test(
    'A refresh after a write waits for a fresh pass when an older read is running',
    () async {
      final server = Server();
      final c = LiftController(
        store: MemoryStore(),
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      final gate = Completer<void>();
      server.delayed = gate;
      final oldRead = c.refresh();
      await Future<void>.delayed(Duration.zero);
      server.technicians = [
        {'id': 88, 'full_name': 'Newly created employee', 'active': true},
      ];
      final afterWrite = c.refresh();
      server.delayed = null;
      gate.complete();
      await Future.wait([oldRead, afterWrite]);
      expect(c.list('technicians').single['id'], 88);
      expect(c.refreshing, false);
      c.dispose();
    },
  );
  test(
    'Late responses from a previous organization cannot overwrite the active page',
    () async {
      final server = Server(),
          c = LiftController(
            store: MemoryStore(),
            log: Diagnostics(MemoryStore()),
            transport: null,
          );
      // Use a separate controller to keep the injectable transport immutable.
      c.dispose();
      final active = LiftController(
        store: MemoryStore(),
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      await active.initialize();
      await active.login('https://example.ru', 'worker', 'password');
      server.delayed = Completer<void>();
      final old = active.refresh();
      await Future<void>.delayed(Duration.zero);
      await active.selectOrganization(2);
      server.delayed!.complete();
      await old;
      expect(active.organization, 2);
      expect(active.me['organization'], 'Организация Бета');
      expect(active.list('tickets'), isEmpty);
      active.dispose();
    },
  );
  test(
    'Company switching stays busy through secure storage and preserves the target draft',
    () async {
      final server = Server(), store = DelayedStore();
      final c = LiftController(
        store: store,
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      addTearDown(() {
        if (!store.release.isCompleted) store.release.complete();
        c.dispose();
      });
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      await c.selectOrganization(2);
      c.setDraft(103, 'Second employer report', 2);
      await c.flushDrafts();
      final secondScope = c.scope;
      await c.selectOrganization(1);
      store.blockedKey = 'cache_$secondScope';
      final switching = c.selectOrganization(2);
      await store.started.future;
      expect(c.organization, 2);
      expect(c.refreshing, true);
      expect(c.online, false);
      expect(c.snapshot, isEmpty);
      final requestCount = server.requests.length;
      await c.refresh();
      // Safe housekeeping may finish while the organization cache is blocked.
      // No new scoped business data may be fetched before restore completes.
      final unexpected = server.requests.skip(requestCount).map(
        (request) => request.url.path.replaceFirst('/api/', ''),
      ).where((route) => !{'updates/', 'mobile/push/'}.contains(route)).toList();
      expect(
        unexpected, isEmpty,
        reason: 'Organization data must not be fetched before scoped restore',
      );
      // An app pause during loading must not replace an unread draft with {}.
      await c.flushDrafts();
      expect(
        store.values['drafts_$secondScope'],
        contains('Second employer report'),
      );
      store.release.complete();
      await switching;
      expect(c.refreshing, false);
      expect(c.online, true);
      expect(c.me['organization_id'], 2);
      expect(c.draft(103), 'Second employer report');
    },
  );
  test(
    'A delayed company cache cannot replace the last selection or erase its draft',
    () async {
      final server = Server(), store = DelayedStore();
      final c = LiftController(
        store: store,
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      addTearDown(() {
        if (!store.release.isCompleted) store.release.complete();
        c.dispose();
      });
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      final firstScope = c.scope;
      await c.selectOrganization(2);
      c.setDraft(103, 'Keep second employer report', 2);
      await c.flushDrafts();
      store.blockedKey = 'cache_$firstScope';
      final olderSwitch = c.selectOrganization(1);
      await store.started.future;
      await c.selectOrganization(2);
      expect(c.draft(103), 'Keep second employer report');
      store.release.complete();
      await olderSwitch;
      expect(c.organization, 2);
      expect(c.me['organization_id'], 2);
      expect(c.refreshing, false);
      expect(c.list('tickets'), isEmpty);
      expect(c.draft(103), 'Keep second employer report');
      expect(jsonDecode((await store.read('session'))!)['organization'], 2);
    },
  );
  test(
    'Logout prevents a pending cache read from restoring account data',
    () async {
      final server = Server(), store = DelayedStore();
      final c = LiftController(
        store: store,
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      addTearDown(() {
        if (!store.release.isCompleted) store.release.complete();
        c.dispose();
      });
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      final firstScope = c.scope;
      await c.selectOrganization(2);
      store.blockedKey = 'cache_$firstScope';
      final switching = c.selectOrganization(1);
      await store.started.future;
      expect(await c.logout(), true);
      store.release.complete();
      await switching;
      expect(c.loggedIn, false);
      expect(c.organization, isNull);
      expect(c.snapshot, isEmpty);
      expect(c.canWrite, false);
      expect(await store.read('session'), isNull);
      expect(
        store.values.keys.where(
          (key) =>
              key.startsWith('cache_') ||
              key.startsWith('drafts_') ||
              key.startsWith('resources_'),
        ),
        isEmpty,
      );
    },
  );
  test(
    'An older server keeps its supported workflows without querying personal tasks',
    () async {
      final server = Server()..fieldOps = false;
      final c = LiftController(
        store: MemoryStore(),
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      expect(c.canWrite, true);
      expect(c.list('tickets'), isNotEmpty);
      expect(
        server.requests.where((r) => r.url.path == '/api/tasks/'),
        isEmpty,
      );
      c.dispose();
    },
  );
  test(
    'Personal task and shared ticket reports keep separate drafts across employers',
    () async {
      final server = Server(), store = MemoryStore();
      final c = LiftController(
        store: store,
        log: Diagnostics(MemoryStore()),
        transport: server.client,
      );
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      c.setDraft(103, 'Shared ticket report', 1);
      c.setTaskDraft(103, 'Private assignment report', 1);
      await c.flushDrafts();
      await c.selectOrganization(2);
      expect(c.taskDraft(103), '');
      await c.selectOrganization(1);
      expect(c.draft(103), 'Shared ticket report');
      expect(c.taskDraft(103), 'Private assignment report');
      c.dispose();
    },
  );
  test('Revoked organization clears its cached data immediately', () async {
    final server = Server(), store = MemoryStore();
    final c = LiftController(
      store: store,
      log: Diagnostics(MemoryStore()),
      transport: server.client,
    );
    await c.initialize();
    await c.login('https://example.ru', 'worker', 'password');
    final key = c.scope;
    c.setDraft(103, 'sensitive report', 1);
    await c.flushDrafts();
    server.forbidden = true;
    await c.refresh();
    expect(c.organization, isNull);
    expect(c.snapshot, isEmpty);
    expect(await store.read('cache_$key'), isNull);
    expect(await store.read('drafts_$key'), isNull);
    c.dispose();
  });
  test(
    'Logout clears every employer cache and all credentials even without a connection',
    () async {
      final server = Server(), store = MemoryStore();
      final c = LiftController(
        store: store,
        log: Diagnostics(store),
        transport: server.client,
      );
      await c.initialize();
      await c.login('https://example.ru', 'worker', 'password');
      c.setDraft(103, 'private report', 1);
      await c.flushDrafts();
      await c.selectOrganization(2);
      server.offline = true;
      expect(await c.logout(), false);
      expect(c.loggedIn, false);
      expect(await store.read('session'), isNull);
      expect(
        store.values.keys.where(
          (key) =>
              key.startsWith('cache_') ||
              key.startsWith('drafts_') ||
              key.startsWith('resources_'),
        ),
        isEmpty,
      );
      expect(store.values.values.join(), isNot(contains(token)));
      c.dispose();
    },
  );
}
