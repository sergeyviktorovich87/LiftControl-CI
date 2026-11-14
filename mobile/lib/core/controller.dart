import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'api.dart';
import 'demo.dart';
import 'diagnostics.dart';
import 'storage.dart';
import 'outbox.dart';
import 'device_bridge.dart';
import 'self_push.dart';
import 'media_files.dart';

class LiftController extends ChangeNotifier {
  LiftController({
    required this.store,
    required this.log,
    this.transport,
    Outbox? outbox,
    PendingMediaFiles? mediaFiles,
  }) : mediaFiles =
           mediaFiles ??
           (store is DeviceStore
               ? DeviceMediaFiles()
               : StoredMediaFiles(store)),
       outbox =
           outbox ??
           (store is DeviceStore ? DeviceOutbox() : StoredOutbox(store));
  final SecretStore store;
  final Diagnostics log;
  final http.Client? transport;
  final Outbox outbox;
  final PendingMediaFiles mediaFiles;
  final DownloadCache _downloadCache = DownloadCache();
  int cacheLimit = 100;
  static const cacheLimits = [100, 500, 1024];
  List<Json> _outboxRows = [];
  final Map<int, int> _resolvedTickets = {};
  Future<void>? _syncFuture;
  int _syncGeneration = -1;
  bool _outboxReady = false;
  bool get syncing => _syncFuture != null && _syncGeneration == _generation;
  List<Json> get pendingOperations =>
      List.unmodifiable(_outboxRows.where((r) => r['state'] != 'synced'));
  int get pendingCount => pendingOperations.length;
  int get failedCount =>
      pendingOperations.where((r) => r['state'] == 'failed').length;
  bool get canRecord =>
      !demo &&
      session != null &&
      organization != null &&
      me.isNotEmpty &&
      _outboxReady &&
      !_switchingOrganization;
  bool get canCreateTicket {
    if (!canRecord || me['can_create_ticket'] != true) return false;
    if (me['role'] != 'MECHANIC') return true;
    final shift = me['duty_shift'];
    if (shift is! Map) return false;
    final end = DateTime.tryParse('${shift['end']}');
    return end != null && DateTime.now().toUtc().isBefore(end.toUtc());
  }

  Json? session;
  Json snapshot = {};
  List<Json> organizations = [];
  int? organization;
  bool starting = true, refreshing = false, online = false, demo = false;
  String appearance = 'light';
  static const appearances = [
    'light',
    'dark',
    'auto',
    'blue',
    'graphite',
    'green',
    'purple',
  ];
  bool get dark => appearance == 'dark' || appearance == 'graphite';
  set dark(bool value) => appearance = value ? 'dark' : 'light';
  String serverText = '', error = '';
  DateTime? syncedAt;
  int _generation = 0;
  Future<void>? _refreshFuture;
  int _refreshGeneration = -1;
  bool _refreshAgain = false;
  int _organizationSwitch = 0;
  bool _switchingOrganization = false;
  Future<void> _writes = Future.value();
  Map<String, dynamic> _resources = {}, _drafts = {};
  Timer? _draftTimer;
  DateTime? _checkedUpdates;
  Future<Json?>? _updatesFuture;
  Json? updates;
  int updateCheckSerial = 0;
  int syncRevision = 0;
  int? installedVersionCode;
  bool get hasUpdate =>
      updates?['android'] is Map &&
      installedVersionCode != null &&
      updates!['android']['version_code'] is int &&
      (updates!['android']['version_code'] as int) > installedVersionCode!;
  bool _pushSyncing = false;
  SelfPushPoller? _selfPushPoller;
  int? _selfPushGeneration;
  int? _selfPushOrg;

  bool get loggedIn => session != null || demo;
  bool get canWrite => online && !demo && session != null;
  Json get me =>
      snapshot['me'] is Map ? Map<String, dynamic>.from(snapshot['me']) : {};
  int get utcOffset => (me['utc_offset_minutes'] as num?)?.toInt() ?? 0;
  List<Json> list(String name) {
    final base = snapshot[name] is List ? records(snapshot[name]) : <Json>[];
    if (name == 'chat') return _chatWithPending(base);
    if (name != 'tickets') return base;
    final tickets = {for (final row in base) row['id'] as int: row};
    for (final row in _outboxRows) {
      final command = Map<String, dynamic>.from(row['command'] as Map);
      if (['media', 'commissioning'].contains(command['type'])) continue;
      final response = row['response'];
      if (row['state'] == 'synced' &&
          response is Map &&
          response['ticket'] is Map) {
        final ticket = Map<String, dynamic>.from(response['ticket']);
        tickets[ticket['id'] as int] = ticket;
        if (row['local_id'] is int) tickets.remove(row['local_id']);
        continue;
      }
      final id = (command['ticket'] ?? row['local_id']) as int;
      final existing =
          tickets[id] ??
          (row['local_ticket'] is Map
              ? Map<String, dynamic>.from(row['local_ticket'])
              : <String, dynamic>{});
      if (existing.isEmpty) continue;
      final ticket = {
        ...existing,
        'sync_state': row['state'],
        'sync_error': row['error'] ?? '',
        'sync_operation': row['id'],
      };
      final body = command['body'] as Map;
      if (row['state'] != 'failed') {
        switch (command['type']) {
          case 'accept':
            ticket['status'] = 'IN_PROGRESS';
            ticket['assigned_to'] = me['technician'];
            ticket['technician_name'] = me['name'];
          case 'complete':
            ticket['status'] = 'DONE';
            ticket['repair'] = body['work_description'];
          case 'stop':
            ticket['status'] = 'STOPPED';
            ticket['stop_reason'] = body['reason'];
            ticket['stopped_at'] = command['occurred_at'];
          case 'resume':
            ticket['status'] = 'IN_PROGRESS';
          case 'cancel':
            ticket['status'] = 'CANCELED';
            ticket['cancellation_reason'] = body['reason'];
          case 'assign':
            ticket['assigned_to'] = body['assigned_to'];
            ticket['technician_name'] =
                list('technicians').firstWhere(
                  (t) => t['id'] == body['assigned_to'],
                  orElse: () => {},
                )['full_name'] ??
                '';
        }
        if (id > 0) {
          final own =
              ticket['assigned_to'] == me['technician'] &&
              me['role'] == 'MECHANIC';
          final responsible =
              ticket['assigned_to'] ??
              list('elevators').firstWhere(
                (e) => e['id'] == ticket['elevator'],
                orElse: () => {},
              )['assigned_technician'];
          ticket['can_accept'] =
              me['role'] == 'MECHANIC' &&
              responsible == me['technician'] &&
              ticket['status'] == 'NEW';
          ticket['can_complete'] =
              own && ['IN_PROGRESS', 'STOPPED'].contains(ticket['status']);
          ticket['can_stop'] = own && ticket['status'] == 'IN_PROGRESS';
          ticket['can_resume'] = own && ticket['status'] == 'STOPPED';
          ticket['can_assign'] =
              ticket['can_assign'] == true && ticket['status'] == 'NEW';
          ticket['can_cancel'] =
              ticket['can_cancel'] == true &&
              ticket['status'] != 'CANCELED' &&
              (ticket['status'] != 'DONE' ||
                  ['ADMIN', 'DIRECTOR'].contains(me['role']));
        }
      }
      tickets[id] = ticket;
    }
    return tickets.values.toList();
  }

  int resolvedTicket(int id) => _resolvedTickets[id] ?? id;
  Future<dynamic> _sendMedia(ApiClient api, String key, Json row) async {
    final body = Map<String, dynamic>.from(row['command']['body']);
    final fileId = body['file_id'];
    return api.uploadMedia(
      'media/uploads/',
      [
        if (fileId is String)
          (
            bytes: await mediaFiles.read(key, fileId),
            name: body['name'] as String,
          ),
      ],
      field: 'file',
      fields: {
        'upload_id': row['id'] as String,
        for (final k in [
          'kind',
          'target_id',
          'body',
          'message_key',
          'category',
          'linked_ticket',
          'linked_part',
          'sha256',
          'name',
        ])
          if (body[k] != null) k: '${body[k]}',
      },
    );
  }

  Future<Json> queueMedia(
    String kind,
    List<({Uint8List bytes, String name})> files, {
    int? target,
    String body = '',
    String category = 'OTHER',
    int? linkedTicket,
    int? linkedPart,
  }) async {
    if (!canRecord || me['media_api'] != 1) {
      throw const ApiError('Обновите сервер и загрузите данные организации.');
    }
    if (!['chat', 'ticket', 'elevator', 'task', 'library'].contains(kind) ||
        (files.isEmpty &&
            (kind != 'chat' ||
                (body.trim().isEmpty &&
                    linkedTicket == null &&
                    linkedPart == null))) ||
        (['ticket', 'elevator', 'task'].contains(kind) &&
            (target == null || target <= 0))) {
      throw const ApiError('Выберите файл и место его сохранения.');
    }
    if (files.length > 5 ||
        files.fold<int>(0, (sum, f) => sum + f.bytes.length) >
            20 * 1024 * 1024) {
      throw const ApiError('До 5 файлов и 20 МБ на отправку.');
    }
    final key = scope,
        captured = _generation,
        messageKey = _operationId(),
        at = DateTime.now().toUtc().toIso8601String();
    final rows = <Json>[], staged = <String>[];
    try {
      for (var i = 0; i < (files.isEmpty ? 1 : files.length); i++) {
        final id = _operationId();
        Json file = {};
        if (files.isNotEmpty) {
          file = await mediaFiles.stage(key, id, files[i].bytes);
          staged.add(id);
        }
        final name = files.isEmpty ? 'Сообщение' : files[i].name;
        rows.add({
          'id': id,
          'command': {
            'id': id,
            'type': 'media',
            'ticket': kind == 'ticket' ? target : 0,
            'occurred_at': at,
            'body': {
              'kind': kind,
              'target_id': ?target,
              'body': body,
              'message_key': messageKey,
              'category': category,
              'linked_ticket': ?linkedTicket,
              'linked_part': ?linkedPart,
              'name': name,
              ...file,
            },
          },
          'local_ticket': {
            'title': kind == 'chat' ? 'Чат команды' : name,
            'address': 'Фото и видео',
            'elevator_label': target == null ? '' : '#$target',
          },
        });
      }
      await outbox.enqueue(key, rows);
    } catch (_) {
      for (final id in staged) {
        await mediaFiles.remove(key, id);
      }
      log.record('media_stage_failed');
      rethrow;
    }
    log.record('media_queued');
    if (captured == _generation) {
      await _loadOutbox();
      notifyListeners();
      unawaited(refresh());
    }
    return {'saved_on_device': true};
  }

  Future<Uint8List> pendingMedia(String key, String id) async {
    if (session == null || key != scope) {
      throw const ApiError('Нет доступа к файлу этой организации.');
    }
    return mediaFiles.read(key, id);
  }

  bool get canRecordCommissioning =>
      canRecord &&
      me['commissioning_sync_api'] == 1 &&
      me['can_commissioning'] == true;

  Future<void> queueCommissioning(
    int project,
    String kind,
    Json data, {
    required int expectedOrganization,
    Uint8List? receipt,
  }) async {
    if (!canRecordCommissioning || organization != expectedOrganization) {
      throw const ApiError(
        'Обновите данные организации и откройте объект заново.',
      );
    }
    if (receipt != null &&
        (receipt.isEmpty || receipt.length > 10 * 1024 * 1024)) {
      throw const ApiError('Фото чека должно быть до 10 МБ.');
    }
    final id = _operationId(), key = scope, captured = _generation;
    final body = {...data};
    if (kind == 'expense') {
      body['spent_on'] ??= orgNow.toIso8601String().substring(0, 10);
    }
    if (receipt != null) {
      await mediaFiles.stage(key, id, receipt);
    }
    try {
      if (captured != _generation) {
        throw const ApiError('Организация изменилась. Откройте объект заново.');
      }
      await outbox.enqueue(key, [
        {
          'id': id,
          'commissioning_project': project,
          if (receipt != null) 'file_id': id,
          'command': {
            'id': id,
            'type': 'commissioning',
            'action': kind,
            'body': body,
          },
          'local_ticket': {
            'title': switch (kind) {
              'progress' => 'Прогресс наладки',
              'expense' => 'Личный расход',
              'receipt' => 'Фото чека',
              _ => 'Сообщение по наладке',
            },
            'address': 'Наладка',
            'elevator_label': 'Объект #$project',
          },
        },
      ]);
    } catch (_) {
      if (receipt != null) await mediaFiles.remove(key, id);
      log.record('commissioning_queue_failed');
      rethrow;
    }
    log.record('commissioning_queued');
    if (captured == _generation) {
      await _loadOutbox();
      notifyListeners();
      unawaited(refresh());
    }
  }

  Future<dynamic> _sendCommissioning(
    ApiClient api,
    String key,
    Json row,
  ) async {
    final saved = row['command'] as Map;
    final command = <String, dynamic>{
      'id': row['id'],
      'type': saved['action'],
      'body': saved['body'],
    };
    final route = 'commissioning/${row['commissioning_project']}/sync/';
    final file = row['file_id'] as String?;
    return file == null
        ? api.call(route, method: 'POST', data: command)
        : api.uploadMedia(
            route,
            [(bytes: await mediaFiles.read(key, file), name: 'receipt.jpg')],
            field: 'receipt',
            fields: {'command': jsonEncode(command)},
          );
  }

  List<Json> _chatWithPending(List<Json> base) {
    final result = [...base];
    final groups = <String, List<Json>>{};
    for (final row in _outboxRows) {
      final command = row['command'] as Map;
      if (command['type'] != 'media' || command['body']['kind'] != 'chat') {
        continue;
      }
      (groups['${command['body']['message_key']}'] ??= []).add(row);
    }
    for (final group in groups.values) {
      final body = group.first['command']['body'] as Map;
      final received = group
          .where((r) => r['response']?['message'] is int)
          .firstOrNull?['response']?['message'];
      final index = result.indexWhere((r) => r['id'] == received);
      final pending = group.where((r) => r['state'] != 'synced').toList();
      if (index >= 0 && pending.isEmpty) continue;
      final attachments = <Json>[
        for (final row in index >= 0 ? pending : group)
          if (row['command']['body']['file_id'] != null)
            row['state'] == 'synced' && row['response']?['asset'] is Map
                ? Map<String, dynamic>.from(row['response']['asset'])
                : {
                    'id': row['id'],
                    'pending': true,
                    'local_scope': scope,
                    'file_id': row['command']['body']['file_id'],
                    'size': row['command']['body']['size'],
                    'name': row['command']['body']['name'],
                    'content_type': mediaType(
                      '${row['command']['body']['name']}',
                    ),
                  },
      ];
      final status = group.any((r) => r['state'] == 'failed')
          ? 'Требует внимания'
          : pending.isEmpty
          ? 'На сервере'
          : 'Сохранено на телефоне · ожидает отправки';
      if (index >= 0) {
        result[index] = {
          ...result[index],
          'attachments': [
            ...records(result[index]['attachments']),
            ...attachments,
          ],
          'delivery_state': status,
        };
      } else {
        result.add({
          'id': 'local-${group.first['id']}',
          'author': me['id'],
          'author_name': me['name'],
          'body': body['body'],
          'created_at': group.first['command']['occurred_at'],
          'attachments': attachments,
          'delivery_state': status,
        });
      }
    }
    return result;
  }

  static String mediaType(String name) {
    final extension = name.toLowerCase().split('.').last;
    return {
          'mp4': 'video/mp4',
          'mov': 'video/quicktime',
          'webm': 'video/webm',
          'png': 'image/png',
          'webp': 'image/webp',
        }[extension] ??
        'image/jpeg';
  }

  Json localTicket(int id) => list(
    'tickets',
  ).firstWhere((t) => t['id'] == resolvedTicket(id), orElse: () => {});
  String get scope => storageScope(
    Uri.parse(session!['server'] as String),
    session!['user']['id'] as int,
    organization!,
  );
  int get generation => _generation;
  DateTime get orgNow =>
      DateTime.now().toUtc().add(Duration(minutes: utcOffset));

  ApiClient _api({bool unscoped = false}) => ApiClient(
    server: Uri.parse(session!['server']),
    token: session!['token'],
    organization: unscoped ? null : organization,
    log: log,
    transport: transport,
  );

  Future<void> _write(int generation, String key, String value) {
    _writes = _writes
        .then((_) async {
          if (_generation == generation) await store.write(key, value);
        })
        .catchError((Object _) {
          log.record('secure_write_failed');
        });
    return _writes;
  }

  Future<void> initialize() async {
    await log.restore();
    log.record('startup');
    try {
      serverText =
          await store.read('server') ??
          const String.fromEnvironment('API_SERVER');
      final savedAppearance = await store.read('theme');
      appearance = appearances.contains(savedAppearance)
          ? savedAppearance!
          : 'light';
      final limit = int.tryParse(
        await store.read('download_cache_limit') ?? '',
      );
      cacheLimit = cacheLimits.contains(limit) ? limit! : 100;
      final saved = await store.read('session');
      if (saved != null) {
        final value = Map<String, dynamic>.from(jsonDecode(saved));
        parseServer(value['server']);
        if (DateTime.parse(
          value['expires_at'],
        ).isAfter(DateTime.now().toUtc())) {
          session = value;
          organizations = records(value['organizations']);
          organization = value['organization'];
          await _restoreSnapshot();
        } else {
          await _clearAccount();
          error = 'Срок входа истёк. Войдите заново.';
        }
      }
    } catch (_) {
      await _clearAccount();
      error = 'Не удалось прочитать сохранённый сеанс. Войдите заново.';
    }
    starting = false;
    notifyListeners();
    if (session != null) {
      await refresh();
      unawaited(syncDevice());
    }
  }

  Future<void> login(String server, String username, String password) async {
    final uri = parseServer(server);
    final api = ApiClient(server: uri, log: log, transport: transport);
    try {
      final data = Map<String, dynamic>.from(
        await api.call(
          'mobile/login/',
          method: 'POST',
          data: {
            'username': username.trim(),
            'password': password,
            'device_name': 'LiftControl Android',
          },
        ),
      );
      if (data['token'] is! String ||
          data['user'] is! Map ||
          data['organizations'] is! List) {
        throw const ApiError(
          'Ответ входа не поддерживается. Проверьте адрес сервера.',
        );
      }
      _stopSelfPush();
      _generation++;
      _organizationSwitch++;
      _switchingOrganization = false;
      demo = false;
      organizations = records(data['organizations']);
      organization = organizations.isEmpty
          ? null
          : organizations.first['id'] as int;
      session = {
        ...data,
        'server': uri.toString(),
        'organization': organization,
      };
      serverText = uri.toString();
      online = true;
      error = '';
      snapshot = {};
      _resources = {};
      _drafts = {};
      _outboxRows = [];
      _resolvedTickets.clear();
      await _write(_generation, 'server', serverText);
      await _saveSession();
      await _loadOutbox();
      log.record('login_success');
      notifyListeners();
      await refresh();
      unawaited(syncDevice());
    } on ApiError catch (e) {
      if (e.status == 404) {
        throw const ApiError(
          'На сервере ещё нет входа для Android. Установите обновление API для приложения.',
        );
      }
      rethrow;
    } finally {
      api.close();
    }
  }

  void preview() {
    _stopSelfPush();
    _generation++;
    _organizationSwitch++;
    _switchingOrganization = false;
    demo = true;
    online = false;
    error = '';
    organization = 1;
    organizations = [
      {'id': 1, 'name': 'Организация Альфа', 'role': 'MECHANIC'},
      {'id': 2, 'name': 'Организация Бета', 'role': 'DIRECTOR'},
    ];
    snapshot = demoSnapshot(1);
    syncedAt = null;
    notifyListeners();
  }

  Future<void> _saveSession() async {
    if (session == null) return;
    session!['organization'] = organization;
    session!['organizations'] = organizations;
    await _write(_generation, 'session', jsonEncode(session));
  }

  Future<void> _restoreSnapshot() async {
    final captured = _generation;
    snapshot = {};
    _resources = {};
    _drafts = {};
    _outboxRows = [];
    _outboxReady = false;
    _resolvedTickets.clear();
    syncedAt = null;
    online = false;
    if (session == null || organization == null) return;
    final key = scope;
    Json restoredSnapshot = {};
    Map<String, dynamic> restoredResources = {}, restoredDrafts = {};
    DateTime? restoredAt;
    try {
      final saved = await store.read('cache_$key');
      if (captured != _generation) return;
      if (saved != null) {
        final data = jsonDecode(saved) as Map;
        final at = DateTime.parse(data['at']);
        if (DateTime.now().toUtc().difference(at) < const Duration(days: 7)) {
          restoredSnapshot = Map<String, dynamic>.from(data['snapshot']);
          restoredAt = at;
        }
      }
    } catch (_) {
      log.record('cache_read_failed');
    }
    try {
      final values = await Future.wait([
        store.read('resources_$key'),
        store.read('drafts_$key'),
      ]);
      if (captured != _generation) return;
      restoredResources = Map<String, dynamic>.from(
        jsonDecode(values[0] ?? '{}'),
      );
      restoredDrafts = Map<String, dynamic>.from(jsonDecode(values[1] ?? '{}'));
    } catch (_) {
      log.record('draft_read_failed');
    }
    if (captured != _generation) return;
    snapshot = restoredSnapshot;
    syncedAt = restoredAt;
    _resources = restoredResources;
    _drafts = restoredDrafts;
    await _loadOutbox();
  }

  Future<void> _loadOutbox({bool configureAuth = true}) async {
    if (session == null || organization == null) return;
    final captured = _generation, key = scope;
    try {
      final rows = await outbox.rows(key);
      if (captured != _generation) return;
      _outboxRows = rows;
      _outboxReady = true;
      for (final row in rows) {
        if (row['local_id'] is int &&
            row['response'] is Map &&
            row['response']['ticket'] is Map) {
          _resolvedTickets[row['local_id'] as int] =
              row['response']['ticket']['id'] as int;
        }
      }
      if (configureAuth) {
        await outbox.configure(key, {
          'server': session!['server'],
          'token': session!['token'],
          'organization': organization,
          'expires_at': session!['expires_at'],
        });
      }
    } catch (_) {
      if (captured != _generation) return;
      _outboxReady = false;
      error =
          'Не удалось прочитать очередь телефона. Сохранённые записи не удалены.';
      log.record('outbox_read_failed');
    }
  }

  Future<void> syncPending() {
    if (session == null || organization == null || demo || !_outboxReady) {
      return Future.value();
    }
    if (_syncFuture != null && _syncGeneration == _generation) {
      return _syncFuture!;
    }
    final captured = _generation, key = scope;
    final done = Completer<void>();
    _syncFuture = done.future;
    _syncGeneration = captured;
    unawaited(() async {
      final api = _api();
      try {
        if (outbox.nativeSync) {
          await outbox.sync(key);
        } else {
          final blocked = <int>{};
          for (final row in await outbox.rows(key)) {
            if (captured != _generation) break;
            final command = Map<String, dynamic>.from(row['command']);
            final commissioning = command['type'] == 'commissioning';
            final target = commissioning
                ? -(2000000000 + (row['commissioning_project'] as int))
                : (command['ticket'] ?? row['local_id']) as int;
            if (row['state'] == 'failed') {
              blocked.add(target);
              continue;
            }
            if (row['state'] != 'queued' || blocked.contains(target)) continue;
            try {
              final media = command['type'] == 'media';
              final response = Map<String, dynamic>.from(
                commissioning
                    ? await _sendCommissioning(api, key, row)
                    : media
                    ? await _sendMedia(api, key, row)
                    : await api.call(
                        'mobile/sync/',
                        method: 'POST',
                        data: command,
                      ),
              );
              if (response['operation_id'] != row['id'] ||
                  (commissioning
                      ? response['result'] is! Map
                      : media
                      ? response['state'] != 'READY' ||
                            response['asset'] == null &&
                                response['message'] is! int
                      : response['ticket'] is! Map ||
                            response['ticket']['id'] is! int)) {
                throw const ApiError(
                  'Сервер не подтвердил сохранение операции.',
                  network: true,
                );
              }
              await outbox.finish(
                key,
                row['id'] as String,
                'synced',
                response: response,
              );
              log.record('outbox_sent');
            } on ApiError catch (e) {
              if (e.network ||
                  e.status == null ||
                  e.status! >= 500 ||
                  [408, 425, 429].contains(e.status)) {
                if (captured == _generation) online = false;
                log.record('outbox_retry', status: e.status);
                break;
              }
              await outbox.finish(
                key,
                row['id'] as String,
                'failed',
                error: e.message,
              );
              blocked.add(target);
              log.record('outbox_rejected', status: e.status);
              if (e.status == 401) break;
            }
          }
        }
        if (captured == _generation) await _loadOutbox();
        done.complete();
      } catch (e, st) {
        log.record('outbox_sync_failed');
        done.completeError(e, st);
      } finally {
        api.close();
        if (identical(_syncFuture, done.future)) _syncFuture = null;
        if (captured == _generation) notifyListeners();
      }
    }());
    return done.future;
  }

  Future<void> retryOperation(String id) async {
    if (session == null || organization == null) return;
    await outbox.retry(scope, id);
    await _loadOutbox();
    notifyListeners();
    unawaited(refresh());
  }

  String _operationId() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    final h = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
  }

  Future<Json> _recordTicket(String kind, Json body, {int? ticketId}) async {
    if (!canRecord) {
      throw const ApiError('Сначала войдите и загрузите данные организации.');
    }
    if (kind == 'create' && !canCreateTicket) {
      throw const ApiError(
        'Создавать заявки может диспетчер или дежурный в течение смены.',
      );
    }
    final captured = _generation, key = scope, id = _operationId();
    final at = DateTime.now().toUtc().toIso8601String();
    Json ticket;
    int? localId;
    if (kind == 'create') {
      final lift = list(
        'elevators',
      ).firstWhere((e) => e['id'] == body['elevator'], orElse: () => {});
      if (lift.isEmpty || lift['active'] == false) {
        throw const ApiError(
          'Выберите действующий лифт из сохранённого списка.',
        );
      }
      localId =
          -DateTime.now().microsecondsSinceEpoch -
          Random.secure().nextInt(1000);
      final assignee = body['assigned_to'] ?? lift['assigned_technician'];
      final person = list(
        'technicians',
      ).firstWhere((e) => e['id'] == assignee, orElse: () => {});
      ticket = {
        'id': localId,
        ...body,
        'address': lift['address'],
        'elevator_label': lift['factory_number'] ?? lift['inventory_number'],
        'assigned_to': assignee,
        'technician_name': person['full_name'] ?? '',
        'created_at': at,
        'status': 'NEW',
        'photos': <Json>[],
        'can_accept': false,
        'can_complete': false,
        'can_stop': false,
        'can_resume': false,
        'can_cancel': false,
      };
    } else {
      ticketId = resolvedTicket(ticketId!);
      ticket = localTicket(ticketId);
      if (ticketId <= 0 || ticket.isEmpty) {
        throw const ApiError(
          'Дождитесь отправки новой заявки. Остальные записи уже сохранены.',
        );
      }
      if (pendingOperations.any(
        (r) => r['command']['ticket'] == ticketId && r['state'] == 'failed',
      )) {
        throw const ApiError(
          'Сначала проверьте ошибку отправки этой заявки в очереди.',
        );
      }
      final permission = {
        'accept': 'can_accept',
        'complete': 'can_complete',
        'stop': 'can_stop',
        'resume': 'can_resume',
        'photo': 'can_complete',
        'cancel': 'can_cancel',
        'assign': 'can_assign',
      }[kind];
      if (kind == 'comment' &&
          (!['NEW', 'IN_PROGRESS', 'STOPPED'].contains(ticket['status']) ||
              !(ticket['can_complete'] == true ||
                  me['on_duty'] == true ||
                  me['can_manage'] == true))) {
        throw const ApiError(
          'Комментировать может исполнитель, дежурный или диспетчер открытой заявки.',
        );
      }
      if (permission != null && ticket[permission] != true) {
        throw const ApiError('Это действие недоступно для выбранной заявки.');
      }
    }
    final command = <String, dynamic>{
      'id': id,
      'type': kind,
      'body': body,
      'occurred_at': at,
      'ticket': ?ticketId,
      if (kind == 'create' && me['offline_grant'] is String)
        'grant': me['offline_grant'],
    };
    await outbox.enqueue(key, [
      {
        'id': id,
        'command': command,
        'local_ticket': ticket,
        'local_id': ?localId,
      },
    ]);
    log.record('outbox_saved');
    if (captured != _generation) {
      throw const ApiError(
        'Организация изменилась. Запись сохранена в очереди прежней организации.',
      );
    }
    // Saving must never wait for a network timeout or the background worker's
    // authentication lock. Its durable queue already owns the operation.
    await _loadOutbox(configureAuth: false);
    final saved = localTicket(ticketId ?? localId!);
    notifyListeners();
    if (online) unawaited(refresh());
    return saved;
  }

  Future<void> selectOrganization(int id) async {
    if (!organizations.any((o) => o['id'] == id)) {
      throw const ApiError('Нет доступа к организации.');
    }
    final selection = ++_organizationSwitch;
    if (!_switchingOrganization) await flushDrafts();
    if (selection != _organizationSwitch) return;
    _stopSelfPush();
    final captured = ++_generation;
    _switchingOrganization = true;
    log.record('organization_switch_started');
    organization = id;
    updates = null;
    _checkedUpdates = null;
    _updatesFuture = null;
    refreshing = true;
    error = '';
    if (demo) {
      snapshot = demoSnapshot(id);
      refreshing = false;
      _switchingOrganization = false;
      notifyListeners();
      return;
    }
    final restoration = _restoreSnapshot();
    notifyListeners();
    try {
      await restoration;
      if (captured != _generation) return;
      await _saveSession();
      if (captured != _generation) return;
      // No asynchronous gap between releasing the guard and starting refresh.
      refreshing = false;
      await refresh();
    } finally {
      if (captured == _generation) {
        _switchingOrganization = false;
        refreshing = false;
        log.record('organization_switch_finished');
        notifyListeners();
        unawaited(syncDevice());
      } else {
        log.record('organization_switch_superseded');
      }
    }
  }

  Future<void> refresh() {
    if (!loggedIn || demo) {
      return Future<void>.value();
    }
    final captured = _generation;
    if (_refreshFuture != null && _refreshGeneration == captured) {
      // A write may finish while an older read is still returning. Do not
      // discard its refresh or resolve the caller before the fresh pass.
      _refreshAgain = true;
      return _refreshFuture!;
    }
    if (_switchingOrganization && refreshing) {
      return Future<void>.value();
    }
    final done = Completer<void>();
    _refreshGeneration = captured;
    _refreshFuture = done.future;
    unawaited(() async {
      try {
        do {
          _refreshAgain = false;
          await _refreshOnce();
        } while (captured == _generation && loggedIn && !demo && _refreshAgain);
        done.complete();
      } catch (error, stack) {
        done.completeError(error, stack);
      } finally {
        if (identical(_refreshFuture, done.future)) {
          _refreshFuture = null;
        }
      }
    }());
    return done.future;
  }

  Future<void> _refreshOnce() async {
    final captured = _generation;
    final api = _api();
    refreshing = true;
    notifyListeners();
    try {
      if (organization == null) {
        final data = await api.call('organizations/');
        if (captured != _generation) return;
        organizations = records(data['organizations']);
        online = true;
        error = '';
        await _saveSession();
        return;
      }
      final currentScope = scope;
      await _loadOutbox();
      if (captured != _generation) return;
      await syncPending();
      if (captured != _generation) return;
      // An invited adjuster is isolated from maintenance, tickets and chat.
      // Fetch the scoped workspace instead of requesting forbidden resources.
      final profile = await api.call('me/');
      if (captured != _generation) return;
      if (profile is Map && profile['commissioning_only'] == true) {
        final projects = profile['can_commissioning'] == true
            ? await api.call('commissioning/')
            : <Json>[];
        if (profile['can_commissioning'] != true) {
          _resources = {};
          await _write(captured, 'resources_$currentScope', '{}');
        }
        if (captured != _generation) return;
        snapshot = {'me': profile, 'commissioning': projects};
        organizations = records(me['organizations']);
        online = true;
        error = '';
        syncedAt = DateTime.now().toUtc();
        await _write(
          captured,
          'cache_$currentScope',
          jsonEncode({'at': syncedAt!.toIso8601String(), 'snapshot': snapshot}),
        );
        await _acknowledgeCommissioning(currentScope);
        if (captured == _generation) await _loadOutbox();
        return;
      }
      const names = [
        'me',
        'tickets',
        'elevators',
        'buildings',
        'duty',
        'duty-swaps',
        'technicians',
        'maintenance',
        'parts',
        'chat',
      ];
      final from = orgNow
          .subtract(const Duration(days: 1))
          .toIso8601String()
          .substring(0, 10);
      final to = orgNow
          .add(const Duration(days: 45))
          .toIso8601String()
          .substring(0, 10);
      final values = await Future.wait(
        names.map(
          (name) => api.call(
            '$name/',
            query: name == 'duty' ? {'from': from, 'to': to} : null,
          ),
        ),
      );
      if (captured != _generation) return;
      final next = Map<String, dynamic>.fromIterables(names, values);
      next['tasks'] = next['me'] is Map && next['me']['field_ops_api'] == 1
          ? await api.call('tasks/')
          : <Json>[];
      if (captured != _generation) return;
      snapshot = next;
      if (me['can_commissioning'] == false &&
          _resources.keys.any((path) => path.startsWith('commissioning/'))) {
        _resources.removeWhere((path, _) => path.startsWith('commissioning/'));
        await _write(
          captured,
          'resources_$currentScope',
          jsonEncode(_resources),
        );
      }
      organizations = records(me['organizations']);
      online = true;
      error = '';
      syncedAt = DateTime.now().toUtc();
      final cache = jsonEncode({
        'at': syncedAt!.toIso8601String(),
        'snapshot': snapshot,
      });
      if (utf8.encode(cache).length < 2 * 1024 * 1024) {
        await _write(captured, 'cache_$currentScope', cache);
      }
      // Only remove confirmed receipts after reading the committed server list.
      final ids = records(snapshot['tickets']).map((t) => t['id']).toSet();
      for (final row in List<Json>.from(_outboxRows)) {
        if (row['state'] == 'synced' &&
            ids.contains(row['response']?['ticket']?['id'])) {
          await outbox.acknowledge(currentScope, row['id'] as String);
        }
      }
      final messageIds = records(snapshot['chat']).map((r) => r['id']).toSet();
      for (final row in List<Json>.from(_outboxRows)) {
        if (row['command']['type'] == 'media' &&
            row['state'] == 'synced' &&
            (row['response']?['message'] == null ||
                messageIds.contains(row['response']?['message']))) {
          final fileId = row['command']['body']['file_id'];
          await outbox.acknowledge(currentScope, row['id'] as String);
          if (!outbox.nativeSync && fileId is String) {
            await mediaFiles.remove(currentScope, fileId);
          }
        }
      }
      if (captured != _generation) return;
      await _acknowledgeCommissioning(currentScope);
      if (captured != _generation) return;
      await _loadOutbox();
      await _saveSession();
    } on ApiError catch (e) {
      if (captured != _generation) return;
      online = false;
      error = e.message;
      if (e.status == 401) {
        await _clearAccount();
        error = 'Сеанс завершён. Войдите заново.';
      }
      if (e.status == 403) {
        await _writes;
        if (organization != null) {
          await store.delete('cache_$scope');
          await store.delete('resources_$scope');
          await store.delete('drafts_$scope');
        }
        snapshot = {};
        _resources = {};
        _drafts = {};
        _outboxRows = [];
        _outboxReady = false;
        final unscoped = _api(unscoped: true);
        try {
          final data = await unscoped.call('organizations/');
          if (captured == _generation) {
            organizations = records(data['organizations']);
            organization = null;
            online = true;
            await _saveSession();
          }
        } catch (_) {
          /* Existing business data has already been removed. */
        } finally {
          unscoped.close();
        }
      }
    } catch (_) {
      if (captured == _generation) {
        online = false;
        error = 'Не удалось загрузить данные. Обновите экран.';
        log.record('snapshot_invalid');
      }
    } finally {
      api.close();
      if (captured == _generation) {
        refreshing = false;
        notifyListeners();
      }
    }
  }

  Future<void> _acknowledgeCommissioning(String key) async {
    var acknowledged = false;
    for (final row in List<Json>.from(_outboxRows)) {
      if (row['command']['type'] != 'commissioning' || row['state'] != 'synced')
        continue;
      await outbox.acknowledge(key, row['id'] as String);
      final file = row['file_id'];
      if (!outbox.nativeSync && file is String)
        await mediaFiles.remove(key, file);
      acknowledged = true;
    }
    if (acknowledged) syncRevision++;
  }

  Future<dynamic> request(
    String path, {
    String method = 'GET',
    Json? data,
    int? expectedOrganization,
  }) async {
    if (demo) {
      throw const ApiError(
        'Это демонстрация. Для работы подключитесь к серверу.',
      );
    }
    if (session == null ||
        (expectedOrganization != null &&
            expectedOrganization != organization)) {
      throw const ApiError('Организация изменилась. Откройте запись заново.');
    }
    if (method == 'POST' && path == 'chat/' && me['media_api'] == 1) {
      return queueMedia(
        'chat',
        const [],
        body: (data?['body'] as String?) ?? '',
        linkedTicket: data?['linked_ticket'] as int?,
        linkedPart: data?['linked_part'] as int?,
      );
    }
    if (method == 'POST' && path == 'tickets/') {
      return _recordTicket('create', data ?? {});
    }
    final ticketAction = RegExp(
      r'^tickets/(\d+)/(accept|complete|stop|resume|comment|cancel|assign)/$',
    ).firstMatch(path);
    if (method == 'POST' && ticketAction != null) {
      return _recordTicket(
        ticketAction.group(2)!,
        data ?? {},
        ticketId: int.parse(ticketAction.group(1)!),
      );
    }
    if (method != 'GET' && !online) {
      throw const ApiError('Сначала восстановите связь и обновите данные.');
    }
    final captured = _generation;
    final api = _api();
    try {
      final parsed = Uri.parse(path);
      if (parsed.hasScheme || parsed.host.isNotEmpty || parsed.hasFragment) {
        throw const ApiError('Некорректный путь запроса.');
      }
      final value = await api.call(
        parsed.path,
        method: method,
        data: data,
        query: parsed.hasQuery ? parsed.queryParameters : null,
      );
      if (captured != _generation) {
        throw const ApiError('Сеанс или организация изменились.');
      }
      return value;
    } on ApiError catch (e) {
      if (captured == _generation) {
        if (e.network) {
          online = false;
          error = e.message;
          notifyListeners();
        }
        if (e.status == 401) {
          await _clearAccount();
          error = e.message;
          notifyListeners();
        }
      }
      rethrow;
    } finally {
      api.close();
    }
  }

  Future<void> uploadCommissioningReceipt(
    int project,
    int expense,
    Uint8List bytes,
    String filename, {
    required int expectedOrganization,
  }) async {
    if (canRecordCommissioning) {
      return queueCommissioning(
        project,
        'receipt',
        {'expense': expense},
        receipt: bytes,
        expectedOrganization: expectedOrganization,
      );
    }
    if (!online || session == null || expectedOrganization != organization) {
      throw const ApiError('Нужна связь с сервером и действующий доступ.');
    }
    if (bytes.length > 10 * 1024 * 1024) {
      throw const ApiError('Чек не должен превышать 10 МБ.');
    }
    final client = _api();
    try {
      await client.uploadMedia(
        'commissioning/$project/expenses/$expense/upload/',
        [(bytes: bytes, name: filename)],
        field: 'receipt',
      );
      log.record('commissioning_receipt_uploaded');
    } catch (_) {
      log.record('commissioning_receipt_failed');
      rethrow;
    } finally {
      client.close();
    }
  }

  Future<dynamic> resource(
    String path, {
    required int expectedOrganization,
  }) async {
    if (!online &&
        session != null &&
        expectedOrganization == organization &&
        _resources.containsKey(path)) {
      return _resources[path];
    }
    if (demo) {
      if (path.startsWith('media/')) {
        return {'results': <Json>[], 'count': 0, 'next': null};
      }
      if (path.endsWith('history/')) {
        return {'events': <Json>[], 'comments': <Json>[]};
      }
      if (path.endsWith('documents/')) return <Json>[];
      return {};
    }
    try {
      final captured = _generation, key = scope;
      final result = await request(
        path,
        expectedOrganization: expectedOrganization,
      );
      if (captured == _generation) {
        _resources[path] = result;
        // Keep the text cache bounded. Photos and PDFs are not stored in this cache.
        var encoded = jsonEncode(_resources);
        if (utf8.encode(encoded).length > 2 * 1024 * 1024) {
          _resources = {path: result};
          encoded = jsonEncode(_resources);
        }
        await _write(captured, 'resources_$key', encoded);
      }
      return result;
    } on ApiError catch (e) {
      if (e.network &&
          expectedOrganization == organization &&
          _resources.containsKey(path)) {
        return _resources[path];
      }
      if (e.status == 403 || e.status == 404) {
        _resources.remove(path);
        if (session != null && organization == expectedOrganization) {
          await _write(_generation, 'resources_$scope', jsonEncode(_resources));
        }
      }
      rethrow;
    }
  }

  Future<void> uploadPhoto(
    int id,
    Uint8List bytes,
    String name,
    int expectedOrg,
  ) async {
    if (!canRecord || expectedOrg != organization) {
      throw const ApiError('Организация изменилась. Откройте заявку заново.');
    }
    if (bytes.isEmpty || bytes.length > 10 * 1024 * 1024) {
      throw const ApiError('Выберите фотографию до 10 МБ.');
    }
    await _recordTicket('photo', {
      'file': base64Encode(bytes),
      'name': name,
    }, ticketId: id);
  }

  Future<Uint8List> download(
    String path,
    int expectedOrg, {
    bool pdf = false,
    bool video = false,
  }) async {
    if (session == null ||
        expectedOrg != organization ||
        _switchingOrganization) {
      throw const ApiError('Организация изменилась. Откройте файл заново.');
    }
    final captured = _generation, key = scope, api = _api();
    final uri = api.resourceUri(path);
    final resource =
        '${uri.path}|${uri.query}|${pdf
            ? 'pdf'
            : video
            ? 'video'
            : 'image'}';
    final useCache = store is DeviceStore && !uri.path.endsWith('/receipt/');
    void checkScope() {
      if (captured != _generation) {
        throw const ApiError('Сеанс или организация изменились.');
      }
    }

    try {
      ({Uint8List bytes, String etag})? saved;
      if (useCache) {
        try {
          saved = await _downloadCache.read(key, resource);
        } catch (_) {
          log.record('download_cache_read_failed');
        }
      }
      checkScope();
      if (!canWrite) {
        if (saved != null) return saved.bytes;
        throw const ApiError('Файл ещё не загружен. Подключитесь к интернету.');
      }
      try {
        final file = await api.fetchDownload(
          path,
          pdf: pdf,
          video: video,
          etag: saved?.etag ?? '',
        );
        checkScope();
        if (file.bytes == null) return saved!.bytes;
        if (useCache) {
          try {
            await _downloadCache.write(
              key,
              resource,
              file.bytes!,
              file.etag,
              cacheLimit,
            );
          } catch (_) {
            log.record('download_cache_write_failed');
          }
        }
        checkScope();
        return file.bytes!;
      } on ApiError catch (e) {
        if (useCache && [401, 403, 404].contains(e.status)) {
          try {
            await _downloadCache.remove(key, resource);
          } catch (_) {
            log.record('download_cache_remove_failed');
          }
        }
        checkScope();
        if (e.network && saved != null) return saved.bytes;
        rethrow;
      }
    } finally {
      api.close();
    }
  }

  String draft(int ticket) => _drafts['$ticket'] as String? ?? '';

  Future<dynamic> uploadMedia(
    String route,
    List<({Uint8List bytes, String name})> files,
    int expectedOrg, {
    Map<String, String> fields = const {},
    String field = 'files',
  }) async {
    if (me['media_api'] == 1) {
      if (expectedOrg != organization) {
        throw const ApiError('Организация изменилась.');
      }
      if (route == 'chat/') {
        return queueMedia(
          'chat',
          files,
          body: fields['body'] ?? '',
          linkedTicket: int.tryParse(fields['linked_ticket'] ?? ''),
          linkedPart: int.tryParse(fields['linked_part'] ?? ''),
        );
      }
      final task = RegExp(r'^tasks/(\d+)/attachments/$').firstMatch(route);
      if (task != null) {
        return queueMedia('task', files, target: int.parse(task.group(1)!));
      }
    }
    if (!canWrite || organization != expectedOrg) {
      throw const ApiError(
        'Для отправки вложения нужна связь с выбранной организацией.',
      );
    }
    final captured = _generation, api = _api();
    try {
      final result = await api.uploadMedia(
        route,
        files,
        fields: fields,
        field: field,
      );
      if (captured != _generation) {
        throw const ApiError(
          'Организация или сеанс изменились. Обновите список.',
        );
      }
      return result;
    } finally {
      api.close();
    }
  }

  String taskDraft(int task) => _drafts['task:$task'] as String? ?? '';
  void setTaskDraft(int task, String value, int expectedOrg) =>
      _setDraft('task:$task', value, expectedOrg);
  void setDraft(int ticket, String value, int expectedOrg) =>
      _setDraft('$ticket', value, expectedOrg);
  void _setDraft(String key, String value, int expectedOrg) {
    if (demo || session == null || expectedOrg != organization) return;
    if (value.isEmpty) {
      _drafts.remove(key);
    } else {
      _drafts[key] = value;
    }
    _draftTimer?.cancel();
    _draftTimer = Timer(const Duration(milliseconds: 350), flushDrafts);
  }

  Future<void> flushDrafts() async {
    _draftTimer?.cancel();
    if (!demo &&
        !_switchingOrganization &&
        session != null &&
        organization != null) {
      await _write(_generation, 'drafts_$scope', jsonEncode(_drafts));
    }
  }

  Future<void> join(String code) async {
    await request(
      'organizations/join/',
      method: 'POST',
      data: {'code': code.trim()},
    );
    final data = await request('organizations/');
    organizations = records(data['organizations']);
    await selectOrganization(organizations.last['id'] as int);
  }

  Future<void> setDark(bool value) async {
    await setAppearance(value ? 'dark' : 'light');
  }

  Future<void> setAppearance(String value) async {
    if (!appearances.contains(value)) return;
    appearance = value;
    notifyListeners();
    await _write(_generation, 'theme', appearance);
  }

  Future<void> setCacheLimit(int value) async {
    if (!cacheLimits.contains(value)) return;
    cacheLimit = value;
    notifyListeners();
    await _write(_generation, 'download_cache_limit', '$value');
  }

  Future<void> clearDownloadedFiles() async {
    if (store is DeviceStore && session != null && organization != null) {
      await _downloadCache.clear(scope);
      log.record('download_cache_cleared');
    }
  }

  Future<void> _clearAccount() async {
    _stopSelfPush();
    try {
      await clearDownloadedFiles();
    } catch (_) {
      log.record('download_cache_clear_failed');
    }
    await outbox.clearAuth();
    try {
      await DeviceBridge.clearPrivateFiles();
    } catch (_) {
      /* Android only. */
    }
    try {
      await DeviceBridge.disablePush();
    } catch (_) {
      /* Native bridge is Android only. */
    }
    _generation++;
    _organizationSwitch++;
    _switchingOrganization = false;
    _draftTimer?.cancel();
    session = null;
    organization = null;
    organizations = [];
    snapshot = {};
    _resources = {};
    _drafts = {};
    _outboxRows = [];
    _outboxReady = false;
    _resolvedTickets.clear();
    online = false;
    refreshing = false;
    syncedAt = null;
    demo = false;
    updates = null;
    _checkedUpdates = null;
    _updatesFuture = null;
    await _writes;
    await log.flush();
    await store.clear();
    await store.write('server', serverText);
    await store.write('theme', appearance);
    await store.write('download_cache_limit', '$cacheLimit');
  }

  Future<bool> logout() async {
    var revoked = demo;
    if (!demo && session != null) {
      final api = _api(unscoped: true);
      try {
        await api.call('mobile/logout/', method: 'POST', data: {});
        revoked = true;
      } on ApiError catch (e) {
        revoked = e.status == 401;
      } finally {
        api.close();
      }
    }
    await _clearAccount();
    notifyListeners();
    return revoked;
  }

  Future<Json?> checkUpdates({bool force = false}) {
    if (!canWrite || organization == null) return Future.value(null);
    if (_updatesFuture != null) return _updatesFuture!;
    if (!force &&
        _checkedUpdates != null &&
        DateTime.now().difference(_checkedUpdates!) <
            const Duration(minutes: 1)) {
      return Future.value(updates);
    }
    final future = _readUpdates();
    _updatesFuture = future;
    return future.whenComplete(() {
      if (identical(_updatesFuture, future)) {
        _updatesFuture = null;
      }
    });
  }

  Future<Json?> _readUpdates() async {
    try {
      installedVersionCode =
          ((await DeviceBridge.status())['version_code'] as num?)?.toInt();
    } catch (_) {
      /* No platform bridge in browser/unit tests. */
    }
    if (!canWrite || organization == null) return null;
    final result = await request('updates/');
    if (result is! Map) {
      throw const ApiError('Не удалось проверить обновление.');
    }
    final data = Map<String, dynamic>.from(result);
    final release = data['android'];
    if (release != null &&
        (release is! Map ||
            release['version_code'] is! int ||
            (release['version_code'] as int) <= 0 ||
            release['version'] is! String ||
            release['size'] is! int ||
            (release['size'] as int) <= 0 ||
            (release['size'] as int) > 200 * 1024 * 1024 ||
            release['sha256'] is! String ||
            !RegExp(r'^[a-f0-9]{64}$').hasMatch(release['sha256'] as String))) {
      throw const ApiError('Описание обновления не прошло проверку.');
    }
    updates = data;
    _checkedUpdates = DateTime.now();
    updateCheckSerial++;
    log.record(hasUpdate ? 'update_available' : 'update_current');
    notifyListeners();
    return updates;
  }

  Future<void> installUpdate(Json release) async {
    if (!canWrite || organization == null) {
      throw const ApiError('Нужна связь с сервером.');
    }
    log.record('update_download_started');
    try {
      await DeviceBridge.installUpdate({
        ...release,
        'server': session!['server'],
        'token': session!['token'],
        'organization': organization,
      });
      log.record('update_installer_opened');
    } catch (_) {
      log.record('update_install_failed');
      rethrow;
    }
  }

  void _stopSelfPush() {
    _selfPushPoller?.stop();
    _selfPushPoller = null;
    _selfPushGeneration = null;
    _selfPushOrg = null;
  }

  void _startSelfPush() {
    if (!canWrite || organization == null || session == null) return;
    // Only start when the currently selected organization explicitly supports
    // this transport. Legacy servers and restricted adjusters must not poll.
    if (me['self_push_api'] != 1) return;
    final user = session!['user'];
    final token = session!['token'];
    final server = session!['server'];
    if (user is! Map ||
        user['id'] is! int ||
        token is! String ||
        server is! String) {
      return;
    }
    late final Uri endpoint;
    try {
      endpoint = parseServer(server);
    } catch (_) {
      log.record('self_push_invalid_server');
      return;
    }
    final scopeOrg = organization!;
    final generation = _generation;
    if (_selfPushPoller != null &&
        !_selfPushPoller!.stopped &&
        _selfPushGeneration == generation &&
        _selfPushOrg == scopeOrg) {
      return;
    }
    _stopSelfPush();
    late final SelfPushPoller poller;
    poller = SelfPushPoller(
      server: endpoint,
      token: token,
      userId: user['id'] as int,
      organization: scopeOrg,
      store: store,
      log: log,
      transport: transport,
      isCurrent: () =>
          identical(_selfPushPoller, poller) &&
          generation == _generation &&
          organization == scopeOrg &&
          session?['token'] == token,
    );
    _selfPushPoller = poller;
    _selfPushGeneration = generation;
    _selfPushOrg = scopeOrg;
    poller.start();
  }

  Future<void> enablePush({bool prompt = true}) async {
    if (_pushSyncing || !canWrite || organization == null) return;
    _pushSyncing = true;
    final captured = _generation;
    try {
      final response = Map<String, dynamic>.from(await request('mobile/push/'));
      if (response['enabled'] != true || response['config'] is! Map) {
        throw const ApiError(
          'На сервере ещё не настроена доставка FCM. Администратору нужно подключить Firebase.',
        );
      }
      if (prompt) await _write(captured, 'push_permission_prompted', 'true');
      if (captured != _generation) return;
      final token = await DeviceBridge.registerPush({
        ...Map<String, dynamic>.from(response['config']),
        'prompt': prompt,
      });
      if (captured != _generation || token == null) return;
      await request('mobile/push/', method: 'POST', data: {'token': token});
      if (captured != _generation) return;
      await DeviceBridge.activatePush();
      await _write(captured, 'push_enabled', 'true');
      log.record('push_registered');
    } finally {
      _pushSyncing = false;
    }
  }

  Future<void> disablePush() async {
    await DeviceBridge.disablePush();
    await _write(_generation, 'push_enabled', 'false');
    if (canWrite) await request('mobile/push/', method: 'DELETE');
    log.record('push_disabled');
  }

  Future<void> syncDevice({bool forceUpdates = false}) async {
    // A stale background sync must never start fresh HTTP requests after the
    // employee switches organizations or the account changes.
    final selectedGeneration = _generation;
    final selectedOrg = organization;
    bool stillSelected() =>
        canWrite &&
        !_switchingOrganization &&
        !refreshing &&
        organization == selectedOrg &&
        selectedGeneration == _generation;
    if (!stillSelected()) return;
    _startSelfPush();
    try {
      installedVersionCode =
          ((await DeviceBridge.status())['version_code'] as num?)?.toInt();
    } catch (_) {
      /* Platform bridge unavailable. */
    }
    if (!stillSelected()) return;
    // Checking the APK catalogue has no Firebase or notification dependency.
    if (forceUpdates) {
      unawaited(
        checkUpdates(force: true).then<void>(
          (_) {},
          onError: (Object _) {
            log.record('update_check_failed');
          },
        ),
      );
    }
    try {
      final key = 'push_permission_prompted';
      final prompt = await store.read(key) != 'true';
      if (!stillSelected()) return;
      await enablePush(prompt: prompt);
    } catch (_) {
      log.record('push_sync_failed');
    }
    if (!stillSelected() || forceUpdates) return;
    try {
      await checkUpdates(force: forceUpdates);
    } catch (_) {
      log.record('update_check_failed');
    }
  }

  @override
  void dispose() {
    _stopSelfPush();
    _generation++;
    _draftTimer?.cancel();
    super.dispose();
  }
}
