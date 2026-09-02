import 'package:flutter/material.dart';
import '../models/worker.dart';
import '../repositories/worker_repository.dart';

class WorkforceController extends ChangeNotifier {
  final WorkerRepository repository;

  List<WorkerInfo> _workers = [];
  List<WorkerInfo> get workers => _workers;

  WorkerInfo? _selectedWorker;
  WorkerInfo? get selectedWorker => _selectedWorker;

  bool _isLoading = false;
  bool get isLoading => _isLoading;

  String? _errorMessage;
  String? get errorMessage => _errorMessage;

  WorkforceController({required this.repository}) {
    loadWorkers();
  }

  Future<void> loadWorkers() async {
    _isLoading = true;
    notifyListeners();
    try {
      _workers = await repository.listWorkers();
      _errorMessage = null;
    } catch (e) {
      _errorMessage = 'Failed to load workers: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  void selectWorker(WorkerInfo? worker) {
    _selectedWorker = worker;
    notifyListeners();
  }
}
