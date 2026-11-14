import 'package:flutter/material.dart';
import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';
import 'tickets.dart';
import 'platform_bridge.dart';

const maintenanceNames = {
  'TO1': 'ТО-1',
  'TO2': 'ТО-2',
  'REPAIR': 'Ремонт',
  'INSPECTION': 'Осмотр',
};

class MaintenancePage extends StatelessWidget {
  const MaintenancePage({super.key, required this.controller});
  final LiftController controller;
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Журнал ТО'),
      actions: [
        IconButton(
          tooltip: 'Сформировать PDF журнала ТО',
          onPressed: controller.canWrite ? () async {
            final lift = await pickElevator(context, controller);
            if (lift == null || !context.mounted) return;
            try {
              final bytes = await controller.download(
                'maintenance/${lift['id']}/journal/pdf/',
                controller.organization!,
                pdf: true,
              );
              await PlatformBridge.openPdf(bytes);
              controller.log.record('maintenance_pdf_opened');
            } catch (e) {
              controller.log.record('maintenance_pdf_failed');
              if (context.mounted) showError(context, e);
            }
          } : null,
          icon: const Icon(Icons.picture_as_pdf_outlined),
        ),
        IconButton(
          tooltip: 'Добавить запись',
          onPressed: controller.canWrite
              ? () => Navigator.push(
                  context,
                  MaterialPageRoute<void>(
                    builder: (_) => MaintenanceForm(controller: controller),
                  ),
                )
              : null,
          icon: const Icon(Icons.add),
        ),
      ],
    ),
    body: ListenableBuilder(
      listenable: controller,
      builder: (context, _) => RefreshIndicator(
        onRefresh: controller.refresh,
        child: ListView(
          padding: const EdgeInsets.all(18),
          physics: const AlwaysScrollableScrollPhysics(),
          children: [
            if (controller.list('maintenance').isEmpty)
              const EmptyState(
                'Записей ТО пока нет',
                'Добавьте осмотр, плановое обслуживание или результат ремонта.',
              ),
            for (final m in controller.list('maintenance'))
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: MaintenanceTile(controller: controller, item: m),
              ),
          ],
        ),
      ),
    ),
  );
}

class MaintenanceTile extends StatelessWidget {
  const MaintenanceTile({
    super.key,
    required this.controller,
    required this.item,
  });
  final LiftController controller;
  final Json item;
  @override
  Widget build(BuildContext context) {
    final completed = item['completed_at'] != null;
    return Card(
      child: ListTile(
        contentPadding: const EdgeInsets.all(16),
        leading: Icon(
          completed ? Icons.task_alt : Icons.build_outlined,
          color: completed ? green : Theme.of(context).colorScheme.primary,
        ),
        title: Text(
          '${maintenanceNames[item['kind']] ?? item['kind']} · ${dateLabel(item['planned_date'], 0, time: false)}',
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 7),
          child: Text(
            '${text(item, 'address')}\nЛифт ${text(item, 'elevator_label')} · ${completed ? 'Выполнено' : 'Запланировано'}',
          ),
        ),
        isThreeLine: true,
        trailing: const Icon(Icons.chevron_right),
        onTap: () => Navigator.push(
          context,
          MaterialPageRoute<void>(
            builder: (_) => MaintenanceForm(controller: controller, item: item),
          ),
        ),
      ),
    );
  }
}

class MaintenanceForm extends StatefulWidget {
  const MaintenanceForm({
    super.key,
    required this.controller,
    this.item,
    this.elevator,
  });
  final LiftController controller;
  final Json? item, elevator;
  @override
  State<MaintenanceForm> createState() => _MaintenanceFormState();
}

class _MaintenanceFormState extends State<MaintenanceForm> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _notes;
  late int _org;
  Json? _elevator;
  String _kind = 'TO1';
  late DateTime _date;
  bool _completed = false, _busy = false;
  bool get editable =>
      widget.item == null ||
      widget.controller.me['can_manage'] == true ||
      widget.item!['technician'] == widget.controller.me['technician'];
  @override
  void initState() {
    super.initState();
    _org = widget.controller.organization!;
    _elevator = widget.elevator;
    _notes = TextEditingController(text: widget.item?['notes'] ?? '');
    _kind = widget.item?['kind'] ?? 'TO1';
    _date =
        DateTime.tryParse('${widget.item?['planned_date']}') ??
        widget.controller.orgNow;
    _completed = widget.item?['completed_at'] != null;
  }

  @override
  void dispose() {
    _notes.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_busy || !_form.currentState!.validate()) return;
    if (widget.item == null && _elevator == null) {
      showError(context, const ApiError('Выберите лифт.'));
      return;
    }
    setState(() => _busy = true);
    final c = widget.controller;
    try {
      final data = <String, dynamic>{
        'notes': _notes.text.trim(),
        'completed_at': _completed
            ? (widget.item?['completed_at'] ??
                  DateTime.now().toUtc().toIso8601String())
            : null,
      };
      if (widget.item == null) {
        data.addAll({
          'elevator': _elevator!['id'],
          'kind': _kind,
          'planned_date': _date.toIso8601String().substring(0, 10),
        });
      }
      await c.request(
        widget.item == null
            ? 'maintenance/'
            : 'maintenance/${widget.item!['id']}/',
        method: widget.item == null ? 'POST' : 'PATCH',
        data: data,
        expectedOrganization: _org,
      );
      await c.refresh();
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ScopePage(
    controller: widget.controller,
    organization: _org,
    builder: (context) => Scaffold(
      appBar: AppBar(
        title: Text(
          widget.item == null
              ? 'Запись в журнал ТО'
              : 'Работа ${maintenanceNames[_kind] ?? _kind}',
        ),
      ),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            Sheet(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (widget.item != null)
                    Text(
                      '${text(widget.item!, 'address')} · ${text(widget.item!, 'elevator_label')}',
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                  if (widget.item == null) ...[
                    OutlinedButton.icon(
                      onPressed: _busy
                          ? null
                          : () async {
                              final e = await pickElevator(
                                context,
                                widget.controller,
                              );
                              if (e != null && mounted) {
                                setState(() => _elevator = e);
                              }
                            },
                      icon: const Icon(Icons.elevator_outlined),
                      label: Text(
                        _elevator == null
                            ? 'Выбрать лифт'
                            : '${text(_elevator!, 'address')} · ${liftLabel(_elevator!)}',
                      ),
                    ),
                    const SizedBox(height: 14),
                    DropdownButtonFormField<String>(
                      initialValue: _kind,
                      decoration: const InputDecoration(
                        labelText: 'Вид работы',
                      ),
                      items: maintenanceNames.entries
                          .map(
                            (e) => DropdownMenuItem(
                              value: e.key,
                              child: Text(e.value),
                            ),
                          )
                          .toList(),
                      onChanged: _busy
                          ? null
                          : (v) => setState(() => _kind = v!),
                    ),
                    const SizedBox(height: 12),
                    OutlinedButton(
                      onPressed: _busy
                          ? null
                          : () async {
                              final d = await showDatePicker(
                                context: context,
                                initialDate: _date,
                                firstDate: DateTime(2020),
                                lastDate: DateTime(2040),
                              );
                              if (d != null) setState(() => _date = d);
                            },
                      child: Text(
                        'Дата: ${dateLabel(_date.toIso8601String().substring(0, 10), 0, time: false)} ${_date.year}',
                      ),
                    ),
                  ],
                  const SizedBox(height: 16),
                  TextFormField(
                    controller: _notes,
                    readOnly: !editable,
                    decoration: const InputDecoration(
                      labelText: 'Результат и замечания',
                    ),
                    maxLength: 4000,
                    minLines: 4,
                    maxLines: 10,
                    validator: (v) => _completed && (v?.trim().isEmpty ?? true)
                        ? 'Для выполненной работы нужен результат'
                        : null,
                  ),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Работа выполнена'),
                    value: _completed,
                    onChanged: editable && !_busy
                        ? (v) => setState(() => _completed = v)
                        : null,
                  ),
                  if (!editable)
                    const Text(
                      'Изменить запись может её исполнитель или руководитель.',
                    ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            if (editable)
              FilledButton(
                onPressed: widget.controller.canWrite && !_busy ? _save : null,
                child: Text(_busy ? 'Сохранение…' : 'Сохранить запись'),
              ),
          ],
        ),
      ),
    ),
  );
}
