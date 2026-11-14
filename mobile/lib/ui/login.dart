import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../core/controller.dart';
import '../core/diagnostics.dart';
import 'common.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key, required this.controller});
  final LiftController controller;
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _server;
  final _login = TextEditingController(), _password = TextEditingController();
  bool _busy = false, _visible = false;
  String _error = '';
  @override
  void initState() {
    super.initState();
    _server = TextEditingController(text: widget.controller.serverText);
    _error = widget.controller.error;
  }

  @override
  void dispose() {
    _server.dispose();
    _login.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_busy || !_form.currentState!.validate()) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _busy = true;
      _error = '';
    });
    try {
      await widget.controller.login(_server.text, _login.text, _password.text);
      TextInput.finishAutofillContext(
        shouldSave: false,
      ); // Device key, never a saved account password.
      _password.clear();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Center(
                  child: Container(
                    width: 76,
                    height: 76,
                    decoration: BoxDecoration(
                      color: forest,
                      borderRadius: BorderRadius.circular(24),
                    ),
                    child: const Icon(
                      Icons.elevator_outlined,
                      color: Colors.white,
                      size: 44,
                    ),
                  ),
                ),
                const SizedBox(height: 22),
                Text(
                  'LiftControl',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.headlineLarge?.copyWith(
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 8),
                const Text(
                  'Работа с лифтами, заявками и дежурствами',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 30),
                Sheet(
                  child: Form(
                    key: _form,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(
                          'Вход в систему',
                          style: Theme.of(context).textTheme.titleLarge
                              ?.copyWith(fontWeight: FontWeight.bold),
                        ),
                        const SizedBox(height: 20),
                        TextFormField(
                          key: const Key('server'),
                          controller: _server,
                          keyboardType: TextInputType.url,
                          autocorrect: false,
                          textCapitalization: TextCapitalization.none,
                          decoration: const InputDecoration(
                            labelText: 'Адрес сервера',
                            hintText: 'https://ваш-домен.ru',
                            prefixIcon: Icon(Icons.lock_outline),
                          ),
                          validator: (v) => (v?.trim().isEmpty ?? true)
                              ? 'Введите адрес сервера'
                              : null,
                        ),
                        const SizedBox(height: 14),
                        TextFormField(
                          key: const Key('username'),
                          controller: _login,
                          autocorrect: false,
                          textInputAction: TextInputAction.next,
                          decoration: const InputDecoration(
                            labelText: 'Логин',
                            prefixIcon: Icon(Icons.person_outline),
                          ),
                          validator: (v) => (v?.trim().isEmpty ?? true)
                              ? 'Введите логин'
                              : null,
                        ),
                        const SizedBox(height: 14),
                        TextFormField(
                          key: const Key('password'),
                          controller: _password,
                          obscureText: !_visible,
                          autocorrect: false,
                          enableSuggestions: false,
                          autofillHints: null,
                          onFieldSubmitted: (_) => _submit(),
                          decoration: InputDecoration(
                            labelText: 'Пароль',
                            prefixIcon: const Icon(Icons.key_outlined),
                            suffixIcon: IconButton(
                              tooltip: _visible
                                  ? 'Скрыть пароль'
                                  : 'Показать пароль',
                              onPressed: () =>
                                  setState(() => _visible = !_visible),
                              icon: Icon(
                                _visible
                                    ? Icons.visibility_off_outlined
                                    : Icons.visibility_outlined,
                              ),
                            ),
                          ),
                          validator: (v) =>
                              (v?.isEmpty ?? true) ? 'Введите пароль' : null,
                        ),
                        if (_error.isNotEmpty)
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 14),
                            child: Text(
                              _error,
                              style: TextStyle(
                                color: Theme.of(context).colorScheme.error,
                              ),
                            ),
                          ),
                        const SizedBox(height: 20),
                        FilledButton(
                          key: const Key('login'),
                          onPressed: _busy ? null : _submit,
                          child: Text(_busy ? 'Подключение…' : 'Войти'),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                OutlinedButton.icon(
                  key: const Key('preview'),
                  onPressed: _busy ? null : widget.controller.preview,
                  icon: const Icon(Icons.preview_outlined),
                  label: const Text('Посмотреть интерфейс'),
                ),
                const SizedBox(height: 18),
                Text(
                  'Android · ${Diagnostics.version}',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
      ),
    ),
  );
}
