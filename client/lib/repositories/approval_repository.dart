import '../models/approval.dart';
import '../models/policy.dart';
import '../services/api_client.dart';

class ApprovalRepository {
  final AutonomOSApiClient _api;

  ApprovalRepository(this._api);

  Future<List<ApprovalItem>> listPendingApprovals(String projectId) =>
      _api.listPendingApprovals(projectId);

  Future<ApprovalItem> grantApproval(String approvalId, {String decidedBy = 'User'}) =>
      _api.grantApproval(approvalId, decidedBy: decidedBy);

  Future<ApprovalItem> rejectApproval(String approvalId, {String reason = '', String decidedBy = 'User'}) =>
      _api.rejectApproval(approvalId, reason: reason, decidedBy: decidedBy);

  Future<List<UserInputItem>> listPendingUserInputs(String projectId) =>
      _api.listPendingUserInputs(projectId);

  Future<UserInputItem> respondToUserInput(String inputId, String answer) =>
      _api.respondToUserInput(inputId, answer);

  Future<List<DecisionItem>> listPendingDecisions(String projectId) =>
      _api.listPendingDecisions(projectId);

  Future<DecisionItem> respondToDecision(String decisionId, String chosenOption, {String rationale = ''}) =>
      _api.respondToDecision(decisionId, chosenOption, rationale: rationale);

  Future<AutonomyPolicyModel> getPolicy(String projectId) =>
      _api.getPolicy(projectId);

  Future<AutonomyPolicyModel> updatePolicy(String projectId, {String? autonomyLevel}) =>
      _api.updatePolicy(projectId, autonomyLevel: autonomyLevel);

  Future<bool> emergencyStop(String projectId, {String reason = 'User initiated'}) =>
      _api.emergencyStop(projectId, reason: reason);

  Future<bool> clearEmergencyStop(String projectId) =>
      _api.clearEmergencyStop(projectId);
}
