import '../models/project.dart';
import '../services/api_client.dart';

class ProjectRepository {
  final AutonomOSApiClient _api;

  ProjectRepository(this._api);

  Future<List<Project>> getProjects() => _api.listProjects();

  Future<Project> getProject(String id) => _api.getProject(id);

  Future<Project> createProject({
    required String name,
    required String rootPath,
    String description = '',
  }) => _api.createProject(name: name, rootPath: rootPath, description: description);

  Future<Project> renameProject(String id, String newName) =>
      _api.updateProject(id, name: newName);

  Future<Project> archiveProject(String id) => _api.archiveProject(id);
}
