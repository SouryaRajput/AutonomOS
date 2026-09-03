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
    Map<String, String>? keyFilePreviews,
  }) {
    final buffer = StringBuffer();
    buffer.writeln('You are AutonomOS — an autonomous AI engineering workforce combining an Engineering Manager, Specialist Researcher, Senior Programmer, and QA Tester.');
    buffer.writeln('You are operating directly on the user\'s active codebase to fulfill their engineering goals, answer questions, research solutions, provide code implementations, and diagnose issues.');
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
      buffer.writeln('2. All file paths, dependencies, components, and recommendations MUST be relative to `$activeWorkingPath`.');
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

    if (keyFilePreviews != null && keyFilePreviews.isNotEmpty) {
      buffer.writeln('==================================================');
      buffer.writeln('PROJECT CONFIGURATION & KEY FILE SNIPPETS:');
      buffer.writeln('==================================================');
      keyFilePreviews.forEach((filePath, content) {
        buffer.writeln('--- File: `$filePath` ---');
        buffer.writeln(content);
        buffer.writeln('');
      });
    }

    buffer.writeln('==================================================');
    buffer.writeln('INSTRUCTIONS FOR COMPLETE & THOROUGH EXECUTION:');
    buffer.writeln('==================================================');
    buffer.writeln('1. When the user asks for RESEARCH or ADVICE (e.g. UX improvements, library recommendations, performance, architecture):');
    buffer.writeln('   - Actively analyze the project architecture, dependencies, and files.');
    buffer.writeln('   - Provide deep, concrete, actionable recommendations tailored specifically to their tech stack.');
    buffer.writeln('   - Include code snippets, library suggestions, UX patterns, and implementation steps.');
    buffer.writeln('   - DO NOT merely say "I will explore" or stop at an empty intent statement — deliver the complete, high-value analysis and solutions immediately.');
    buffer.writeln('2. When the user asks for CODE CHANGES or BUG FIXES:');
    buffer.writeln('   - Provide exact code snippets, surgical modifications, and explanations.');
    buffer.writeln('3. Format your response cleanly using GitHub Markdown (headings, bullet points, code blocks).');
    buffer.writeln('4. Maintain a professional, confident, engineering-focused tone.');
    buffer.writeln('');
    buffer.writeln('==================================================');
    buffer.writeln('RESPONSE STRUCTURE:');
    buffer.writeln('==================================================');
    buffer.writeln('Deliver a rich, structured engineering response containing:');
    buffer.writeln('• **Workforce Execution Summary**: Brief opening summarizing the Manager plan and work allocated across workers (Researcher, Crawlers, Programmer, QA).');
    buffer.writeln('• **Specialist Findings & Concrete Recommendations**: In-depth solutions, code examples, UI/UX patterns, and architectural recommendations tailored to the project.');
    buffer.writeln('• **Implementation Plan / Next Steps**: A concrete roadmap and actionable options for the user to proceed.');

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
    Map<String, String>? keyFilePreviews,
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
      keyFilePreviews: keyFilePreviews,
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

  /// Multi-Agent Phase 1: Manager analyzes request & forms a delegation brief for the Researcher.
  Future<Map<String, dynamic>> generateManagerPlan({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    String? activeWorkingPath,
    String? projectName,
    List<String>? scannedFiles,
    Map<String, String>? keyFilePreviews,
  }) async {
    final cleanUrl = baseUrl.trim().endsWith('/')
        ? baseUrl.trim().substring(0, baseUrl.trim().length - 1)
        : baseUrl.trim();
    final endpointUrl = cleanUrl.endsWith('/chat/completions')
        ? cleanUrl
        : '$cleanUrl/chat/completions';
    final uri = Uri.parse(endpointUrl);

    final pName = projectName ?? (activeWorkingPath?.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last ?? 'Project');

    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Workforce Engineering Manager (Executive Orchestrator).');
    buffer.writeln('You have received an engineering goal from the user: "$userPrompt"');
    buffer.writeln('Target Project: `$pName` located at `${activeWorkingPath ?? "."}`');
    if (scannedFiles != null && scannedFiles.isNotEmpty) {
      buffer.writeln('Workspace Files:');
      for (final f in scannedFiles.take(40)) {
        buffer.writeln('- $f');
      }
    }
    if (keyFilePreviews != null && keyFilePreviews.isNotEmpty) {
      buffer.writeln('\nKey Project File Snippets:');
      keyFilePreviews.forEach((k, v) {
        buffer.writeln('--- $k ---');
        buffer.writeln(v);
      });
    }
    buffer.writeln('\nYOUR TASK AS MANAGER IN PHASE 1:');
    buffer.writeln('1. Formulate a crisp, multi-phase execution plan for this goal.');
    buffer.writeln('2. Formulate a specific, structured Task Delegation Brief for your Specialist Researcher worker.');
    buffer.writeln('3. Specify exactly which 3-5 technical questions and codebase areas the Researcher must investigate.');
    buffer.writeln('\nCRITICAL OUTPUT CONSTRAINTS:');
    buffer.writeln('- Do NOT output any XML tags, tool calls, or pseudo function blocks (e.g. <tool_call>, FUNCTIONS.EXECUTE_SHELL).');
    buffer.writeln('- All project files and context have already been inspected and supplied above.');
    buffer.writeln('- Respond strictly in pure, natural Markdown text.');

    final messages = [
      {'role': 'system', 'content': 'You are the AutonomOS Workforce Engineering Manager. Respond strictly in pure Markdown prose. Never emit <tool_call> or pseudo-function JSON.'},
      {'role': 'user', 'content': buffer.toString()},
    ];

    return await _postRequest(uri, apiKey, model, messages);
  }

  /// Multi-Agent Phase 2: Researcher executes deep codebase investigation based on Manager's brief.
  Future<Map<String, dynamic>> generateResearcherFindings({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String managerBrief,
    String? activeWorkingPath,
    String? projectName,
    List<String>? scannedFiles,
    Map<String, String>? keyFilePreviews,
  }) async {
    final cleanUrl = baseUrl.trim().endsWith('/')
        ? baseUrl.trim().substring(0, baseUrl.trim().length - 1)
        : baseUrl.trim();
    final endpointUrl = cleanUrl.endsWith('/chat/completions')
        ? cleanUrl
        : '$cleanUrl/chat/completions';
    final uri = Uri.parse(endpointUrl);

    final pName = projectName ?? (activeWorkingPath?.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last ?? 'Project');

    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Specialist Researcher.');
    buffer.writeln('Your Engineering Manager has assigned you the following investigation task:\n');
    buffer.writeln(managerBrief);
    buffer.writeln('\nTarget Project: `$pName` in `${activeWorkingPath ?? "."}`');
    if (keyFilePreviews != null && keyFilePreviews.isNotEmpty) {
      buffer.writeln('\nInspected Codebase Configurations & Files:');
      keyFilePreviews.forEach((k, v) {
        buffer.writeln('--- File: $k ---');
        buffer.writeln(v);
      });
    }
    buffer.writeln('\nYOUR TASK AS SPECIALIST RESEARCHER:');
    buffer.writeln('1. Perform a deep, thorough technical analysis tailored specifically to this codebase and stack.');
    buffer.writeln('2. Address every question raised by the Manager.');
    buffer.writeln('3. Provide concrete code patterns, library suggestions, UX/UI improvements, performance optimizations, and exact implementation recommendations.');
    buffer.writeln('4. Return a comprehensive Research Findings Dossier in pure Markdown.');
    buffer.writeln('\nCRITICAL OUTPUT CONSTRAINTS:');
    buffer.writeln('- Do NOT output any XML tags, tool calls, or pseudo function blocks (e.g. <tool_call>, FUNCTIONS.EXECUTE_SHELL).');
    buffer.writeln('- All project files and context have already been inspected and supplied above.');
    buffer.writeln('- Respond strictly in pure, natural Markdown text.');

    final messages = [
      {'role': 'system', 'content': 'You are the AutonomOS Specialist Researcher. Provide rigorous, deep, concrete technical analysis in pure Markdown. Never emit <tool_call> or pseudo-function JSON.'},
      {'role': 'user', 'content': buffer.toString()},
    ];

    return await _postRequest(uri, apiKey, model, messages);
  }

  /// Multi-Agent Phase 3: Manager synthesizes Researcher findings, summarizes for user, and proposes implementation plan.
  Future<Map<String, dynamic>> generateManagerSynthesis({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    required String managerPlan,
    required String researcherFindings,
    String? projectName,
  }) async {
    final cleanUrl = baseUrl.trim().endsWith('/')
        ? baseUrl.trim().substring(0, baseUrl.trim().length - 1)
        : baseUrl.trim();
    final endpointUrl = cleanUrl.endsWith('/chat/completions')
        ? cleanUrl
        : '$cleanUrl/chat/completions';
    final uri = Uri.parse(endpointUrl);

    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Workforce Engineering Manager (Head of the Workforce).');
    buffer.writeln('User\'s Request: "$userPrompt"');
    buffer.writeln('Your Initial Plan:\n$managerPlan\n');
    buffer.writeln('Specialist Researcher Findings Dossier:\n$researcherFindings\n');
    buffer.writeln('YOUR TASK AS MANAGER:');
    buffer.writeln('Synthesize these findings and deliver a complete, highly structured response to the user with:');
    buffer.writeln('1. **Workforce Execution Summary**: Briefly explain how you planned the work and what the Researcher investigated.');
    buffer.writeln('2. **Key Findings & Recommendations**: The core concrete recommendations, code snippets, architectural improvements, and UI/UX patterns tailored to the project.');
    buffer.writeln('3. **Proposed Implementation Plan**: A clear step-by-step roadmap for implementing these improvements.');
    buffer.writeln('4. **Call to Action**: Conclude by asking the user: "Would you like me to proceed with creating an implementation plan for the Programmer and QA Tester to begin implementing these changes?"');
    buffer.writeln('\nCRITICAL OUTPUT CONSTRAINTS:');
    buffer.writeln('- Do NOT output any XML tags, tool calls, or pseudo function blocks (e.g. <tool_call>, FUNCTIONS.EXECUTE_SHELL).');
    buffer.writeln('- Respond strictly in pure, natural Markdown text.');

    final messages = [
      {'role': 'system', 'content': 'You are the AutonomOS Workforce Engineering Manager. Communicate directly, professionally, and clearly with the user in pure Markdown. Never emit <tool_call> or pseudo-function JSON.'},
      {'role': 'user', 'content': buffer.toString()},
    ];

    return await _postRequest(uri, apiKey, model, messages);
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
