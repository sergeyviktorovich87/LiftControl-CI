import 'package:flutter/services.dart';

class PlatformBridge {
  static const _channel = MethodChannel('ru.liftcontrol.app/files');
  static Future<void> openPdf(Uint8List bytes) =>
      _channel.invokeMethod<void>('openPdf', {'bytes': bytes});
  static Future<void> openVideo(Uint8List bytes, String contentType) =>
      _channel.invokeMethod<void>('openVideo', {
        'bytes': bytes,
        'content_type': contentType,
      });
  static Future<void> shareReport(String text) =>
      _channel.invokeMethod<void>('shareReport', {'text': text});
  static Future<void> clearPrivateFiles() =>
      _channel.invokeMethod<void>('clearPrivateFiles');
}
