import 'dart:ui' as ui;
import 'video_fixture.dart';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:liftcontrol_mobile/main.dart';
import 'package:liftcontrol_mobile/core/controller.dart';
import 'package:liftcontrol_mobile/core/diagnostics.dart';
import 'package:liftcontrol_mobile/core/storage.dart';
import 'package:liftcontrol_mobile/core/device_bridge.dart';

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
    'Native Android to Django: login, report, image, second employer and logout',
    (tester) async {
      final store = DeviceStore();
      await store.clear();
      var c = LiftController(store: store, log: Diagnostics(store));
      await c.initialize();
      await tester.pumpWidget(LiftApp(controller: c, initialize: false));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('server')),
        'http://127.0.0.1:18081',
      );
      await tester.enterText(find.byKey(const Key('username')), 'ci-mechanic');
      await tester.enterText(
        find.byKey(const Key('password')),
        'ci-test-password-only',
      );
      await tester.tap(find.byKey(const Key('login')));
      Future<void> waitUntil(bool Function() test) async {
        for (var i = 0; i < 100; i++) {
          await tester.pump(const Duration(milliseconds: 250));
          if (test()) return;
        }
        fail(
          'State did not become ready: refreshing=${c.refreshing}, '
          'role=${c.me['role']}, tickets=${c.list('tickets').map((t) => t['status']).toList()}, '
          'error=${c.error}\n${c.log.export()}',
        );
      }

      Future<void> tapReady(String label) async {
        final button = find.ancestor(
          of: find.text(label),
          matching: find.byWidgetPredicate(
            (widget) => widget is ButtonStyleButton,
          ),
        );
        await waitUntil(
          () =>
              button.evaluate().length == 1 &&
              tester.widget<ButtonStyleButton>(button).onPressed != null,
        );
        await tester.ensureVisible(button);
        await tester.tap(button);
      }

      Future<void> tapIconReady(String tooltip) async {
        final button = find.byWidgetPredicate(
          (widget) => widget is IconButton && widget.tooltip == tooltip,
        );
        await waitUntil(
          () =>
              button.evaluate().length == 1 &&
              tester.widget<IconButton>(button).onPressed != null,
        );
        await tester.ensureVisible(button);
        await tester.tap(button);
      }

      await waitUntil(() => c.online && c.snapshot.isNotEmpty);
      await tester.pumpAndSettle();
      expect(c.me['can_create_ticket'], true);
      expect(c.me['role'], 'MECHANIC');
      // A large encrypted value crosses Android's default CursorWindow limit.
      final largeValue = 'x' * (3 * 1024 * 1024);
      await store.write('cache_ci_chunk_check', largeValue);
      expect(await DeviceStore().read('cache_ci_chunk_check'), largeValue);
      await store.delete('cache_ci_chunk_check');
      await binding.convertFlutterSurfaceToImage();
      await tester.pumpAndSettle();
      await binding.takeScreenshot('01-home');
      await tester.tap(find.text('Заявки').last);
      await tester.pumpAndSettle();
      await binding.takeScreenshot('02-tickets');
      await tester.tap(find.text('Проверка дверей • CI'));
      await tester.pumpAndSettle();
      await tapReady('Принять заявку');
      await waitUntil(() => c.list('tickets').first['status'] == 'IN_PROGRESS');
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Лифт остановлен'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Лифт остановлен');
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byType(TextFormField),
        'Ожидаем запасную плату',
      );
      await tapReady('Сохранить остановку');
      // The ticket changes optimistically; the elevator is updated by the
      // following confirmed snapshot. Wait for that server acknowledgement.
      await waitUntil(
        () =>
            c.list('tickets').first['status'] == 'STOPPED' &&
            c.list('elevators').first['stopped_at'] != null &&
            c.pendingCount == 0 &&
            !c.refreshing,
      );
      await tester.pumpAndSettle();
      expect(c.list('elevators').first['stopped_at'], isNotNull);
      await binding.takeScreenshot('02b-lift-stopped');
      await tester.scrollUntilVisible(
        find.text('Продолжить ремонт'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Продолжить ремонт');
      await waitUntil(
        () =>
            c.list('tickets').first['status'] == 'IN_PROGRESS' &&
            c.pendingCount == 0 &&
            !c.refreshing,
      );
      await tester.pumpAndSettle();
      expect(c.list('elevators').first['stopped_at'], isNotNull);
      Future<void> gate(Map<String, bool> changes) async {
        final response = await http.post(
          Uri.parse('http://127.0.0.1:18083/'),
          body: jsonEncode(changes),
          headers: {'Content-Type': 'application/json'},
        );
        expect(response.statusCode, 200);
      }

      await gate({'offline': true});
      await c.refresh();
      expect(c.online, false);
      expect(c.canRecord, true);
      // Exercise real encrypted SQLite photo/comment/report storage while the API is unreachable.

      final recorder = ui.PictureRecorder();
      final canvas = Canvas(recorder);
      canvas.drawRect(
        const Rect.fromLTWH(0, 0, 16, 16),
        Paint()..color = const Color(0xFF237651),
      );
      final image = await recorder.endRecording().toImage(16, 16);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final photoBytes = bytes!.buffer.asUint8List();
      await c.uploadPhoto(
        c.list('tickets').first['id'] as int,
        photoBytes,
        'ci.png',
        c.organization!,
      );
      await c.queueMedia('chat', [
        (bytes: photoBytes, name: 'offline-ci.png'),
        (bytes: base64Decode(videoFixture), name: 'offline-ci.mp4'),
      ], body: 'Фото без связи CI');
      final mediaRow = c.pendingOperations.firstWhere(
        (r) => r['command']['type'] == 'media',
      );
      final mediaId = mediaRow['command']['body']['file_id'] as String;
      final mediaScope = c.scope;
      expect(await c.pendingMedia(mediaScope, mediaId), photoBytes);
      image.dispose();
      await tester.scrollUntilVisible(
        find.byTooltip('Добавить комментарий'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tapIconReady('Добавить комментарий');
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byType(TextFormField),
        'Проверены контакты дверей',
      );
      await tapReady('Отправить');
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Завершить с отчётом'),
        -300,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Завершить с отчётом');
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byType(TextFormField),
        'Восстановлены контакты. Работа проверена.',
      );
      await tapReady('Завершить');
      await waitUntil(() => c.list('tickets').first['status'] == 'DONE');
      await tester.pumpAndSettle();
      expect(c.pendingCount, 5);
      await binding.takeScreenshot('03a-offline-report-saved');
      final savedScope = c.scope;
      final queued = await c.outbox.rows(savedScope);
      expect(queued.where((r) => r['state'] != 'synced').length, 5);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      c = LiftController(store: DeviceStore(), log: Diagnostics(store));
      await c.initialize();
      expect(c.online, false);
      expect(c.pendingCount, 5);
      expect(c.list('tickets').first['status'], 'DONE');
      expect(await c.pendingMedia(mediaScope, mediaId), photoBytes);
      await tester.pumpWidget(LiftApp(controller: c, initialize: false));
      await tester.pumpAndSettle();
      await binding.takeScreenshot('03b-offline-after-restart');
      // Drop a response after the server commits one operation; replay must be harmless.
      await gate({
        'offline': false,
        'lose_next_response': true,
        'lose_next_media_response': true,
      });
      for (var attempt = 0; attempt < 5 && c.pendingCount > 0; attempt++) {
        await c.refresh();
      }
      expect(
        c.pendingCount,
        0,
        reason: 'Queue errors: ${c.pendingOperations.map((r) => r['error'])}',
      );
      expect(c.list('tickets').first['status'], 'DONE');
      expect((c.list('tickets').first['photos'] as List).length, 1);
      expect(
        c.list('chat').where((m) => m['body'] == 'Фото без связи CI').length,
        1,
      );
      final sent = c
          .list('chat')
          .singleWhere((m) => m['body'] == 'Фото без связи CI');
      final attachments = sent['attachments'] as List;
      expect(attachments.length, 2);
      final clip = attachments.singleWhere(
        (a) => a['content_type'] == 'video/mp4',
      );
      expect(
        await c.download(clip['url'] as String, c.organization!, video: true),
        base64Decode(videoFixture),
      );
      // The native file is only removed after the committed message is visible.
      await expectLater(
        c.mediaFiles.read(mediaScope, mediaId),
        throwsA(anything),
      );

      await tester.pumpAndSettle();
      await tester.tap(find.text('Заявки').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Завершённые'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Проверка дверей • CI'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Выполнена'),
        -300,
        scrollable: find.byType(Scrollable).first,
      );
      await binding.takeScreenshot('03-ticket-completed');
      expect(c.draft(c.list('tickets').first['id'] as int), '');
      expect(c.list('elevators').first['stopped_at'], isNull);
      expect(c.list('tickets').first['stop_reason'], 'Ожидаем запасную плату');
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      final original = c.organization;
      await tester.tap(find.byKey(const Key('organization-switch')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Организация Бета • CI'));
      await waitUntil(
        () =>
            c.organization != original &&
            !c.refreshing &&
            c.me['organization_id'] == c.organization &&
            find.text('Организация Бета • CI').evaluate().length == 1,
      );
      await tester.pumpAndSettle();
      expect(c.list('tickets'), isEmpty);
      expect(find.text('Организация Бета • CI'), findsOneWidget);
      await binding.takeScreenshot('04-second-employer');
      await tester.tap(find.byKey(const Key('organization-switch')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Организация Альфа • CI'));
      await waitUntil(
        () =>
            c.organization == original &&
            !c.refreshing &&
            c.me['organization_id'] == original &&
            find.text('Организация Альфа • CI').evaluate().length == 1,
      );
      await tester.pumpAndSettle();
      expect(c.list('tickets').first['status'], 'DONE');
      expect(find.text('Организация Альфа • CI'), findsOneWidget);
      await tester.tap(find.text('Объекты').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Тестовая улица'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Тестовая улица, 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('CI-001'));
      await tester.pumpAndSettle();
      await binding.takeScreenshot('05-elevator');
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Профиль и настройки'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(SwitchListTile, 'Тёмная тема'));
      await tester.pumpAndSettle();
      expect(c.dark, true);
      await binding.takeScreenshot('06-dark-settings');
      await c.log.flush();
      expect(c.log.export(), isNot(contains('ci-test-password-only')));
      expect(c.log.export(), isNot(contains('ci-mechanic')));
      await tester.scrollUntilVisible(
        find.text('Выйти из аккаунта'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Выйти из аккаунта');
      await tester.pumpAndSettle();
      await tapReady('Выйти');
      await waitUntil(() => !c.loggedIn);
      await tester.pumpAndSettle();
      expect(await store.read('session'), isNull);
      expect(find.byKey(const Key('login')), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.enterText(
        find.byKey(const Key('server')),
        'http://127.0.0.1:18081',
      );
      await tester.enterText(find.byKey(const Key('username')), 'ci-admin');
      await tester.enterText(
        find.byKey(const Key('password')),
        'ci-admin-password-only',
      );
      await tester.tap(find.byKey(const Key('login')));
      await waitUntil(
        () => c.online && c.me['role'] == 'ADMIN' && !c.refreshing,
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Ещё').last);
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Сотрудники'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Сотрудники'));
      await tester.pumpAndSettle();
      await tapReady('Добавить сотрудника');
      await tester.pumpAndSettle();
      final staffName = find.widgetWithText(TextFormField, 'ФИО сотрудника');
      await tester.ensureVisible(staffName);
      await tester.enterText(staffName, 'Новый сотрудник CI');
      final loginField = find.widgetWithText(TextFormField, 'Логин');
      await tester.ensureVisible(loginField);
      await tester.enterText(loginField, 'ci-created-mechanic');
      final passwordField = find.widgetWithText(
        TextFormField,
        'Пароль (от 12 символов)',
      );
      await tester.ensureVisible(passwordField);
      await tester.enterText(
        passwordField,
        'UniqueNativeCreatedPassword-2026!',
      );
      await tester.scrollUntilVisible(
        find.text('Создать сотрудника и доступ'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Создать сотрудника и доступ');
      await waitUntil(
        () =>
            c
                .list('technicians')
                .any((t) => t['full_name'] == 'Новый сотрудник CI') &&
            !c.refreshing &&
            find.text('Создать сотрудника и доступ').evaluate().isEmpty,
      );
      await tester.pumpAndSettle();
      final members = await c.request('organizations/members/');
      expect(
        (members as List)
            .where((m) => m['name'] == 'Новый сотрудник CI')
            .length,
        1,
      );
      await binding.takeScreenshot('07-admin-created-employee');
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Главная').last);
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Доп. задание'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Доп. задание');
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Название задания'),
        'Личное задание CI',
      );
      await tester.tap(
        find.widgetWithText(DropdownButtonFormField<int>, 'Исполнитель'),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Тестовый механик').last);
      await tester.pumpAndSettle();
      await binding.takeScreenshot('07b-personal-task-form');
      await tester.scrollUntilVisible(
        find.text('Назначить задание'),
        250,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Назначить задание');
      await waitUntil(
        () =>
            c.list('tasks').isNotEmpty &&
            !c.refreshing &&
            find.text('Личные задания').evaluate().length == 1,
      );
      await tester.pumpAndSettle();
      await binding.takeScreenshot('08-admin-personal-task');
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Чат бригады'));
      await tester.pumpAndSettle();
      await c.uploadMedia(
        'chat/',
        [(bytes: photoBytes, name: 'ci-chat.png')],
        c.organization!,
        fields: {'body': 'Фото в чате CI'},
      );
      await c.refresh();
      await waitUntil(() => c.pendingCount == 0 && !c.refreshing);
      await tester.pumpAndSettle();
      expect(find.text('Фото в чате CI'), findsOneWidget);
      await binding.takeScreenshot('09-chat-photo');
      final catalogue = await c.resource(
        'media/?source=chat',
        expectedOrganization: c.organization!,
      );
      expect(
        catalogue['count'],
        2,
      ); // Identical bytes shared by chat and report.
      await tapIconReady('Вложения чата');
      await waitUntil(
        () =>
            find.text('Ноябрь 2026').evaluate().isNotEmpty ||
            find.text('Исходное сообщение').evaluate().isNotEmpty,
      );
      await tester.pumpAndSettle();
      await binding.takeScreenshot('09b-media-library');
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();

      final device = await DeviceBridge.status();
      expect(device['categories']['entrapment']['importance'], 4);
      expect(device['categories']['entrapment']['distinct_alert_sound'], true);
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await c.logout();
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('server')),
        'http://127.0.0.1:18081',
      );
      await tester.enterText(find.byKey(const Key('username')), 'ci-mechanic');
      await tester.enterText(
        find.byKey(const Key('password')),
        'ci-test-password-only',
      );
      await tester.tap(find.byKey(const Key('login')));
      await waitUntil(
        () => c.me['role'] == 'MECHANIC' && c.online && !c.refreshing,
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Личные задания · 1'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tapReady('Личные задания · 1');
      await tester.pumpAndSettle();
      await tester.tap(find.textContaining('Личное задание CI').first);
      await tester.pumpAndSettle();
      await tapReady('Принять задание');
      await waitUntil(() => c.list('tasks').first['status'] == 'IN_PROGRESS');
      await tester.pumpAndSettle();
      await tapReady('Завершить с отчётом');
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byType(TextFormField),
        'Личное задание выполнено CI',
      );
      await tapReady('Завершить');
      await waitUntil(
        () =>
            c.list('tasks').first['status'] == 'DONE' &&
            find.text('Личное задание выполнено CI').evaluate().isNotEmpty,
      );
      await tester.pumpAndSettle();
      await binding.takeScreenshot('10-personal-task-completed');
      expect(c.taskDraft(c.list('tasks').first['id'] as int), '');
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      await c.logout();
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
      c.dispose();
    },
  );
}
