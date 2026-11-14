import 'package:flutter/material.dart';
import '../core/api.dart';
import '../core/controller.dart';
import 'common.dart';

class DutyCard extends StatefulWidget {
  const DutyCard({super.key, required this.controller});
  final LiftController controller;
  @override
  State<DutyCard> createState() => _DutyCardState();
}

class _DutyCardState extends State<DutyCard> {
  bool _busy = false;
  @override
  Widget build(BuildContext context) {
    final c = widget.controller,
        shift = c.me['duty_shift'] is Map
            ? Map<String, dynamic>.from(c.me['duty_shift'])
            : <String, dynamic>{};
    final own = c.me['on_duty'] == true;
    return Sheet(
      color: forest,
      child: DefaultTextStyle(
        style: const TextStyle(color: Colors.white, fontSize: 14, height: 1.4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.shield_outlined, color: Color(0xFFC9E3D0)),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    own ? 'Вы на дежурстве' : 'Дежурный механик',
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              shift.isEmpty
                  ? 'Сейчас дежурный не назначен'
                  : text(shift, 'technician_name'),
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            ),
            if (shift.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                '${dateLabel(shift['start'], c.utcOffset)} — ${dateLabel(shift['end'], c.utcOffset)}',
                style: const TextStyle(color: Color(0xFFC2D4CA)),
              ),
            ],
            const SizedBox(height: 10),
            Text(
              own
                  ? 'Вы можете создавать заявки до конца смены.'
                  : 'Права дежурного включаются по графику организации.',
              style: const TextStyle(fontSize: 12, color: Color(0xFFC2D4CA)),
            ),
            if (own && shift['acknowledged_at'] == null) ...[
              const SizedBox(height: 14),
              FilledButton(
                onPressed: c.canWrite && !_busy
                    ? () async {
                        setState(() => _busy = true);
                        try {
                          await c.request(
                            'duty/${shift['id']}/acknowledge/',
                            method: 'POST',
                            data: {},
                            expectedOrganization: c.organization,
                          );
                          await c.refresh();
                        } catch (e) {
                          if (context.mounted) showError(context, e);
                        } finally {
                          if (mounted) setState(() => _busy = false);
                        }
                      }
                    : null,
                style: FilledButton.styleFrom(
                  backgroundColor: const Color(0xFFDCECDC),
                  foregroundColor: forest,
                ),
                child: Text(_busy ? 'Подтверждение…' : 'Принял смену'),
              ),
            ],
            if (own && shift['acknowledged_at'] != null)
              const Padding(
                padding: EdgeInsets.only(top: 10),
                child: Text(
                  '✓ Приём смены подтверждён',
                  style: TextStyle(color: Color(0xFFB7E3C6), fontSize: 12),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class DutyPage extends StatefulWidget {
  const DutyPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<DutyPage> createState() => _DutyPageState();
}

class _DutyPageState extends State<DutyPage> {
  bool _busy = false;

  Future<void> _reassign(Json shift) async {
    final c = widget.controller;
    final organization = c.organization;
    if (organization == null) return;
    final target = await showModalBottomSheet<int>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 20),
              child: SectionTitle('Изменить будущего дежурного'),
            ),
            for (final technician in c.list('technicians').where((t) => t['active'] == true))
              ListTile(
                leading: const Icon(Icons.person_outline),
                title: Text(text(technician, 'full_name')),
                trailing: technician['id'] == shift['technician'] ? const Icon(Icons.check) : null,
                onTap: () => Navigator.pop(context, technician['id'] as int),
              ),
          ],
        ),
      ),
    );
    if (target == null || target == shift['technician'] || !mounted) return;
    setState(() => _busy = true);
    try {
      await c.request(
        'duty/${shift['id']}/',
        method: 'PATCH',
        data: {'technician': target},
        expectedOrganization: organization,
      );
      await c.refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Дежурный изменён')),
        );
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resolve(Json swap, bool accept) async {
    setState(() => _busy = true);
    try {
      await widget.controller.request(
        'duty-swaps/${swap['id']}/${accept ? 'accept' : 'cancel'}/',
        method: 'POST',
        data: {},
        expectedOrganization: widget.controller.organization,
      );
      await widget.controller.refresh();
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _swap(Json shift) async {
    final c = widget.controller, org = c.organization!;
    final target = await showModalBottomSheet<int>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 20),
              child: SectionTitle('Кто подменит?'),
            ),
            for (final t
                in c
                    .list('technicians')
                    .where(
                      (t) =>
                          t['active'] == true && t['id'] != shift['technician'],
                    ))
              ListTile(
                leading: const Icon(Icons.person_outline),
                title: Text(text(t, 'full_name')),
                onTap: () => Navigator.pop(context, t['id'] as int),
              ),
          ],
        ),
      ),
    );
    if (target == null || !mounted) return;
    final reason = await askText(
      context,
      title: 'Запросить подмену',
      label: 'Причина',
      minimum: 5,
      maximum: 1000,
      action: 'Отправить запрос',
    );
    if (reason == null) return;
    setState(() => _busy = true);
    try {
      await c.request(
        'duty/${shift['id']}/swap/',
        method: 'POST',
        data: {'technician': target, 'reason': reason},
        expectedOrganization: org,
      );
      await c.refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Запрос отправлен. Смена перейдёт после подтверждения сменщика.',
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller,
        rows = c.list('duty')
          ..sort((a, b) => '${a['start']}'.compareTo('${b['start']}'));
    final swaps = c.list('duty-swaps').where((s) => s['status'] == 'PENDING');
    return RefreshIndicator(
      onRefresh: c.refresh,
      child: ListView(
        padding: const EdgeInsets.all(18),
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          SectionTitle(
            'Дежурства',
            trailing: c.me['can_manage'] == true
                ? IconButton(
                    tooltip: 'Составить график',
                    onPressed: c.canWrite
                        ? () => Navigator.push(
                            context,
                            MaterialPageRoute<void>(
                              builder: (_) => PlanDutyPage(controller: c),
                            ),
                          )
                        : null,
                    icon: const Icon(Icons.edit_calendar_outlined),
                  )
                : null,
          ),
          DutyCard(controller: c),
          const SizedBox(height: 18),
          if (swaps.isNotEmpty) const SectionTitle('Запросы подмены'),
          for (final swap in swaps)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Sheet(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${text(swap, 'source_name')} → ${text(swap, 'target_name')}',
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 6),
                    Text(text(swap, 'reason')),
                    Text(dateLabel(swap['start'], c.utcOffset)),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        if (swap['target'] == c.me['technician'])
                          FilledButton(
                            onPressed: c.canWrite && !_busy
                                ? () => _resolve(swap, true)
                                : null,
                            child: const Text('Подтвердить подмену'),
                          ),
                        if (swap['target'] == c.me['technician'] ||
                            swap['source'] == c.me['technician'] ||
                            c.me['can_manage'] == true)
                          OutlinedButton(
                            onPressed: c.canWrite && !_busy
                                ? () => _resolve(swap, false)
                                : null,
                            child: const Text('Отменить запрос'),
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          const SectionTitle('Ближайшие смены'),
          Text(
            'Время организации · ${text(c.me, 'timezone', 'по графику')}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          if (rows.isEmpty)
            const Sheet(
              child: EmptyState(
                'График пока пуст',
                'Руководитель назначает дежурных на день, месяц или другой период.',
                icon: Icons.calendar_month_outlined,
              ),
            ),
          for (final shift in rows)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Sheet(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(
                          Icons.calendar_today_outlined,
                          size: 20,
                          color: Theme.of(context).colorScheme.primary,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            text(shift, 'technician_name'),
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                        ),
                        if (shift['technician'] == c.me['technician'])
                          const Text(
                            'Ваша смена',
                            style: TextStyle(fontSize: 11),
                          ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    Text(
                      '${dateLabel(shift['start'], c.utcOffset)} — ${dateLabel(shift['end'], c.utcOffset)}',
                    ),
                    if (c.me['can_manage'] == true &&
                        DateTime.tryParse('${shift['start']}')?.isAfter(DateTime.now().toUtc()) == true) ...[
                      const SizedBox(height: 8),
                      OutlinedButton.icon(
                        onPressed: c.canWrite && !_busy ? () => _reassign(shift) : null,
                        icon: const Icon(Icons.edit_outlined),
                        label: const Text('Изменить дежурного'),
                      ),
                    ],
                    if (c.me['role'] != 'ADMIN' &&
                        (shift['technician'] == c.me['technician'] ||
                            c.me['can_manage'] == true) &&
                        DateTime.tryParse(
                              '${shift['end']}',
                            )?.isAfter(DateTime.now().toUtc()) ==
                            true) ...[
                      const SizedBox(height: 8),
                      TextButton.icon(
                        onPressed: c.canWrite && !_busy
                            ? () => _swap(shift)
                            : null,
                        icon: const Icon(Icons.swap_horiz),
                        label: const Text('Запросить подмену'),
                      ),
                    ],
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class PlanDutyPage extends StatefulWidget {
  const PlanDutyPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<PlanDutyPage> createState() => _PlanDutyPageState();
}

class _PlanDutyPageState extends State<PlanDutyPage> {
  late DateTime _from, _to;
  late int _org;
  final List<int> _technicians = [];
  Json? _preview;
  bool _busy = false;
  bool _replace = false;
  @override
  void initState() {
    super.initState();
    final now = widget.controller.orgNow;
    _org = widget.controller.organization!;
    _from = DateTime(now.year, now.month + 1);
    _to = DateTime(now.year, now.month + 2, 0);
  }

  String _date(DateTime value) => value.toIso8601String().substring(0, 10);
  Future<void> _pick(bool from) async {
    final date = await showDatePicker(
      context: context,
      initialDate: from ? _from : _to,
      firstDate: DateTime(2020),
      lastDate: DateTime.now().add(const Duration(days: 730)),
    );
    if (date != null) {
      setState(() {
        if (from) {
          _from = date;
        } else {
          _to = date;
        }
        _preview = null;
      });
    }
  }


  Future<void> _confirmApply() async {
    final replaced = (_preview?['replaced'] as num?)?.toInt() ?? 0;
    if (replaced > 0) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('Заменить график?'),
          content: Text(
            'Будет заменено будущих смен: $replaced. '
            'Старые назначения исчезнут из графика. Продолжить?',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('Отмена'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: const Text('Заменить'),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted) return;
    }
    await _plan(true);
  }

  Future<void> _plan(bool apply) async {
    setState(() => _busy = true);
    try {
      final c = widget.controller;
      if (apply) {
        await c.request(
          'duty/apply/',
          method: 'POST',
          data: {'token': _preview!['token']},
          expectedOrganization: _org,
        );
        await c.refresh();
        if (mounted) Navigator.pop(context);
      } else {
        final data = await c.request(
          'duty/preview/',
          method: 'POST',
          data: {
            'from': _date(_from),
            'to': _date(_to),
            'technicians': _technicians,
            'time': text(c.me, 'duty_start', '09:00'),
            'hours': 24,
            'kind': 'daily',
            'every': 1,
            'replace': _replace,
          },
          expectedOrganization: _org,
        );
        if (mounted) setState(() => _preview = Map<String, dynamic>.from(data));
      }
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
    builder: (context) {
      final c = widget.controller;
      return Scaffold(
        appBar: AppBar(title: const Text('Составить график')),
        body: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            Sheet(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'Суточные дежурства по очереди',
                    style: TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton(
                    onPressed: _busy ? null : () => _pick(true),
                    child: Text(
                      'С ${dateLabel(_date(_from), 0, time: false)} ${_from.year}',
                    ),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton(
                    onPressed: _busy ? null : () => _pick(false),
                    child: Text(
                      'По ${dateLabel(_date(_to), 0, time: false)} ${_to.year}',
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    'Начало смены ${text(c.me, 'duty_start', '09:00')} · ${text(c.me, 'timezone')}',
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Нажмите механиков в порядке чередования. Повторное нажатие убирает из очереди.',
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final t
                          in c
                              .list('technicians')
                              .where((t) => t['active'] == true))
                        FilterChip(
                          selected: _technicians.contains(t['id']),
                          label: Text(
                            '${_technicians.contains(t['id']) ? '${_technicians.indexOf(t['id'] as int) + 1}. ' : ''}${text(t, 'full_name')}',
                          ),
                          onSelected: _busy
                              ? null
                              : (v) => setState(() {
                                  if (v) {
                                    _technicians.add(t['id'] as int);
                                  } else {
                                    _technicians.remove(t['id']);
                                  }
                                  _preview = null;
                                }),
                        ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  SwitchListTile.adaptive(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Заменить существующий график'),
                    subtitle: const Text(
                      'Только будущие смены в выбранном периоде. '
                      'Начавшиеся дежурства нельзя перезаписать.',
                    ),
                    value: _replace,
                    onChanged: _busy
                        ? null
                        : (value) => setState(() {
                            _replace = value;
                            _preview = null;
                          }),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _busy || !c.canWrite || _technicians.isEmpty
                  ? null
                  : () => _plan(false),
              child: const Text('Предпросмотр графика'),
            ),
            if (_preview != null) ...[
              const SectionTitle('Предпросмотр'),
              Text(
                'Новых смен: ${(_preview!['added'] as List).length} · будет заменено: ${_preview!['replaced']} · пропущено занятых: ${_preview!['skipped']}',
              ),
              const SizedBox(height: 12),
              for (final row in records(_preview!['added']))
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(
                    c
                            .list('technicians')
                            .where((t) => t['id'] == row['technician_id'])
                            .map((t) => text(t, 'full_name'))
                            .firstOrNull ??
                        'Механик',
                  ),
                  subtitle: Text(
                    '${dateLabel(row['start'], c.utcOffset)} — ${dateLabel(row['end'], c.utcOffset)}',
                  ),
                ),
              FilledButton(
                onPressed:
                    _busy || !c.canWrite || (_preview!['added'] as List).isEmpty
                    ? null
                    : _confirmApply,
                child: const Text('Применить график'),
              ),
            ],
            const SizedBox(height: 16),
            const Text(
              'Без переключателя существующие смены сохраняются. Для изменения текущей смены используется подтверждённая подмена.',
              style: TextStyle(fontSize: 12),
            ),
          ],
        ),
      );
    },
  );
}
