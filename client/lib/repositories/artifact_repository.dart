import '../models/artifact.dart';
import '../models/evidence.dart';
import '../models/verification.dart';
import '../services/api_client.dart';

class ArtifactRepository {
  final AutonomOSApiClient _api;

  ArtifactRepository(this._api);

  Future<List<ArtifactModel>> listArtifacts(String projectId, {String? taskId}) =>
      _api.listArtifacts(projectId, taskId: taskId);

  Future<ArtifactModel> getArtifact(String artifactId) =>
      _api.getArtifact(artifactId);

  Future<String?> getArtifactContent(String artifactId) =>
      _api.getArtifactContent(artifactId);

  Future<List<EvidenceModel>> listEvidence(String taskId) =>
      _api.listEvidence(taskId);

  Future<VerificationReportModel?> getVerificationReport(String taskId) =>
      _api.getVerificationReport(taskId);
}
