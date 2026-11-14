import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import 'diagnostics.dart';

typedef Json = Map<String, dynamic>;
List<Json> records(dynamic value) =>
    (value as List).map((e) => Map<String, dynamic>.from(e as Map)).toList();

class ApiError implements Exception {
  const ApiError(this.message, {this.status, this.network = false});
  final String message;
  final int? status;
  final bool network;
  @override
  String toString() => message;
}

Uri parseServer(String raw) {
  final uri = Uri.tryParse(raw.trim());
  if (uri == null ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      !['', '/'].contains(uri.path)) {
    throw const ApiError(
      'Укажите адрес сервера, например https://ваш-домен.ru',
    );
  }
  final loopback = ['127.0.0.1', 'localhost', '::1'].contains(uri.host);
  if (uri.scheme != 'https' && !(uri.scheme == 'http' && loopback)) {
    throw const ApiError(
      'Для подключения нужен HTTPS. HTTP разрешён только для локального туннеля.',
    );
  }
  return uri.replace(path: '/', query: null, fragment: null);
}

class ApiClient {
  ApiClient({
    required this.server,
    required this.log,
    this.token,
    this.organization,
    http.Client? transport,
  }) : _transport = transport ?? http.Client(),
       _ownsTransport = transport == null;
  final Uri server;
  final String? token;
  final int? organization;
  final Diagnostics log;
  final http.Client _transport;
  final bool _ownsTransport;

  void close() {
    if (_ownsTransport) _transport.close();
  }

  Map<String, String> get headers => {
    'Accept': 'application/json',
    if (token != null) 'Authorization': 'Bearer $token',
    if (organization != null) 'X-Organization-ID': '$organization',
  };

  Uri endpoint(String path) {
    if (!RegExp(r'^[a-z0-9_/-]+$').hasMatch(path) ||
        path.startsWith('/') ||
        path.contains('//')) {
      throw const ApiError('Некорректный адрес запроса.');
    }
    return server.resolve('/api/$path');
  }

  Future<http.Response> _send(
    http.BaseRequest request,
    String route, {
    int limit = 4 * 1024 * 1024,
    bool allowNotModified = false,
  }) async {
    request.followRedirects =
        false; // Never forward a bearer token to a redirected host.
    request.headers.addAll(headers);
    final clock = Stopwatch()..start();
    try {
      final stream = await _transport
          .send(request)
          .timeout(const Duration(seconds: 20));
      final bytes = BytesBuilder(copy: false);
      await for (final chunk in stream.stream.timeout(
        const Duration(seconds: 30),
      )) {
        if (bytes.length + chunk.length > limit) {
          throw const ApiError('Ответ сервера слишком большой.');
        }
        bytes.add(chunk);
      }
      final response = http.Response.bytes(
        bytes.takeBytes(),
        stream.statusCode,
        headers: stream.headers,
      );
      log.record(
        'request',
        route: route,
        status: response.statusCode,
        elapsed: clock.elapsedMilliseconds,
      );
      if (allowNotModified && response.statusCode == 304) return response;
      if (response.statusCode >= 300 && response.statusCode < 400) {
        throw const ApiError(
          'Сервер перенаправляет запрос. Проверьте адрес HTTPS.',
        );
      }
      if (response.statusCode < 200 || response.statusCode >= 300) {
        var message = response.statusCode == 401
            ? 'Сеанс завершён. Войдите заново.'
            : 'Сервер отклонил запрос (${response.statusCode}).';
        try {
          final body = jsonDecode(utf8.decode(response.bodyBytes));
          if (body is Map) {
            message = body.entries
                .map(
                  (e) => e.value is List
                      ? (e.value as List).join(' ')
                      : '${e.value}',
                )
                .join('\n');
          } else if (body is List) {
            message = body.join('\n');
          }
        } catch (_) {
          /* HTML error pages are not displayed or logged. */
        }
        throw ApiError(
          message.length > 600 ? message.substring(0, 600) : message,
          status: response.statusCode,
        );
      }
      return response;
    } on ApiError {
      rethrow;
    } catch (_) {
      log.record(
        'network_unavailable',
        route: route,
        elapsed: clock.elapsedMilliseconds,
      );
      throw const ApiError(
        'Нет связи с сервером. Проверьте подключение и адрес.',
        network: true,
      );
    }
  }

  Future<dynamic> call(
    String path, {
    String method = 'GET',
    Json? data,
    Map<String, String>? query,
  }) async {
    var uri = endpoint(path);
    if (query != null) uri = uri.replace(queryParameters: query);
    final request = http.Request(method, uri);
    if (data != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(data);
    }
    final response = await _send(request, path);
    try {
      return jsonDecode(utf8.decode(response.bodyBytes));
    } catch (_) {
      throw const ApiError('Сервер вернул неподдерживаемый ответ.');
    }
  }

  Future<void> uploadPhoto(int ticket, Uint8List bytes, String fileName) async {
    if (bytes.length > 10 * 1024 * 1024) {
      throw const ApiError('Максимальный размер фотографии — 10 МБ.');
    }
    final route = 'tickets/$ticket/photos/';
    final request = http.MultipartRequest('POST', endpoint(route));
    request.files.add(
      http.MultipartFile.fromBytes('file', bytes, filename: fileName),
    );
    await _send(request, route);
  }

  Future<dynamic> uploadMedia(
    String route,
    List<({Uint8List bytes, String name})> files, {
    Map<String, String> fields = const {},
    String field = 'files',
  }) async {
    if (files.length > 5 ||
        files.fold<int>(0, (total, file) => total + file.bytes.length) >
            20 * 1024 * 1024) {
      throw const ApiError('До 5 вложений и 20 МБ на сообщение.');
    }
    final request = http.MultipartRequest('POST', endpoint(route));
    request.fields.addAll(fields);
    for (final file in files) {
      request.files.add(
        http.MultipartFile.fromBytes(field, file.bytes, filename: file.name),
      );
    }
    final response = await _send(request, route);
    return jsonDecode(utf8.decode(response.bodyBytes));
  }

  Uri resourceUri(String path) {
    final uri = server.resolve(path);
    if (uri.origin != server.origin ||
        uri.userInfo.isNotEmpty ||
        uri.hasFragment ||
        !RegExp(
          r'^/api/(maintenance/\d+/journal/pdf|tickets/\d+/photos/\d+|documents/\d+/download|(?:chat|tasks)/\d+/attachments/\d+|media/[0-9a-f-]{36}/(?:download|preview)|commissioning/\d+/expenses/\d+/receipt)/$',
        ).hasMatch(uri.path) ||
        (uri.queryParameters['organization'] != null &&
            uri.queryParameters['organization'] != '$organization')) {
      throw const ApiError('Файл недоступен по этому адресу.');
    }
    return uri;
  }

  Future<Uint8List> download(
    String path, {
    bool pdf = false,
    bool video = false,
  }) async {
    final file = await fetchDownload(path, pdf: pdf, video: video);
    return file.bytes!;
  }

  Future<({Uint8List? bytes, String etag})> fetchDownload(
    String path, {
    bool pdf = false,
    bool video = false,
    String etag = '',
  }) async {
    final uri = resourceUri(path);
    final request = http.Request('GET', uri);
    if (etag.isNotEmpty) request.headers['If-None-Match'] = etag;
    final response = await _send(
      request,
      uri.path,
      limit: 32 * 1024 * 1024,
      allowNotModified: etag.isNotEmpty,
    );
    if (response.statusCode == 304) return (bytes: null, etag: etag);
    final contentType = response.headers['content-type'] ?? '';
    if (pdf
        ? !contentType.startsWith('application/pdf')
        : video
        ? ![
            'video/mp4',
            'video/webm',
            'video/quicktime',
          ].any(contentType.startsWith)
        : !contentType.startsWith('image/')) {
      throw const ApiError('Формат файла не поддерживается.');
    }
    final tag = response.headers['etag'] ?? '';
    return (bytes: response.bodyBytes, etag: tag.length > 100 ? '' : tag);
  }
}
