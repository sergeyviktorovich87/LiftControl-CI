import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:liftcontrol_mobile/core/api.dart';
import 'package:liftcontrol_mobile/core/controller.dart';
import 'package:liftcontrol_mobile/core/demo.dart';
import 'package:liftcontrol_mobile/core/device_bridge.dart';
import 'package:liftcontrol_mobile/core/diagnostics.dart';
import 'package:liftcontrol_mobile/core/storage.dart';
import 'package:liftcontrol_mobile/ui/shell.dart';
import 'package:liftcontrol_mobile/ui/extras.dart';

// Keep widget tests independent of HTTP stream timers. ApiClient's real HTTP
// behavior is covered separately in client_test.dart; these exercise the
// controller's version logic, native handoff and foreground UI.
class UpdateFixtureController extends LiftController {
  UpdateFixtureController({
    required super.store,
    required super.log,
    required this.respond,
  });
  final Object Function(String path, String method) respond;
  @override
  Future<dynamic> request(
    String path, {
    String method = 'GET',
    Json? data,
    int? expectedOrganization,
  }) async => respond(path, method);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  int checks = 0, registrations = 0, permissionPrompts = 0;
  int? version;
  bool configured = false;
  Map<dynamic, dynamic>? install;
  late LiftController c;
  setUp(() {
    checks = 0;
    registrations = 0;
    permissionPrompts = 0;
    version = 2031;
    configured = false;
    install = null;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(DeviceBridge.channel, (call) async {
          if (call.method == 'status') {
            return {'version': '0.12.0', 'version_code': 2030, 'notifications_allowed': true};
          }
          if (call.method == 'installUpdate') {
            install = Map<dynamic, dynamic>.from(call.arguments as Map);
          }
          if (call.method == 'registerPush') {
            if ((call.arguments as Map)['prompt'] == true) {
              permissionPrompts++;
            }
            return 'test-fcm-token';
          }
          return null;
        });
    final store = MemoryStore();
    c = UpdateFixtureController(
      store: store,
      log: Diagnostics(store),
      respond: (path, method) {
        Object result = {};
        if (path == 'updates/') {
          checks++;
          result = {
            'android': version == null
                ? null
                : {
                    'version_code': version,
                    'version': '0.5.0',
                    'size': 100,
                    'sha256': 'a' * 64,
                  },
            'installed': {'panel': '0.10'},
          };
        }
        if (path == 'mobile/push/') {
          if (method == 'POST') {
            registrations++;
            result = {'ok': true};
          } else {
            result = {
              'enabled': configured,
              'config': configured
                  ? {
                      'apiKey': 'key',
                      'appId': 'app',
                      'projectId': 'project',
                      'messagingSenderId': 'sender',
                    }
                  : null,
            };
          }
        }
        return result;
      },
    );
    c.session = {
      'server': 'https://example.com',
      'token': 'a' * 43,
      'user': {'id': 7},
    };
    c.organization = 1;
    c.organizations = [
      {'id': 1, 'name': 'Компания', 'role': 'MECHANIC'},
    ];
    c.snapshot = demoSnapshot(1);
    c.online = true;
    c.starting = false;
  });
  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(DeviceBridge.channel, null);
    c.dispose();
  });

  testWidgets(
    'Opening prompts once; Later suppresses duplicates until the next foreground',
    (tester) async {
      await tester.pumpWidget(MaterialApp(home: LiftShell(controller: c)));
      await c.checkUpdates();
      await tester.pumpAndSettle();
      expect(c.hasUpdate, isTrue, reason: c.log.export());
      expect(find.text('Доступно обновление'), findsOneWidget);
      await tester.tap(find.text('Позже'));
      await tester.pumpAndSettle();
      await c.checkUpdates(force: true);
      await tester.pumpAndSettle();
      expect(find.text('Доступно обновление'), findsNothing);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await c.checkUpdates();
      await tester.pumpAndSettle();
      expect(find.text('Доступно обновление'), findsOneWidget);
      expect(checks, greaterThanOrEqualTo(3));
      await tester.pumpWidget(const SizedBox());
    },
  );

  for (final current in [2030, 2029, null]) {
    testWidgets('No update dialog for server version $current', (tester) async {
      version = current;
      await tester.pumpWidget(MaterialApp(home: LiftShell(controller: c)));
      await tester.pumpAndSettle();
      expect(find.text('Доступно обновление'), findsNothing);
      expect(checks, 1);
      await tester.pumpWidget(const SizedBox());
    });
  }

  testWidgets(
    'Update opens the native installer with the checked build and digest',
    (tester) async {
      await tester.pumpWidget(MaterialApp(home: LiftShell(controller: c)));
      await c.checkUpdates();
      await tester.pumpAndSettle();
      await tester.tap(find.text('Обновить').last);
      await tester.pumpAndSettle();
      expect(install?['version_code'], 2031);
      expect(install?['sha256'], 'a' * 64);
      expect(install?['server'], 'https://example.com');
      expect(install?['organization'], 1);
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets('More menu shows the installed Android version and build', (tester) async {
    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: MorePage(controller: c))),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.byKey(const Key('software-version')),
      220,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    expect(find.text('Версия ПО'), findsOneWidget);
    expect(find.text('Android 0.12.0 · сборка 2030'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  test('Diagnostics use the installed APK version supplied by Android', () {
    expect(c.log.export(device: {'version': '0.12.0'}), contains('"version": "0.12.0"'));
  });

  test('Forced foreground checks bypass throttling', () async {
    await c.checkUpdates();
    await c.checkUpdates();
    expect(checks, 1);
    await c.checkUpdates(force: true);
    expect(checks, 2);
  });

  test(
    'Push registers without a manual opt-in and retries after server setup',
    () async {
      await c.syncDevice();
      expect(registrations, 0);
      configured = true;
      await c.syncDevice();
      expect(registrations, 1);
      expect(permissionPrompts, 1);
      await c.syncDevice();
      expect(registrations, 2);
      expect(permissionPrompts, 1);
    },
  );
}
