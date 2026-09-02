import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';

/// Client service communicating with AutonomOS backend workspace API.
class WorkspaceClientService {
  final String baseUrl;
  final HttpClient _httpClient = HttpClient()..connectionTimeout = const Duration(seconds: 5);

  WorkspaceClientService({this.baseUrl = 'http://127.0.0.1:8000'});

  Future<Map<String, dynamic>> getStatus() async {
    try {
      final req = await _httpClient.getUrl(Uri.parse('$baseUrl/api/workspace/status'));
      final res = await req.close();
      if (res.statusCode == 200) {
        final body = await res.transform(utf8.decoder).join();
        return json.decode(body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return {
      'active_path': Directory.current.path,
      'is_valid': true,
      'is_initialized': false,
      'project_name': 'AutonomOS',
      'total_files': 0,
      'tech_stack': {},
    };
  }

  Future<Map<String, dynamic>?> getProjectMap() async {
    try {
      final req = await _httpClient.getUrl(Uri.parse('$baseUrl/api/workspace/map'));
      final res = await req.close();
      if (res.statusCode == 200) {
        final body = await res.transform(utf8.decoder).join();
        return json.decode(body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return null;
  }

  Future<String> getProjectMapMarkdown() async {
    try {
      final req = await _httpClient.getUrl(Uri.parse('$baseUrl/api/workspace/map?format=markdown'));
      final res = await req.close();
      if (res.statusCode == 200) {
        final body = await res.transform(utf8.decoder).join();
        final data = json.decode(body) as Map<String, dynamic>;
        return data['markdown'] as String? ?? '';
      }
    } catch (_) {}
    return '# Project Map\n\nRun initial audit to generate map.';
  }

  Future<List<dynamic>> listSubsystems() async {
    try {
      final req = await _httpClient.getUrl(Uri.parse('$baseUrl/api/workspace/subsystems'));
      final res = await req.close();
      if (res.statusCode == 200) {
        final body = await res.transform(utf8.decoder).join();
        return json.decode(body) as List<dynamic>;
      }
    } catch (_) {}
    return [];
  }

  Future<Map<String, dynamic>?> getFileDetail(String path) async {
    try {
      final uri = Uri.parse('$baseUrl/api/workspace/file-detail').replace(queryParameters: {'path': path});
      final req = await _httpClient.getUrl(uri);
      final res = await req.close();
      if (res.statusCode == 200) {
        final body = await res.transform(utf8.decoder).join();
        return json.decode(body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return null;
  }

  Future<List<dynamic>> getAuditHistory() async {
    try {
      final req = await _httpClient.getUrl(Uri.parse('$baseUrl/api/workspace/audit-history'));
      final res = await req.close();
      if (res.statusCode == 200) {
        final body = await res.transform(utf8.decoder).join();
        return json.decode(body) as List<dynamic>;
      }
    } catch (_) {}
    return [];
  }

  Future<Map<String, dynamic>> setFolder(String folderPath) async {
    try {
      final req = await _httpClient.postUrl(Uri.parse('$baseUrl/api/workspace/set-folder'));
      req.headers.set(HttpHeaders.contentTypeHeader, 'application/json; charset=utf-8');
      req.add(utf8.encode(json.encode({'folder_path': folderPath})));
      final res = await req.close();
      final body = await res.transform(utf8.decoder).join();
      return json.decode(body) as Map<String, dynamic>;
    } catch (e) {
      return {'error': e.toString()};
    }
  }

  Future<Map<String, dynamic>> runAudit({bool full = false, String trigger = 'MANUAL_TRIGGER'}) async {
    try {
      final req = await _httpClient.postUrl(Uri.parse('$baseUrl/api/workspace/audit'));
      req.headers.set(HttpHeaders.contentTypeHeader, 'application/json; charset=utf-8');
      req.add(utf8.encode(json.encode({'full': full, 'trigger': trigger})));
      final res = await req.close();
      final body = await res.transform(utf8.decoder).join();
      return json.decode(body) as Map<String, dynamic>;
    } catch (e) {
      return {'error': e.toString()};
    }
  }
}
