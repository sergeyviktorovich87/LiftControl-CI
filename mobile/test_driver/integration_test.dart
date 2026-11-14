import 'dart:io';
import 'dart:convert';
import 'package:integration_test/integration_test_driver_extended.dart';

Future<void> main() async {
  await integrationDriver(
    onScreenshot: (name, bytes, [args]) async {
      final directory = Directory('test-output')..createSync(recursive: true);
      await File('${directory.path}/$name.png').writeAsBytes(bytes);
      if ([
            '07b-personal-task-form',
            '09-chat-photo',
            '10-personal-task-completed',
          ].contains(name) &&
          bytes.length < 600000) {
        final data = base64Encode(bytes);
        stdout.writeln('LC_SCREENSHOT_BEGIN $name');
        for (var i = 0; i < data.length; i += 2000) {
          stdout.writeln(
            'LC_SCREENSHOT_DATA ${data.substring(i, i + 2000 < data.length ? i + 2000 : data.length)}',
          );
        }
        stdout.writeln('LC_SCREENSHOT_END $name');
      }
      return true;
    },
    writeResponseOnFailure: true,
  );
}
