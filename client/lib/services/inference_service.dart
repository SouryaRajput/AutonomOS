import 'dart:convert';
import 'dart:io';

/// Direct real inference service connecting Flutter directly to OpenAI-compatible custom endpoints.
/// Configured for AutonomOS Manager Dry-Run & Orchestration Observation Mode.
class InferenceService {
  final HttpClient _httpClient = HttpClient()
    ..connectionTimeout = const Duration(seconds: 15);

  static String buildManagerSystemPrompt({
    String? activeWorkingPath,
    String? projectName,
    String? projectMapMarkdown,
    List<String>? scannedFiles,
  }) {
    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Workforce Manager Agent — the HEAD OF THE WORKFORCE.');
    buffer.writeln('You are NOT a programmer, coder, tester, or general-purpose executor.');
    buffer.writeln('You do NOT write project code, implement features, or execute tests directly.');
    buffer.writeln('');
    buffer.writeln('==================================================');
    buffer.writeln('ACTIVE WORKSPACE BOUNDARY (TARGET PROJECT)');
    buffer.writeln('==================================================');

    if (activeWorkingPath != null && activeWorkingPath.isNotEmpty) {
      final pName = projectName ?? activeWorkingPath.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last;
      buffer.writeln('Active Project Name: `$pName`');
      buffer.writeln('Active Workspace Directory: `$activeWorkingPath`');
      buffer.writeln('');
      buffer.writeln('CRITICAL BOUNDARY ENFORCEMENT:');
      buffer.writeln('1. You are operating EXCLUSIVELY on the project inside `$activeWorkingPath`.');
      buffer.writeln('2. Do NOT inspect, analyze, or refer to the parent AutonomOS tool host codebase unless the user explicitly chose that folder.');
      buffer.writeln('3. All file paths, dependencies, components, and tasks MUST be relative to `$activeWorkingPath`.');
      buffer.writeln('');
    }

    if (projectMapMarkdown != null && projectMapMarkdown.isNotEmpty && !projectMapMarkdown.contains('Not Initialized')) {
      buffer.writeln('==================================================');
      buffer.writeln('PROJECT MAP & ARCHITECTURE FOR ACTIVE WORKSPACE:');
      buffer.writeln('==================================================');
      buffer.writeln(projectMapMarkdown);
      buffer.writeln('');
    } else if (scannedFiles != null && scannedFiles.isNotEmpty) {
      buffer.writeln('==================================================');
      buffer.writeln('INSPECTED FILES IN ACTIVE WORKSPACE:');
      buffer.writeln('==================================================');
      for (final f in scannedFiles.take(60)) {
        buffer.writeln('- `$f`');
      }
      buffer.writeln('');
    }

    buffer.writeln('==================================================');
    buffer.writeln('ORCHESTRATION MODE: MANAGER CONVERSATION');
    buffer.writeln('Specialized workers (Researcher, Programmer, Tester) are provisioned on demand as needed by the plan.');
    buffer.writeln('');
    buffer.writeln('CRITICAL INSTRUCTION FOR OUTPUT:');
    buffer.writeln('1. Communicate directly, concisely, and naturally as an autonomous engineering Manager having a conversation with the user.');
    buffer.writeln('2. Do NOT dump raw checklists ("Planned TASK-01", "TASK-02"), raw task dependency graphs, internal state IDs, or debug logs.');
    buffer.writeln('3. Do NOT dump a raw list of inspected files or "Context selected:" headers. State naturally what workspace areas you inspected.');
    buffer.writeln('4. Describe your execution plan conversationally (e.g. "I’ve prepared an execution plan covering the main features and verification.").');
    buffer.writeln('5. State which specialized worker you are provisioning or dispatching to execute the plan.');
    buffer.writeln('6. Keep the response crisp, technical, and natural. Do NOT use Jira or checklist-style formatting.');
    buffer.writeln('');

    return buffer.toString();
  }

  Future<Map<String, dynamic>> generateCompletion({
    required String baseUrl,
    required String apiKey,
    required String model,
    required List<Map<String, String>> messages,
    String? activeWorkingPath,
    String? projectName,
    String? projectMapMarkdown,
    List<String>? scannedFiles,
  }) async {
    final cleanUrl = baseUrl.trim().endsWith('/')
        ? baseUrl.trim().substring(0, baseUrl.trim().length - 1)
        : baseUrl.trim();
    final endpointUrl = cleanUrl.endsWith('/chat/completions')
        ? cleanUrl
        : '$cleanUrl/chat/completions';
    final uri = Uri.parse(endpointUrl);

    final systemPrompt = buildManagerSystemPrompt(
      activeWorkingPath: activeWorkingPath,
      projectName: projectName,
      projectMapMarkdown: projectMapMarkdown,
      scannedFiles: scannedFiles,
    );

    // 1. Clean and enforce strict message alternation (user -> assistant -> user)
    final cleaned = <Map<String, String>>[];
    for (final msg in messages) {
      final role = msg['role'] ?? 'user';
      final content = (msg['content'] ?? '').trim();
      if (content.isEmpty) continue;

      if (cleaned.isNotEmpty && cleaned.last['role'] == role) {
        cleaned.last['content'] = '${cleaned.last['content']}\n\n$content';
      } else {
        cleaned.add({'role': role, 'content': content});
      }
    }

    if (cleaned.isEmpty) {
      cleaned.add({'role': 'user', 'content': 'Hello'});
    }

    // Ensure the conversation starts with a user message
    if (cleaned.first['role'] != 'user') {
      cleaned.insert(0, {'role': 'user', 'content': 'Hello'});
    }

    // Attempt 1: Standard OpenAI format with system role
    try {
      final standardMessages = [
        {'role': 'system', 'content': systemPrompt},
        ...cleaned,
      ];
      return await _postRequest(uri, apiKey, model, standardMessages);
    } catch (firstError) {
      // Attempt 2: If endpoint rejects system role, merge into first user prompt
      try {
        final mergedMessages = <Map<String, String>>[];
        for (var i = 0; i < cleaned.length; i++) {
          if (i == 0) {
            mergedMessages.add({
              'role': 'user',
              'content': '$systemPrompt\n\n---\nUser: ${cleaned[0]['content']}',
            });
          } else {
            mergedMessages.add(cleaned[i]);
          }
        }
        return await _postRequest(uri, apiKey, model, mergedMessages);
      } catch (secondError) {
        rethrow;
      }
    }
  }

  Future<Map<String, dynamic>> _postRequest(
    Uri uri,
    String apiKey,
    String model,
    List<Map<String, String>> payloadMessages,
  ) async {
    final request = await _httpClient.postUrl(uri);
    request.headers.set(HttpHeaders.contentTypeHeader, 'application/json; charset=utf-8');
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    request.headers.set('User-Agent', 'AutonomOS-Client/1.0');
    if (apiKey.trim().isNotEmpty) {
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer ${apiKey.trim()}');
    }

    final payload = {
      'model': model.trim().isNotEmpty ? model.trim() : 'glm-5.3',
      'messages': payloadMessages,
      'temperature': 0.3,
      'stream': false,
    };

    final bodyBytes = utf8.encode(json.encode(payload));
    request.contentLength = bodyBytes.length;
    request.add(bodyBytes);

    final response = await request.close().timeout(const Duration(seconds: 45));
    final responseBody = await response.transform(utf8.decoder).join();

    if (response.statusCode >= 400) {
      try {
        final errJson = json.decode(responseBody);
        final errMsg = errJson['error']?['message'] ?? errJson['message'] ?? responseBody;
        throw Exception('HTTP ${response.statusCode}: $errMsg');
      } catch (e) {
        if (e is Exception && !e.toString().contains('FormatException')) rethrow;
        throw Exception('HTTP ${response.statusCode}: $responseBody');
      }
    }

    final data = json.decode(responseBody);
    final choices = data['choices'] as List?;
    if (choices == null || choices.isEmpty) {
      throw Exception('No completion choices returned by model.');
    }

    final firstChoice = choices.first as Map<String, dynamic>;
    final message = firstChoice['message'] as Map<String, dynamic>?;
    final text = message?['content'] as String? ?? firstChoice['text'] as String? ?? '';

    final usage = data['usage'] as Map<String, dynamic>?;
    final promptTokens = usage?['prompt_tokens'] as int? ?? 0;
    final completionTokens = usage?['completion_tokens'] as int? ?? 0;

    return {
      'content': text.trim(),
      'promptTokens': promptTokens,
      'completionTokens': completionTokens,
      'model': data['model'] ?? model,
    };
  }
}
