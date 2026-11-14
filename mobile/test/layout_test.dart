import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:liftcontrol_mobile/main.dart';
import 'package:liftcontrol_mobile/core/controller.dart';
import 'package:liftcontrol_mobile/core/diagnostics.dart';
import 'package:liftcontrol_mobile/core/storage.dart';

void main() {
  for (final spec in [
    (360.0, 800.0, 1.0, false),
    (390.0, 844.0, 1.3, true),
    (960.0, 800.0, 1.0, false),
  ]) {
    testWidgets('Demo navigation fits ${spec.$1}px and text scale ${spec.$3}', (
      tester,
    ) async {
      tester.view.physicalSize = Size(spec.$1, spec.$2);
      tester.view.devicePixelRatio = 1;
      tester.platformDispatcher.textScaleFactorTestValue = spec.$3;
      addTearDown(() => tester.view.resetPhysicalSize());
      addTearDown(() => tester.view.resetDevicePixelRatio());
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
      final c = LiftController(
        store: MemoryStore(),
        log: Diagnostics(MemoryStore()),
      );
      await c.initialize();
      c.dark = spec.$4;
      await tester.pumpWidget(LiftApp(controller: c, initialize: false));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('preview')));
      await tester.pumpAndSettle();
      expect(find.text('Здравствуйте, Демо-пользователь'), findsOneWidget);
      expect(tester.takeException(), isNull);
      for (final name in ['Заявки', 'Объекты', 'Дежурство', 'Ещё', 'Главная']) {
        await tester.tap(find.text(name).last);
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: name);
      }
      await tester.tap(find.byTooltip('Профиль и настройки'));
      await tester.pumpAndSettle();
      expect(find.text('Системные уведомления'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
      c.dispose();
    });
  }
  testWidgets('Switching demo employer hides the first company data', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(() => tester.view.resetPhysicalSize());
    addTearDown(() => tester.view.resetDevicePixelRatio());
    final c = LiftController(
      store: MemoryStore(),
      log: Diagnostics(MemoryStore()),
    );
    await c.initialize();
    c.preview();
    await tester.pumpWidget(LiftApp(controller: c, initialize: false));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Организация Альфа').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Организация Бета'));
    await tester.pumpAndSettle();
    expect(c.organization, 2);
    expect(find.textContaining('Учебное'), findsNothing);
    expect(find.text('Организация Бета'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
    c.dispose();
  });
}
