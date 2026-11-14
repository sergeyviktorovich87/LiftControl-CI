import 'dart:async';

import 'package:flutter/material.dart';

import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';
import 'tickets.dart';
import 'journal.dart';
import 'platform_bridge.dart';
import 'staff.dart';
import 'media_library.dart';

String _searchable(String value) =>
    value.toLowerCase().replaceAll(RegExp(r'[^а-яёa-z0-9]'), '');
void openElevator(BuildContext context, LiftController c, Json e) =>
    Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) => ElevatorPage(
          controller: c,
          id: e['id'] as int,
          organization: c.organization!,
        ),
      ),
    );

class ElevatorsPage extends StatefulWidget {
  const ElevatorsPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<ElevatorsPage> createState() => _ElevatorsPageState();
}

class _ElevatorsPageState extends State<ElevatorsPage> {
  String _search = '';
  bool _mine = false, _archive = false;
  @override
  Widget build(BuildContext context) {
    final c = widget.controller, query = _searchable(_search);
    final elevators = c
        .list('elevators')
        .where(
          (e) =>
              (_archive || e['active'] == true) &&
              (!_mine || e['assigned_technician'] == c.me['technician']) &&
              _searchable('${e['address']} ${liftLabel(e)}').contains(query),
        )
        .toList();
    final buildingIds = elevators.map((e) => e['building']).toSet();
    final buildings = c
        .list('buildings')
        .where((b) => buildingIds.contains(b['id']))
        .toList();
    final streets = <String, List<Json>>{};
    for (final b in buildings) {
      final street = text(b, 'street', 'Без указанной улицы');
      (streets[street] ??= []).add(b);
    }
    return RefreshIndicator(
      onRefresh: c.refresh,
      child: ListView(
        padding: const EdgeInsets.all(18),
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          const SectionTitle('Объекты и лифты'),
          TextField(
            decoration: const InputDecoration(
              hintText: 'Улица, дом или номер лифта',
              prefixIcon: Icon(Icons.search),
            ),
            onChanged: (v) => setState(() => _search = v),
          ),
          const SizedBox(height: 12),
          FilterChip(
            label: const Text('Показывать архив'),
            selected: _archive,
            onSelected: (v) => setState(() => _archive = v),
          ),
          if (c.me['technician'] != null)
            Align(
              alignment: Alignment.centerLeft,
              child: FilterChip(
                label: const Text('Закреплённые за мной'),
                selected: _mine,
                onSelected: (v) => setState(() => _mine = v),
              ),
            ),
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 12),
            child: Text(
              '${buildings.length} объектов · ${elevators.length} лифтов',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          if (streets.isEmpty)
            const Sheet(
              child: EmptyState(
                'Объектов нет',
                'Проверьте выбранную организацию или измените поиск.',
                icon: Icons.elevator_outlined,
              ),
            ),
          for (final entry in streets.entries)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Card(
                child: ExpansionTile(
                  key: ValueKey(
                    '${c.organization}_${entry.key}_${query.isNotEmpty}',
                  ),
                  initiallyExpanded: query.isNotEmpty,
                  tilePadding: const EdgeInsets.symmetric(
                    horizontal: 18,
                    vertical: 6,
                  ),
                  shape: const Border(),
                  collapsedShape: const Border(),
                  leading: Icon(
                    Icons.location_city_outlined,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                  title: Text(
                    entry.key,
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  subtitle: Text('Объектов: ${entry.value.length}'),
                  children: [
                    for (final b in entry.value)
                      ListTile(
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 22,
                          vertical: 5,
                        ),
                        title: Text(text(b, 'address')),
                        subtitle: Text(
                          'Лифтов: ${elevators.where((e) => e['building'] == b['id']).length}',
                        ),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute<void>(
                            builder: (_) => BuildingPage(
                              controller: c,
                              id: b['id'] as int,
                              organization: c.organization!,
                              showArchived: _archive,
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class BuildingPage extends StatelessWidget {
  const BuildingPage({
    this.showArchived = false,
    super.key,
    required this.controller,
    required this.id,
    required this.organization,
  });
  final LiftController controller;
  final int id, organization;
  final bool showArchived;
  @override
  Widget build(BuildContext context) => ScopePage(
    controller: controller,
    organization: organization,
    builder: (context) {
      final b = controller
              .list('buildings')
              .firstWhere((b) => b['id'] == id, orElse: () => {}),
          lifts = controller
              .list('elevators')
              .where(
                (e) =>
                    e['building'] == id &&
                    (showArchived || e['active'] == true),
              )
              .toList();
      return Scaffold(
        appBar: AppBar(title: const Text('Объект')),
        body: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            Sheet(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    text(b, 'address'),
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(text(b, 'name')),
                  if (text(b, 'management_company', '').isNotEmpty) ...[
                    const Divider(),
                    Text(text(b, 'management_company')),
                  ],
                  if (text(b, 'contact_name', '').isNotEmpty) ...[
                    const SizedBox(height: 8),
                    SelectableText(
                      '${text(b, 'contact_name')} · ${text(b, 'contact_phone')}',
                    ),
                  ],
                ],
              ),
            ),
            SectionTitle('Лифты · ${lifts.length}'),
            for (final e in lifts)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: LiftTile(
                  elevator: e,
                  onTap: () => openElevator(context, controller, e),
                ),
              ),
          ],
        ),
      );
    },
  );
}

class LiftTile extends StatelessWidget {
  const LiftTile({super.key, required this.elevator, required this.onTap});
  final Json elevator;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Card(
    child: ListTile(
      contentPadding: const EdgeInsets.all(16),
      leading: Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Icon(
          Icons.elevator_outlined,
          color: Theme.of(context).colorScheme.primary,
        ),
      ),
      title: Text(
        '${liftLabel(elevator)}${elevator['stopped_at'] != null
            ? ' · Лифт остановлен'
            : elevator['active'] == false
            ? ' · Архив'
            : ''}',
        style: const TextStyle(fontWeight: FontWeight.w700),
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Text(
          '${text(elevator, 'entrance', 'Подъезд не указан')}${text(elevator, 'model', '').isNotEmpty ? ' · ${elevator['model']}' : ''}\n${text(elevator, 'technician_name', 'Механик не назначен')}',
        ),
      ),
      isThreeLine: true,
      trailing: const Icon(Icons.chevron_right),
      onTap: onTap,
    ),
  );
}

class ElevatorPage extends StatefulWidget {
  const ElevatorPage({
    super.key,
    required this.controller,
    required this.id,
    required this.organization,
  });
  final LiftController controller;
  final int id, organization;
  @override
  State<ElevatorPage> createState() => _ElevatorPageState();
}

class _ElevatorPageState extends State<ElevatorPage> {
  List<Json> _documents = [];
  String _error = '';
  bool _loading = true, _opening = false;
  @override
  void initState() {
    super.initState();
    unawaited(_loadDocuments());
  }

  Future<void> _loadDocuments() async {
    try {
      final data = await widget.controller.resource(
        'elevators/${widget.id}/documents/',
        expectedOrganization: widget.organization,
      );
      if (mounted) {
        setState(() {
          _documents = records(data);
          _error = '';
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _openDocument(Json d) async {
    setState(() => _opening = true);
    try {
      final bytes = await widget.controller.download(
        d['url'],
        widget.organization,
        pdf: true,
      );
      await PlatformBridge.openPdf(bytes);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _opening = false);
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: widget.controller,
    organization: widget.organization,
    builder: (context) {
      final c = widget.controller,
          e = c
              .list('elevators')
              .firstWhere((e) => e['id'] == widget.id, orElse: () => {});
      final tickets =
          c.list('tickets').where((t) => t['elevator'] == widget.id).toList()
            ..sort((a, b) => (b['id'] as int).compareTo(a['id'] as int));
      final maintenance = c
          .list('maintenance')
          .where((m) => m['elevator'] == widget.id)
          .toList();
      return DefaultTabController(
        length: 4,
        child: Scaffold(
          appBar: AppBar(
            title: Text('Лифт ${liftLabel(e)}'),
            actions: [
              IconButton(
                tooltip: 'Фото и видео лифта',
                icon: const Icon(Icons.photo_library_outlined),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) =>
                        MediaPage(controller: c, elevator: widget.id),
                  ),
                ),
              ),
              if (c.me['can_manage_access'] == true && e['active'] == true)
                IconButton(
                  tooltip: 'Удалить лифт из списка',
                  icon: const Icon(Icons.archive_outlined),
                  onPressed: c.canWrite
                      ? () async {
                          final saved = await archiveRecord(
                            context,
                            c,
                            'elevators',
                            e,
                            widget.organization,
                          );
                          if (saved && context.mounted) Navigator.pop(context);
                        }
                      : null,
                ),
            ],
            bottom: const TabBar(
              isScrollable: true,
              tabAlignment: TabAlignment.start,
              tabs: [
                Tab(text: 'Карточка'),
                Tab(text: 'Заявки'),
                Tab(text: 'Журнал ТО'),
                Tab(text: 'Документация'),
              ],
            ),
          ),
          body: TabBarView(
            children: [
              ListView(
                padding: const EdgeInsets.all(18),
                children: [
                  Sheet(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          text(e, 'address'),
                          style: Theme.of(context).textTheme.titleLarge
                              ?.copyWith(fontWeight: FontWeight.w700),
                        ),
                        const Divider(),
                        if (e['stopped_at'] != null) ...[
                          const StatusPill('STOPPED'),
                          const SizedBox(height: 8),
                          _info('Причина остановки', text(e, 'stop_reason')),
                          const Divider(),
                        ],
                        _info('Внутренний номер', text(e, 'inventory_number')),
                        _info(
                          'Заводской номер',
                          text(e, 'factory_number', 'Не указан'),
                        ),
                        _info('Подъезд', text(e, 'entrance', 'Не указан')),
                        _info(
                          'Производитель',
                          text(e, 'manufacturer', 'Не указан'),
                        ),
                        _info('Модель', text(e, 'model', 'Не указана')),
                        _info(
                          'Остановок лифта',
                          text(e, 'floors', 'Не указано'),
                        ),
                        _info(
                          'Этажность здания',
                          text(e, 'building_floors', 'Не указана'),
                        ),
                        _info(
                          'Ответственный',
                          text(e, 'technician_name', 'Не назначен'),
                        ),
                        if (text(e, 'notes', '').isNotEmpty) ...[
                          const Divider(),
                          SelectableText(text(e, 'notes')),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                  if (c.me['can_create_ticket'] == true)
                    FilledButton.icon(
                      onPressed: c.canCreateTicket
                          ? () => Navigator.push(
                              context,
                              MaterialPageRoute<void>(
                                builder: (_) => CreateTicketPage(
                                  controller: c,
                                  elevator: e,
                                ),
                              ),
                            )
                          : null,
                      icon: const Icon(Icons.add),
                      label: const Text('Создать заявку на этот лифт'),
                    ),
                ],
              ),
              ListView(
                padding: const EdgeInsets.all(18),
                children: [
                  if (tickets.isEmpty)
                    const EmptyState(
                      'Заявок пока нет',
                      'Вся история обращений по этому лифту будет здесь.',
                    ),
                  for (final t in tickets)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: TicketTile(
                        t,
                        controller: c,
                        onTap: () => openTicket(context, c, t['id'] as int),
                      ),
                    ),
                ],
              ),
              ListView(
                padding: const EdgeInsets.all(18),
                children: [
                  FilledButton.icon(
                    onPressed: c.canWrite
                        ? () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) =>
                                  MaintenanceForm(controller: c, elevator: e),
                            ),
                          )
                        : null,
                    icon: const Icon(Icons.add),
                    label: const Text('Добавить запись в журнал'),
                  ),
                  const SizedBox(height: 16),
                  if (maintenance.isEmpty)
                    const EmptyState(
                      'Журнал пока пуст',
                      'Здесь сохраняются ТО, осмотры и ремонт этого лифта.',
                    ),
                  for (final m in maintenance)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: MaintenanceTile(controller: c, item: m),
                    ),
                ],
              ),
              ListView(
                padding: const EdgeInsets.all(18),
                children: [
                  const Text(
                    'Документы автоматически подобраны из общей библиотеки по производителю и модели.',
                  ),
                  const SizedBox(height: 14),
                  if (_loading)
                    const Center(child: CircularProgressIndicator()),
                  if (_error.isNotEmpty) ...[
                    Text(_error),
                    TextButton(
                      onPressed: _loadDocuments,
                      child: const Text('Повторить'),
                    ),
                  ],
                  if (!_loading && _documents.isEmpty)
                    const EmptyState(
                      'Документов пока нет',
                      'В библиотеке ещё нет подходящей документации.',
                      icon: Icons.folder_open_outlined,
                    ),
                  for (final d in _documents)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Card(
                        child: ListTile(
                          leading: const Icon(Icons.picture_as_pdf_outlined),
                          title: Text(text(d, 'title')),
                          subtitle: Text(
                            '${text(d, 'manufacturer', '')} ${text(d, 'model', '')}',
                          ),
                          trailing: const Icon(Icons.open_in_new),
                          onTap: _opening ? null : () => _openDocument(d),
                        ),
                      ),
                    ),
                  if (_opening) const LinearProgressIndicator(),
                ],
              ),
            ],
          ),
        ),
      );
    },
  );
  Widget _info(String label, String value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 7),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(child: Text(label)),
        const SizedBox(width: 16),
        Expanded(
          child: Text(
            value,
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
        ),
      ],
    ),
  );
}
