import 'package:flutter/material.dart';
import '../models/event.dart';
import '../repositories/activity_repository.dart';

class ActivityController extends ChangeNotifier {
  final ActivityRepository repository;
  final String? projectId;

  List<ActivityItemModel> _activities = [];
  List<ActivityItemModel> get activities => _filteredActivities;

  String? _selectedWorkerFilter;
  String? get selectedWorkerFilter => _selectedWorkerFilter;

  String? _selectedLevelFilter;
  String? get selectedLevelFilter => _selectedLevelFilter;

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  String? _errorMessage;
  String? get errorMessage => _errorMessage;

  ActivityController({required this.repository, this.projectId}) {
    loadActivities();
  }

  List<ActivityItemModel> get _filteredActivities {
    return _activities.where((a) {
      if (_selectedWorkerFilter != null && a.workerId != _selectedWorkerFilter) {
        return false;
      }
      if (_selectedLevelFilter != null && a.level != _selectedLevelFilter) {
        return false;
      }
      return true;
    }).toList();
  }

  Future<void> loadActivities() async {
    _isLoading = true;
    notifyListeners();
    try {
      if (projectId != null) {
        _activities = await repository.getProjectActivity(projectId!);
      } else {
        _activities = await repository.getActivityFeed();
      }
      _errorMessage = null;
    } catch (e) {
      _errorMessage = 'Failed to load activity feed: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  void setWorkerFilter(String? workerId) {
    _selectedWorkerFilter = workerId;
    notifyListeners();
  }

  void setLevelFilter(String? level) {
    _selectedLevelFilter = level;
    notifyListeners();
  }
}
