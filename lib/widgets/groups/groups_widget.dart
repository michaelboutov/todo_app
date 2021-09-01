import 'package:flutter/material.dart';
import 'package:flutter_slidable/flutter_slidable.dart';
import 'package:flutter_slidable/flutter_slidable.dart';

class GroupsWidget extends StatelessWidget {
  const GroupsWidget({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text("groups"),
      ),
      body: const _GroupListWidget(),
      floatingActionButton: FloatingActionButton(
        onPressed: () {},
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
    return ListView.separated(
        itemBuilder: (BuildContext context, int index) {
          return const Divider(
            height: 1,
          );
        },
        separatorBuilder: (BuildContext context, int index) {
          return GroupListRowWidget(
            indexList: index,
          );
        },
        itemCount: 100);
  }
}

class GroupListRowWidget extends StatelessWidget {
  final int indexList;
  const GroupListRowWidget({Key? key, required this.indexList})
      : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Slidable(
      actionPane: SlidableDrawerActionPane(),
      child: ListTile(
        title: Text("one_line with traling widget"),
      ),
      secondaryActions: <Widget>[
        IconSlideAction(
          caption: 'Delete',
          color: Colors.red,
          icon: Icons.delete,
          onTap: () => () {},
        ),
      ],
    );
  }
}
