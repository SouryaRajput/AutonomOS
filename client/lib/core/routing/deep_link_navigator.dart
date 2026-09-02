import '../../models/artifact.dart';
import '../../models/task.dart';
import '../../state/app_state.dart';

enum DeepLinkTarget {
  project,
  workflow,
  task,
  artifact,
  approval,
  chat,
  activity,
  settings,
}

class DeepLinkNavigator {
  final AppState appState;

  DeepLinkNavigator(this.appState);

  Future<bool> handleDeepLink(String uriString) => handleUri(uriString);

  /// Parse a deep link URI and navigate directly to the target context.
  /// Supported schemes:
  /// autonomos://project/:id
  /// autonomos://workflow/:id
  /// autonomos://task/:id
  /// autonomos://artifact/:id
  /// autonomos://approval/:id
  /// autonomos://chat
  Future<bool> handleUri(String uriString) async {
    try {
      final uri = Uri.parse(uriString);
      final segments = uri.pathSegments;

      if (segments.isEmpty) {
        if (uri.host == 'chat') {
          appState.setTab(AppTab.chat);
          return true;
        }
        if (uri.host == 'workflow') {
          appState.setTab(AppTab.workflow);
          return true;
        }
        if (uri.host == 'approvals' || uri.host == 'approval') {
          appState.setTab(AppTab.approvals);
          return true;
        }
        if (uri.host == 'activity') {
          appState.setTab(AppTab.activity);
          return true;
        }
        if (uri.host == 'artifacts' || uri.host == 'artifact') {
          appState.setTab(AppTab.artifacts);
          return true;
        }
        return false;
      }

      final entityType = segments.first.toLowerCase();
      final entityId = segments.length > 1 ? segments[1] : null;

      switch (entityType) {
        case 'project':
          if (entityId != null) {
            final p = appState.projects.where((proj) => proj.id == entityId).toList();
            if (p.isNotEmpty) {
              await appState.selectProject(p.first);
              appState.setTab(AppTab.home);
              return true;
            }
          }
          break;
        case 'chat':
          appState.setTab(AppTab.chat);
          return true;
        case 'workflow':
          appState.setTab(AppTab.workflow);
          return true;
        case 'task':
          appState.setTab(AppTab.workflow);
          return true;
        case 'artifact':
        case 'artifacts':
          appState.setTab(AppTab.artifacts);
          return true;
        case 'approval':
        case 'approvals':
          appState.setTab(AppTab.approvals);
          return true;
        case 'activity':
          appState.setTab(AppTab.activity);
          return true;
      }
    } catch (_) {}
    return false;
  }
}
