import 'package:flutter/services.dart';
import 'api.dart';

class DeviceBridge {
  static const channel = MethodChannel('ru.liftcontrol.app/device');
  static Future<Json> status() async => Map<String, dynamic>.from(
    await channel.invokeMethod<dynamic>('status') as Map,
  );
  static Future<String?> registerPush(Json config) =>
      channel.invokeMethod<String>('registerPush', config);
  static Future<bool> showSelfEvent(Json event) async =>
      await channel.invokeMethod<bool>('showSelfEvent', event) ?? false;
  static Future<void> disablePush() =>
      channel.invokeMethod<void>('disablePush');
  static Future<void> clearPrivateFiles() => const MethodChannel(
    'ru.liftcontrol.app/files',
  ).invokeMethod<void>('clearPrivateFiles');
  static Future<void> activatePush() =>
      channel.invokeMethod<void>('activatePush');
  static Future<void> testPush() => channel.invokeMethod<void>('testPush');
  static Future<void> notificationSettings() =>
      channel.invokeMethod<void>('notificationSettings');
  static Future<void> categorySettings(String category) => channel
      .invokeMethod<void>('notificationSettings', {'category': category});
  static Future<void> testCategory(String category) =>
      channel.invokeMethod<void>('testPush', {'category': category});
  static Future<Json?> consumeNotification() async {
    final result = await channel.invokeMethod<dynamic>('consumeNotification');
    return result is Map ? Map<String, dynamic>.from(result) : null;
  }

  static Future<void> installUpdate(Json data) =>
      channel.invokeMethod<void>('installUpdate', data);
}
