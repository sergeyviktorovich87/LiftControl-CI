import 'package:flutter/material.dart';

import '../core/api.dart';
import '../core/controller.dart';

const forest = Color(0xFF14352B);
const green = Color(0xFF237651);
const pale = Color(0xFFE8F1E5);
const statusNames = {
  'NEW': 'Новая',
  'ASSIGNED': 'Назначена',
  'IN_PROGRESS': 'В работе',
  'STOPPED': 'Лифт остановлен',
  'DONE': 'Выполнена',
  'CANCELED': 'Отменена',
};
const priorityNames = {
  'LOW': 'Низкий',
  'NORMAL': 'Обычный',
  'HIGH': 'Срочный',
  'EMERGENCY': 'Аварийный',
  'ENTRAPMENT': 'Застревание',
};
const roleNames = {
  'ADMIN': 'Администратор',
  'DIRECTOR': 'Руководитель',
  'DISPATCHER': 'Диспетчер',
  'MECHANIC': 'Механик',
  'ADJUSTER': 'Наладчик',
};

String text(Json item, String key, [String fallback = '—']) {
  final value = item[key];
  return value == null || '$value'.isEmpty ? fallback : '$value';
}

String liftLabel(Json item) => text(
  item,
  'factory_number',
  text(item, 'inventory_number', 'Лифт №${item['id']}'),
);
String liftDetails(Json item) => [
  if (text(item, 'entrance', '').isNotEmpty) 'подъезд ${item['entrance']}',
  if (text(item, 'model', '').isNotEmpty) item['model'],
  if (item['capacity_kg'] != null) '${item['capacity_kg']} кг',
  if (text(item, 'inventory_number', '').isNotEmpty)
    'учётный ${item['inventory_number']}',
  if (text(item, 'factory_number', '').isNotEmpty)
    'зав. ${item['factory_number']}',
].join(' · ');

class SyncStatus extends StatelessWidget {
  const SyncStatus({super.key, required this.ticket});
  final Json ticket;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 8),
    child: Text(
      ticket['sync_state'] == 'failed'
          ? 'Требует внимания · ${ticket['sync_error'] ?? 'Запись сохранена на телефоне'}'
          : 'Сохранено на телефоне · ожидает отправки',
      style: TextStyle(
        color: ticket['sync_state'] == 'failed'
            ? Theme.of(context).colorScheme.error
            : Theme.of(context).colorScheme.primary,
        fontSize: 12,
      ),
    ),
  );
}

String dateLabel(dynamic value, int offset, {bool time = true}) {
  if (value == null || '$value'.isEmpty) return '—';
  final raw = '$value';
  final parsed = DateTime.tryParse(raw);
  if (parsed == null) return '—';
  final day = raw.length == 10
      ? parsed
      : parsed.toUtc().add(Duration(minutes: offset));
  const months = [
    'янв',
    'фев',
    'мар',
    'апр',
    'мая',
    'июн',
    'июл',
    'авг',
    'сен',
    'окт',
    'ноя',
    'дек',
  ];
  final hm =
      '${day.hour.toString().padLeft(2, '0')}:${day.minute.toString().padLeft(2, '0')}';
  return '${day.day} ${months[day.month - 1]}${time && raw.length > 10 ? ', $hm' : ''}';
}

ThemeData liftTheme(bool dark, {String appearance = 'light'}) {
  final accent = const {
    'blue': Color(0xFF28589D),
    'graphite': Color(0xFF475569),
    'green': Color(0xFF2B6C33),
    'purple': Color(0xFF6E4199),
  }[appearance];
  final generated = ColorScheme.fromSeed(
    seedColor: accent ?? green,
    brightness: dark ? Brightness.dark : Brightness.light,
  );
  final scheme = accent != null
      ? generated
      : generated.copyWith(
          primary: dark ? const Color(0xFF79C69B) : green,
          surface: dark ? const Color(0xFF1A2922) : Colors.white,
          onSurface: dark ? const Color(0xFFE7F0E9) : const Color(0xFF24362F),
          outlineVariant: dark
              ? const Color(0xFF33463B)
              : const Color(0xFFE1E7E2),
        );
  final background = accent != null
      ? scheme.surfaceContainerLow
      : dark
      ? const Color(0xFF101B17)
      : const Color(0xFFF2F5F3);
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: background,
    appBarTheme: AppBarTheme(
      backgroundColor: background,
      surfaceTintColor: Colors.transparent,
      centerTitle: false,
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(20),
        side: BorderSide(color: scheme.outlineVariant),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: accent != null
          ? scheme.surface
          : dark
          ? const Color(0xFF203128)
          : Colors.white,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide(color: scheme.outlineVariant),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(48, 48),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(48, 48),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    navigationBarTheme: const NavigationBarThemeData(
      height: 76,
      indicatorColor: Color(0xFFDCECDC),
      labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
    ),
    dividerTheme: DividerThemeData(color: scheme.outlineVariant, space: 24),
  );
}

class Sheet extends StatelessWidget {
  const Sheet({super.key, required this.child, this.padding = 18, this.color});
  final Widget child;
  final double padding;
  final Color? color;
  @override
  Widget build(BuildContext context) => Card(
    color: color,
    child: Padding(padding: EdgeInsets.all(padding), child: child),
  );
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.title, {super.key, this.trailing});
  final String title;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 8, bottom: 14),
    child: Row(
      children: [
        Expanded(
          child: Text(
            title,
            style: Theme.of(
              context,
            ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
          ),
        ),
        ?trailing,
      ],
    ),
  );
}

class EmptyState extends StatelessWidget {
  const EmptyState(
    this.title,
    this.description, {
    super.key,
    this.icon = Icons.inbox_outlined,
  });
  final String title, description;
  final IconData icon;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 36, horizontal: 20),
    child: Column(
      children: [
        Icon(icon, size: 44, color: Theme.of(context).colorScheme.primary),
        const SizedBox(height: 14),
        Text(
          title,
          textAlign: TextAlign.center,
          style: Theme.of(
            context,
          ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        Text(
          description,
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyMedium,
        ),
      ],
    ),
  );
}

class StatusPill extends StatelessWidget {
  const StatusPill(this.status, {super.key});
  final String status;
  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      'NEW' => const Color(0xFF9A5C0B),
      'IN_PROGRESS' || 'ASSIGNED' => const Color(0xFF2861A4),
      'DONE' => green,
      'STOPPED' => const Color(0xFFB44840),
      _ => const Color(0xFF71666C),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        statusNames[status] ?? status,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          color: Theme.of(context).brightness == Brightness.dark
              ? Color.lerp(color, Colors.white, 0.45)
              : color,
        ),
      ),
    );
  }
}

class TicketTile extends StatelessWidget {
  const TicketTile(
    this.ticket, {
    super.key,
    required this.controller,
    required this.onTap,
  });
  final Json ticket;
  final LiftController controller;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Card(
    child: InkWell(
      borderRadius: BorderRadius.circular(20),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.all(17),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                  (ticket['id'] as int) < 0
                      ? 'На телефоне'
                      : '#${ticket['id']}',
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.primary,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    dateLabel(ticket['created_at'], controller.utcOffset),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
                StatusPill(text(ticket, 'status')),
              ],
            ),
            const SizedBox(height: 12),
            if (ticket['sync_state'] != null) SyncStatus(ticket: ticket),
            Text(
              text(ticket, 'title'),
              style: Theme.of(
                context,
              ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.location_on_outlined, size: 18),
                const SizedBox(width: 5),
                Expanded(child: Text(text(ticket, 'address'))),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              runSpacing: 6,
              children: [
                Text(
                  'Лифт ${text(ticket, 'elevator_label')}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                if (ticket['priority'] == 'HIGH' ||
                    ticket['priority'] == 'EMERGENCY' ||
                    ticket['priority'] == 'ENTRAPMENT')
                  Text(
                    priorityNames[ticket['priority']]!,
                    style: const TextStyle(
                      color: Color(0xFFBA4B36),
                      fontWeight: FontWeight.w700,
                    ),
                  ),
              ],
            ),
            const Divider(),
            Row(
              children: [
                const Icon(Icons.person_outline, size: 18),
                const SizedBox(width: 7),
                Expanded(
                  child: Text(
                    text(ticket, 'technician_name', 'Исполнитель не назначен'),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
                const Icon(Icons.chevron_right, size: 20),
              ],
            ),
          ],
        ),
      ),
    ),
  );
}

class ScopePage extends StatelessWidget {
  const ScopePage({
    super.key,
    required this.controller,
    required this.organization,
    required this.builder,
  });
  final LiftController controller;
  final int organization;
  final WidgetBuilder builder;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) {
      if (!controller.loggedIn || controller.organization != organization) {
        return Scaffold(
          appBar: AppBar(),
          body: const EmptyState(
            'Запись закрыта',
            'Изменились сеанс или организация. Вернитесь на главный экран.',
          ),
        );
      }
      return builder(context);
    },
  );
}

void showError(BuildContext context, Object error) {
  final message = error is ApiError
      ? error.message
      : 'Не удалось выполнить действие. Попробуйте обновить данные.';
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text(message), duration: const Duration(seconds: 6)),
  );
}

Future<String?> askText(
  BuildContext context, {
  required String title,
  required String label,
  int minimum = 3,
  int maximum = 4000,
  String initial = '',
  ValueChanged<String>? onChanged,
  String action = 'Сохранить',
}) async {
  final form = GlobalKey<FormState>();
  final input = TextEditingController(text: initial);
  final result = await showDialog<String>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: SizedBox(
        width: 480,
        child: Form(
          key: form,
          child: SingleChildScrollView(
            child: TextFormField(
              controller: input,
              autofocus: true,
              minLines: 3,
              maxLines: 7,
              maxLength: maximum,
              onChanged: onChanged,
              decoration: InputDecoration(labelText: label),
              validator: (v) => (v?.trim().length ?? 0) < minimum
                  ? 'Минимум $minimum символов'
                  : null,
            ),
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Отмена'),
        ),
        FilledButton(
          onPressed: () {
            if (form.currentState!.validate()) {
              Navigator.pop(context, input.text.trim());
            }
          },
          child: Text(action),
        ),
      ],
    ),
  );
  // The dialog route animates out after its result is delivered.
  Future<void>.delayed(const Duration(milliseconds: 350), input.dispose);
  return result;
}

Widget separated(List<Widget> children, {double gap = 12}) => Column(
  crossAxisAlignment: CrossAxisAlignment.stretch,
  children: [
    for (var i = 0; i < children.length; i++) ...[
      if (i > 0) SizedBox(height: gap),
      children[i],
    ],
  ],
);
