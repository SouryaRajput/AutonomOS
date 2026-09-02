import '../models/worker.dart';
import '../services/api_client.dart';

class WorkerRepository {
  final AutonomOSApiClient _api;

  WorkerRepository(this._api);

  Future<List<WorkerInfo>> listWorkers() => _api.listWorkers();

  Future<List<WorkerInfo>> getWorkers() => listWorkers();

  Future<WorkerInfo> getWorker(String workerId) => _api.getWorker(workerId);
}
