import 'package:flutter/material.dart';
import '../models/approval.dart';
import '../models/policy.dart';
import '../repositories/approval_repository.dart';

class ApprovalController extends ChangeNotifier {
  final ApprovalRepository repository;
  final String projectId;

  List<ApprovalItem> _approvals = [];
  List<ApprovalItem> get approvals => _approvals;

  List<UserInputItem> _userInputs = [];
  List<UserInputItem> get userInputs => _userInputs;

  List<DecisionItem> _decisions = [];
  List<DecisionItem> get decisions => _decisions;

  AutonomyPolicyModel? _policy;
  AutonomyPolicyModel? get policy => _policy;

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  String? _errorMessage;
  String? get errorMessage => _errorMessage;

  ApprovalController({required this.repository, required this.projectId}) {
    loadApprovals();
  }

  Future<void> loadApprovals() async {
    _isLoading = true;
    notifyListeners();
    try {
      final results = await Future.wait([
        repository.listPendingApprovals(projectId),
        repository.listPendingUserInputs(projectId),
        repository.listPendingDecisions(projectId),
        repository.getPolicy(projectId),
      ]);

      _approvals = results[0] as List<ApprovalItem>;
      _userInputs = results[1] as List<UserInputItem>;
      _decisions = results[2] as List<DecisionItem>;
      _policy = results[3] as AutonomyPolicyModel;
      _errorMessage = null;
    } catch (e) {
      _errorMessage = 'Failed to load approvals: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> grant(String approvalId) async {
    try {
      await repository.grantApproval(approvalId);
      await loadApprovals();
    } catch (e) {
      _errorMessage = 'Failed to grant approval: $e';
      notifyListeners();
    }
  }

  Future<void> reject(String approvalId, {String reason = ''}) async {
    try {
      await repository.rejectApproval(approvalId, reason: reason);
      await loadApprovals();
    } catch (e) {
      _errorMessage = 'Failed to reject approval: $e';
      notifyListeners();
    }
  }

  Future<void> answerInput(String inputId, String answer) async {
    try {
      await repository.respondToUserInput(inputId, answer);
      await loadApprovals();
    } catch (e) {
      _errorMessage = 'Failed to submit answer: $e';
      notifyListeners();
    }
  }

  Future<void> makeDecision(String decisionId, String option) async {
    try {
      await repository.respondToDecision(decisionId, option);
      await loadApprovals();
    } catch (e) {
      _errorMessage = 'Failed to submit decision: $e';
      notifyListeners();
    }
  }

  Future<void> emergencyStop() async {
    try {
      await repository.emergencyStop(projectId);
      await loadApprovals();
    } catch (e) {
      _errorMessage = 'Emergency stop failed: $e';
      notifyListeners();
    }
  }

  Future<void> clearEmergencyStop() async {
    try {
      await repository.clearEmergencyStop(projectId);
      await loadApprovals();
    } catch (e) {
      _errorMessage = 'Clear emergency stop failed: $e';
      notifyListeners();
    }
  }
}
