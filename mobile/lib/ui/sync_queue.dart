import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/controller.dart';
import 'common.dart';

class SyncQueuePage extends StatelessWidget {
  const SyncQueuePage({super.key, required this.controller});
  final LiftController controller;
  @override
  Widget build(BuildContext context) => ScopePage(
    controller: controller,
    organization: controller.organization!,
    builder: (context) => Scaffold(
      appBar: AppBar(title: const Text('Очередь отправки')),
      body: ListView(
        padding: const EdgeInsets.all(18),
        children: [
          const Text(
            'Записи сохранены на телефоне. После подключения они отправляются автоматически. Если сеанс истечёт, войдите тем же аккаунтом: очередь сохранится.',
          ),
          const SizedBox(height: 12),
          OutlinedButton.icon(
            onPressed: controller.refreshing ? null : controller.refresh,
            icon: const Icon(Icons.sync),
            label: const Text('Проверить связь и отправить'),
          ),
          if (controller.pendingOperations.isEmpty)
            const EmptyState(
              'Всё отправлено',
              'Неотправленных записей в этой организации нет.',
            ),
          for (final row in controller.pendingOperations)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Sheet(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      text(
                        Map<String, dynamic>.from(row['local_ticket'] as Map),
                        'title',
                      ),
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    Text(
                      '${text(Map<String, dynamic>.from(row['local_ticket'] as Map), 'address')} · ${text(Map<String, dynamic>.from(row['local_ticket'] as Map), 'elevator_label')}',
                    ),
                    Text(
                      {
                            'create': 'Новая заявка',
                            'accept': 'Принятие в работу',
                            'complete': 'Результат ремонта',
                            'stop': 'Остановка лифта',
                            'resume': 'Продолжение ремонта',
                            'comment': 'Комментарий',
                            'photo': 'Фотография',
                            'media': 'Фото / видео / сообщение',
                          }[row['command']['type']] ??
                          'Запись',
                    ),
                    Text(
                      row['state'] == 'failed'
                          ? 'Требует внимания'
                          : 'Ожидает отправки',
                    ),
                    if ((row['error'] ?? '').toString().isNotEmpty)
                      Text(
                        row['error'] as String,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    if (!['photo', 'media'].contains(row['command']['type']))
                      SelectableText(row['command']['body'].toString()),
                    Wrap(
                      spacing: 8,
                      children: [
                        TextButton.icon(
                          icon: const Icon(Icons.copy_outlined),
                          label: const Text('Копировать'),
                          onPressed: () async {
                            final body =
                                [
                                  'photo',
                                  'media',
                                ].contains(row['command']['type'])
                                ? 'Вложение ${row['command']['body']['name'] ?? ''} сохранено на телефоне. ${row['command']['body']['body'] ?? ''}'
                                : row['command']['body'].toString();
                            await Clipboard.setData(
                              ClipboardData(
                                text:
                                    '${row['local_ticket']['title']}\n${row['local_ticket']['address']}\n${row['local_ticket']['elevator_label']}\n$body\n${row['error'] ?? ''}',
                              ),
                            );
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(
                                  content: Text('Запись скопирована'),
                                ),
                              );
                            }
                          },
                        ),
                        if (row['state'] == 'failed')
                          TextButton(
                            onPressed: () async {
                              try {
                                await controller.retryOperation(
                                  row['id'] as String,
                                );
                              } catch (e) {
                                if (context.mounted) showError(context, e);
                              }
                            },
                            child: const Text('Повторить отправку'),
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    ),
  );
}
