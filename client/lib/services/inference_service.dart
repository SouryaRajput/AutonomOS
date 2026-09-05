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

  Uri _getChatUri(String baseUrl) {
    final cleanUrl = baseUrl.trim().endsWith('/')
        ? baseUrl.trim().substring(0, baseUrl.trim().length - 1)
        : baseUrl.trim();
    final endpointUrl = cleanUrl.endsWith('/chat/completions')
        ? cleanUrl
        : '$cleanUrl/chat/completions';
    return Uri.parse(endpointUrl);
  }

  Future<Map<String, dynamic>> _sendWithHistory({
    required Uri uri,
    required String apiKey,
    required String model,
    required String systemPrompt,
    required String currentPrompt,
    List<Map<String, String>>? conversationHistory,
  }) async {
    final cleaned = <Map<String, String>>[];

    if (conversationHistory != null && conversationHistory.isNotEmpty) {
      final recent = conversationHistory.length > 10
          ? conversationHistory.sublist(conversationHistory.length - 10)
          : conversationHistory;
      for (final msg in recent) {
        final role = msg['role'] ?? 'user';
        final content = (msg['content'] ?? '').trim();
        if (content.isEmpty) continue;
        if (cleaned.isNotEmpty && cleaned.last['role'] == role) {
          cleaned.last['content'] = '${cleaned.last['content']}\n\n$content';
        } else {
          cleaned.add({'role': role, 'content': content});
        }
      }
    }

    if (cleaned.isNotEmpty && cleaned.last['role'] == 'user') {
      cleaned.last['content'] = '${cleaned.last['content']}\n\n$currentPrompt';
    } else {
      cleaned.add({'role': 'user', 'content': currentPrompt});
    }

    if (cleaned.isNotEmpty && cleaned.first['role'] != 'user') {
      cleaned.insert(0, {'role': 'user', 'content': 'Hello'});
    }

    try {
      final standardMessages = [
        {'role': 'system', 'content': systemPrompt},
        ...cleaned,
      ];
      return await _postRequest(uri, apiKey, model, standardMessages);
    } catch (_) {
      // Fallback: merge system prompt into the first user message
      final mergedMessages = <Map<String, String>>[];
      for (var i = 0; i < cleaned.length; i++) {
        if (i == 0) {
          mergedMessages.add({
            'role': 'user',
            'content': '$systemPrompt\n\n---\n${cleaned[0]['content']}',
          });
        } else {
          mergedMessages.add(cleaned[i]);
        }
      }
      return await _postRequest(uri, apiKey, model, mergedMessages);
    }
  }

  /// Fast-path: Manager answers simple questions, status queries, or greetings directly.
  Future<Map<String, dynamic>> generateDirectManagerAnswer({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    List<Map<String, String>>? conversationHistory,
    String? activeWorkingPath,
    String? projectName,
    List<String>? scannedFiles,
    Map<String, String>? keyFilePreviews,
  }) async {
    final uri = _getChatUri(baseUrl);
    final pName = projectName ?? (activeWorkingPath?.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last ?? 'Project');

    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Engineering Manager (Executive Orchestrator).');
    buffer.writeln('Target Project: `$pName` located at `${activeWorkingPath ?? "."}`');
    buffer.writeln('You have direct visibility into the project workspace files, architecture, and conversation history.');
    if (scannedFiles != null && scannedFiles.isNotEmpty) {
      buffer.writeln('\nWorkspace Files:');
      for (final f in scannedFiles.take(35)) {
        buffer.writeln('- `$f`');
      }
    }
    if (keyFilePreviews != null && keyFilePreviews.isNotEmpty) {
      buffer.writeln('\nKey Project File Snippets:');
      keyFilePreviews.forEach((k, v) {
        buffer.writeln('--- $k ---');
        buffer.writeln(v);
      });
    }
    buffer.writeln('\nYOUR TASK AS MANAGER:');
    buffer.writeln('1. Answer the user\'s question or message directly, clearly, concisely, and professionally.');
    buffer.writeln('2. Keep the response minimal, scannable, and high-signal (under 250 words). Avoid giant walls of text or multi-line ASCII art.');
    buffer.writeln('3. Reference workspace files, configurations, and conversation context accurately.');
    buffer.writeln('4. Respond strictly in pure, natural Markdown text. Never emit <tool_call> or pseudo-function JSON.');

    return await _sendWithHistory(
      uri: uri,
      apiKey: apiKey,
      model: model,
      systemPrompt: buffer.toString(),
      currentPrompt: userPrompt,
      conversationHistory: conversationHistory,
    );
  }

  /// Implementation Phase: Manager converts completed research findings into a clean, executive implementation roadmap.
  Future<Map<String, dynamic>> generateImplementationPlan({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    required String previousResearchOrContext,
    List<Map<String, String>>? conversationHistory,
    String? activeWorkingPath,
    String? projectName,
    List<String>? scannedFiles,
    Map<String, String>? keyFilePreviews,
  }) async {
    final uri = _getChatUri(baseUrl);
    final pName = projectName ?? (activeWorkingPath?.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last ?? 'Project');

    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Workforce Engineering Manager (Executive Orchestrator).');
    buffer.writeln('Target Project: `$pName` located at `${activeWorkingPath ?? "."}`');
    buffer.writeln('\nCRITICAL CONTEXT & MANDATE:');
    buffer.writeln('The research phase has completed and the user approved proceeding to implementation: "$userPrompt".');
    buffer.writeln('Present a clean, high-signal, executive implementation roadmap for the user.');
    buffer.writeln('The Programmer and QA Tester receive and execute the full technical code modifications directly into the workspace.');
    buffer.writeln('DO NOT dump giant code implementations or raw multi-page files in this user chat. Keep this chat summary clean, structured, and under 350 words.');
    if (previousResearchOrContext.isNotEmpty) {
      buffer.writeln('\nPREVIOUS WORKFORCE RESEARCH & TECHNICAL FINDINGS:');
      buffer.writeln(previousResearchOrContext);
    }
    if (keyFilePreviews != null && keyFilePreviews.isNotEmpty) {
      buffer.writeln('\nActive Workspace Key Files:');
      keyFilePreviews.forEach((k, v) {
        buffer.writeln('--- $k ---');
        buffer.writeln(v);
      });
    }
    buffer.writeln('\nDELIVER A CLEAN IMPLEMENTATION ROADMAP IN PURE MARKDOWN:');
    buffer.writeln('1. **Sprint Overview**: 1-2 sentences on the primary focus of Phase A (e.g. Security & Hardening).');
    buffer.writeln('2. **Engineering Issue Tickets (Linear / GitHub format)**:');
    buffer.writeln('   For EACH ticket (2-3 tickets maximum):');
    buffer.writeln('   - **Ticket ID & Title**: e.g., `[TASK-01] Production Security Headers & Config Hardening`');
    buffer.writeln('   - **Assignee**: Senior Programmer');
    buffer.writeln('   - **Priority & Estimate**: e.g., High • 2 Story Points');
    buffer.writeln('   - **Target Files**: Exact files to create or modify');
    buffer.writeln('   - **Acceptance Criteria**: Concrete checklist (`- [ ] ...`)');
    buffer.writeln('3. **QA Test Matrix**:');
    buffer.writeln('   A compact markdown table: `Test ID | Scope | Test Scenario | Expected Outcome`.');
    buffer.writeln('4. **Execution Handoff**:');
    buffer.writeln('   Confirm that the Programmer and QA Tester workers are dispatched to begin implementation.');
    buffer.writeln('\nCRITICAL OUTPUT CONSTRAINTS:');
    buffer.writeln('- Do NOT dump massive raw code snippets or full file implementations in this chat.');
    buffer.writeln('- Do NOT output any XML tags, tool calls, or pseudo function blocks.');
    buffer.writeln('- Respond strictly in pure, natural Markdown text.');

    return await _sendWithHistory(
      uri: uri,
      apiKey: apiKey,
      model: model,
      systemPrompt: buffer.toString(),
      currentPrompt: userPrompt,
      conversationHistory: conversationHistory,
    );
  }

  /// Summary Phase: Manager synthesizes previous findings or workspace state into a clean executive summary.
  Future<Map<String, dynamic>> generateSummaryOfFindings({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    required String previousResearchOrContext,
    List<Map<String, String>>? conversationHistory,
    String? activeWorkingPath,
    String? projectName,
    List<String>? scannedFiles,
    Map<String, String>? keyFilePreviews,
  }) async {
    final uri = _getChatUri(baseUrl);
    final pName = projectName ?? (activeWorkingPath?.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last ?? 'Project');

    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Workforce Engineering Manager (Executive Orchestrator).');
    buffer.writeln('Target Project: `$pName` located at `${activeWorkingPath ?? "."}`');
    buffer.writeln('\nCRITICAL CONTEXT & MANDATE:');
    buffer.writeln('The user requested a summary of findings, research, or current codebase analysis: "$userPrompt".');
    buffer.writeln('DO NOT repeat the research, DO NOT spin up crawlers, and DO NOT re-investigate from scratch.');
    buffer.writeln('Synthesize the findings, insights, and recommendations from the previous workforce activity and conversation history into a concise, high-impact executive summary.');

    if (previousResearchOrContext.isNotEmpty) {
      buffer.writeln('\nEXISTING RESEARCH FINDINGS & ANALYSIS:');
      buffer.writeln(previousResearchOrContext);
    }
    if (scannedFiles != null && scannedFiles.isNotEmpty) {
      buffer.writeln('\nWorkspace Files:');
      for (final f in scannedFiles.take(30)) {
        buffer.writeln('- `$f`');
      }
    }
    if (keyFilePreviews != null && keyFilePreviews.isNotEmpty) {
      buffer.writeln('\nKey Project File Snippets:');
      keyFilePreviews.forEach((k, v) {
        buffer.writeln('--- $k ---');
        buffer.writeln(v);
      });
    }

    buffer.writeln('\nROLE & AUDIENCE:');
    buffer.writeln('You are speaking directly to the human project owner. The user wants a clean, minimal, executive summary.');
    buffer.writeln('The giant technical data, schemas, and evidence packages have ALREADY been saved for the Programmer and QA Tester in `.autonomos/research/evidence/`.');
    buffer.writeln('DO NOT dump giant technical data or code onto the user!');
    buffer.writeln('\nCRITICAL OUTPUT CONSTRAINTS FOR USER-FACING SUMMARY:');
    buffer.writeln('1. NO CODE DUMPS: Do NOT output code snippets, class definitions (@dataclass), or programming language implementations. The Programmer handles code in the implementation phase.');
    buffer.writeln('2. NO ASCII ART OR BOX DIAGRAMS: Do NOT output giant text-box flowcharts (| IDEA | -> | BUILD |) or ASCII directory trees (|-- decisions/). Use concise bullet points or small markdown tables instead.');
    buffer.writeln('3. KEEP IT MINIMAL & HIGH-SIGNAL: The entire response must be concise (under 250-300 words). Focus strictly on key architectural takeaways, trade-offs, and product impact.');
    buffer.writeln('4. NO ROBOTIC SYSTEM ARTIFACTS: Do NOT output any XML tags, tool calls, or pseudo function blocks.');
    buffer.writeln('\nDELIVER A MINIMAL EXECUTIVE SUMMARY IN CLEAN MARKDOWN:');
    buffer.writeln('1. **Executive Overview**: 2-3 concise sentences summarizing what was analyzed.');
    buffer.writeln('2. **Key Architectural & UX Takeaways**: 3-5 high-signal bullet points or a compact table.');
    buffer.writeln('3. **Recommended Next Steps**: 2-3 high-level phases in 1 sentence each.');
    buffer.writeln('4. **Call to Action**: Conclude by asking:');
    buffer.writeln('   "Would you like me to proceed with creating a detailed implementation plan for the Programmer and QA Tester to begin executing Phase A?"');

    return await _sendWithHistory(
      uri: uri,
      apiKey: apiKey,
      model: model,
      systemPrompt: buffer.toString(),
      currentPrompt: userPrompt,
      conversationHistory: conversationHistory,
    );
  }

  /// Multi-Agent Phase 1: Manager analyzes request & forms a delegation brief for the Researcher.
  Future<Map<String, dynamic>> generateManagerPlan({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    List<Map<String, String>>? conversationHistory,
    String? activeWorkingPath,
    String? projectName,
    List<String>? scannedFiles,
    Map<String, String>? keyFilePreviews,
  }) async {
    final uri = _getChatUri(baseUrl);
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

    return await _sendWithHistory(
      uri: uri,
      apiKey: apiKey,
      model: model,
      systemPrompt: buffer.toString(),
      currentPrompt: userPrompt,
      conversationHistory: conversationHistory,
    );
  }

  /// Multi-Agent Phase 2: Researcher executes deep codebase investigation based on Manager's brief.
  Future<Map<String, dynamic>> generateResearcherFindings({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String managerBrief,
    List<Map<String, String>>? conversationHistory,
    String? activeWorkingPath,
    String? projectName,
    List<String>? scannedFiles,
    Map<String, String>? keyFilePreviews,
  }) async {
    final uri = _getChatUri(baseUrl);
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

    return await _sendWithHistory(
      uri: uri,
      apiKey: apiKey,
      model: model,
      systemPrompt: buffer.toString(),
      currentPrompt: managerBrief,
      conversationHistory: conversationHistory,
    );
  }

  /// Multi-Agent Phase 3: Manager synthesizes Researcher findings, summarizes for user, and proposes implementation plan.
  Future<Map<String, dynamic>> generateManagerSynthesis({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    required String managerPlan,
    required String researcherFindings,
    List<Map<String, String>>? conversationHistory,
    String? projectName,
    String? activeWorkingPath,
  }) async {
    final uri = _getChatUri(baseUrl);
    final pName = projectName ?? (activeWorkingPath?.split(Platform.pathSeparator).where((s) => s.isNotEmpty).last ?? 'Project');

    final buffer = StringBuffer();
    buffer.writeln('You are the AutonomOS Workforce Engineering Manager (Executive Orchestrator).');
    buffer.writeln('Target Project: `$pName` located at `${activeWorkingPath ?? "."}`');
    buffer.writeln('User\'s Request: "$userPrompt"');
    buffer.writeln('Your Initial Plan:\n$managerPlan\n');
    buffer.writeln('Specialist Researcher Findings Dossier (Technical Data):\n$researcherFindings\n');
    buffer.writeln('\nROLE & AUDIENCE:');
    buffer.writeln('You are presenting an executive synthesis directly to the human user / project owner.');
    buffer.writeln('The giant technical data, schemas, and evidence packages have ALREADY been saved for the Programmer and QA Tester in `.autonomos/research/evidence/`.');
    buffer.writeln('The user wants a clean, minimal, high-signal summary. Do NOT dump giant walls of text, code, or ASCII diagrams onto the user.');
    buffer.writeln('\nCRITICAL OUTPUT CONSTRAINTS FOR USER-FACING SYNTHESIS:');
    buffer.writeln('1. NO CODE DUMPS: Do NOT output code snippets, class schemas (@dataclass), function definitions, or SQL in this response. The Senior Programmer handles code execution in the workspace.');
    buffer.writeln('2. NO ASCII ART OR BOX DIAGRAMS: Do NOT output giant text-box flowcharts (| IDEA | -> | BUILD |) or ASCII directory trees (|-- decisions/). Use concise bullet points or small tables instead.');
    buffer.writeln('3. KEEP IT MINIMAL & HIGH-SIGNAL: Keep the response under 300 words total. Focus strictly on key architectural decisions, identified opportunities, and product impact.');
    buffer.writeln('4. NO ROBOTIC SYSTEM ARTIFACTS: Do NOT output any XML tags, tool calls, or pseudo function blocks.');
    buffer.writeln('\nDELIVER A MINIMAL EXECUTIVE RESPONSE IN CLEAN MARKDOWN:');
    buffer.writeln('1. **Executive Overview**: 2-3 concise sentences summarizing what was investigated and key takeaways.');
    buffer.writeln('2. **Key Findings**: 3-5 high-signal bullet points or a compact table summarizing architectural strengths and UX opportunities.');
    buffer.writeln('3. **Recommended Next Steps**: 2-3 high-level phases described in 1 sentence each.');
    buffer.writeln('4. **Call to Action**: Conclude by asking:');
    buffer.writeln('   "Would you like me to proceed with creating the implementation plan for the Programmer and QA Tester to begin executing Phase A?"');

    return await _sendWithHistory(
      uri: uri,
      apiKey: apiKey,
      model: model,
      systemPrompt: buffer.toString(),
      currentPrompt: userPrompt,
      conversationHistory: conversationHistory,
    );
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
