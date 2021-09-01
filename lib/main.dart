import 'package:flutter/material.dart';
import 'package:todo_app/widgets/group_form/group_form_widget.dart';
import 'package:todo_app/widgets/groups/groups_widget.dart';

void main() {
  runApp(MyApp());
}

class MyApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Flutter Demo',
      theme: ThemeData(
        primarySwatch: Colors.blue,
      ),
      routes: {
        '/groups': (context) => const GroupsWidget(),
        '/groups/form': (context) => const GroupsFormWidget()
      },
      initialRoute: '/groups',
    );
  }
}
