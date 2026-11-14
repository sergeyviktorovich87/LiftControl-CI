import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'core/controller.dart';
import 'core/diagnostics.dart';
import 'core/storage.dart';
import 'ui/common.dart';
import 'ui/login.dart';
import 'ui/shell.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final store = DeviceStore();
  final log = Diagnostics(store);
  FlutterError.onError = (details) {
    log.record('flutter_error');
  };
  PlatformDispatcher.instance.onError = (error, stack) {
    log.record('unhandled_error');
    return true;
  };
  runApp(
    LiftApp(
      controller: LiftController(store: store, log: log),
    ),
  );
}

class LiftApp extends StatefulWidget {
  const LiftApp({super.key, required this.controller, this.initialize = true});
  final LiftController controller;
  final bool initialize;
  @override
  State<LiftApp> createState() => _LiftAppState();
}

class _LiftAppState extends State<LiftApp> with WidgetsBindingObserver {
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (widget.initialize) unawaited(widget.controller.initialize());
    _startRefresh();
  }

  void _startRefresh() {
    _timer?.cancel();
    _timer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => widget.controller.refresh(),
    );
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _startRefresh();
      unawaited(
        widget.controller.refresh().then(
          (_) => widget.controller.syncDevice(forceUpdates: true),
        ),
      );
    } else {
      _timer?.cancel();
      unawaited(widget.controller.flushDrafts());
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.controller,
    builder: (context, _) {
      final c = widget.controller;
      return MaterialApp(
        title: 'LiftControl',
        debugShowCheckedModeBanner: false,
        theme: liftTheme(false),
        darkTheme: liftTheme(true),
        themeMode: c.dark ? ThemeMode.dark : ThemeMode.light,
        locale: const Locale('ru'),
        supportedLocales: const [Locale('ru'), Locale('en')],
        localizationsDelegates: GlobalMaterialLocalizations.delegates,
        home: c.starting
            ? const Scaffold(body: Center(child: CircularProgressIndicator()))
            : c.loggedIn
            ? LiftShell(controller: c)
            : LoginPage(controller: c),
      );
    },
  );
}
