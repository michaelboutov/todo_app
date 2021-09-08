import 'package:flutter/material.dart';
import 'package:todo_app/widgets/groups/groups_widget_model.dart';

import 'group_form_widget_model.dart';

class GroupsFormWidget extends StatefulWidget {
  const GroupsFormWidget({Key? key}) : super(key: key);

  @override
  _GroupsFormWidgetState createState() => _GroupsFormWidgetState();
}

class _GroupsFormWidgetState extends State<GroupsFormWidget> {
  final model = GroupFormWidgetModel();
  @override
  Widget build(BuildContext context) {
    return GroupFormWidgetModelProvider(
        model: model, child: const _GroupeFormWidgetBody());
  }
}

class GroupsFormWidgetModel {}

class _GroupeFormWidgetBody extends StatelessWidget {
  const _GroupeFormWidgetBody({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(" new form", textAlign: TextAlign.center),
      ),
      body: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Center(
          child: const _GroupNameWidget(),
        ),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => GroupFormWidgetModelProvider.read(context)
            ?.model
            .saveGroup(context),
        child: const Icon(Icons.done),
      ),
    );
  }
}

class GroupsFormWidgetModelProvider {}

class _GroupNameWidget extends StatelessWidget {
  const _GroupNameWidget({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    final model = GroupFormWidgetModelProvider.read(context)?.model;
    return TextField(
      onEditingComplete: () => model?.saveGroup(context),
      autofocus: true,
      decoration: InputDecoration(border: OutlineInputBorder()),
      onChanged: (value) => model?.groupName = value,
    );
  }
}
