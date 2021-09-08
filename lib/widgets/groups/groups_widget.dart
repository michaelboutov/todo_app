import 'package:flutter/material.dart';
import 'package:flutter_slidable/flutter_slidable.dart';
import 'package:todo_app/widgets/groups/groups_widget_model.dart';

class GroupsWidget extends StatefulWidget {
  const GroupsWidget({Key? key}) : super(key: key);

  @override
  _GroupsWidgetState createState() => _GroupsWidgetState();
}

class _GroupsWidgetState extends State<GroupsWidget> {
  final model = GroupWidgetModel();
  @override
  Widget build(BuildContext context) {
    return GroupWidgetModelProvider(
      child: const _GroupsWidgetBudy(),
      model: model,
    );
  }
}

class _GroupsWidgetBudy extends StatelessWidget {
  const _GroupsWidgetBudy({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text("groups"),
      ),
      body: const _GroupListWidget(),
      floatingActionButton: FloatingActionButton(
        onPressed: () {
          GroupWidgetModelProvider.read(context)?.model.showForm(context);
        },
        child: const Icon(Icons.add),
      ),
    );
  }
}

class _GroupListWidget extends StatefulWidget {
  const _GroupListWidget({Key? key}) : super(key: key);

  @override
  _GroupListWidgetState createState() => _GroupListWidgetState();
}

class _GroupListWidgetState extends State<_GroupListWidget> {
  @override
  Widget build(BuildContext context) {
    final groupsCount =
        GroupWidgetModelProvider.watch(context)?.model.groups.length ?? 0;
    return ListView.separated(
        itemBuilder: (BuildContext context, int index) {
          return GroupListRowWidget(
            indexList: index,
          );
        },
        separatorBuilder: (BuildContext context, int index) {
          return const Divider(
            height: 1,
          );
        },
        itemCount: groupsCount);
  }
}

class GroupListRowWidget extends StatelessWidget {
  final int indexList;
  const GroupListRowWidget({Key? key, required this.indexList})
      : super(key: key);

  @override
  Widget build(BuildContext context) {
    final model = GroupWidgetModelProvider.read(context)!.model;
    final group =
        GroupWidgetModelProvider.read(context)!.model.groups[indexList];
    return Slidable(
      actionPane: SlidableDrawerActionPane(),
      child: ListTile(
        title: Text(group.name),
        onTap: () {},
      ),
      secondaryActions: <Widget>[
        IconSlideAction(
          caption: 'Delete',
          color: Colors.red,
          icon: Icons.delete,
          onTap: () => model.deleteGroup(indexList),
        ),
      ],
    );
  }
}
