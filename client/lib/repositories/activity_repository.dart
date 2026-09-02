import '../models/event.dart';
import '../services/api_client.dart';

class ActivityRepository {
  final AutonomOSApiClient _api;

  ActivityRepository(this._api);

  Future<List<ActivityItemModel>> getActivityFeed({int limit = 50}) =>
      _api.getActivityFeed(limit: limit);

  Future<List<ActivityItemModel>> getProjectActivity(String projectId, {int limit = 50}) =>
      _api.getProjectActivity(projectId, limit: limit);

  Stream<EventModel> subscribeEvents({int sinceSequence = 0}) =>
      _api.subscribeEvents(sinceSequence: sinceSequence);
}
