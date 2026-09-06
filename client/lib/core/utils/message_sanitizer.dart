import 'dart:convert';

/// Extracted message payload separating user-facing narrative from internal tool executions.
class ExtractedMessagePayload {
  final String userFacingNarrative;
  final List<Map<String, dynamic>> internalToolCalls;

  const ExtractedMessagePayload({
    required this.userFacingNarrative,
    this.internalToolCalls = const [],
  });
}

/// Strict architectural separation adapter between:
/// 1. USER-FACING CONVERSATION CHANNEL (assistant narrative, progress, results)
/// 2. INTERNAL EXECUTION CHANNEL (tool calls, tool arguments, raw JSON payloads, worker events)
class MessageSanitizer {
  MessageSanitizer._();

  static const Set<String> _toolActionKeys = {
    'action',
    'tool',
    'tool_id',
    'tool_name',
    'function',
    'function_call',
    'tool_calls',
    'command',
    'crawler_action',
    'research_action',
  };

  static const Set<String> _knownToolNames = {
    'list_directory',
    'read_file',
    'write_file',
    'create_file',
    'delete_file',
    'file_exists',
    'stat_file',
    'run_command',
    'exec',
    'web_search',
    'web_fetch',
    'crawler_allocation',
    'spawn_crawler',
    'execute_crawler_task',
    'evidence_evaluation',
    'plan_update',
    'create_task',
    'assign_task',
    'renegotiate_plan',
  };

  static bool isInternalToolPayload(dynamic obj) {
    if (obj is Map<String, dynamic>) {
      final keysLower = obj.keys.map((k) => k.toLowerCase()).toSet();
      if (keysLower.intersection(_toolActionKeys).isNotEmpty) {
        final actionVal = (obj['action'] ?? obj['tool'] ?? obj['tool_id'] ?? obj['name'] ?? obj['command'] ?? '').toString().toLowerCase();
        if (_knownToolNames.contains(actionVal)) return true;
        if (keysLower.contains('arguments') || keysLower.contains('parameters') || keysLower.contains('path')) return true;
        if (keysLower.contains('tool') || keysLower.contains('tool_id') || keysLower.contains('tool_calls')) return true;
      }

      if (obj['type'] == 'function' && obj.containsKey('function')) return true;
      if (obj.containsKey('function_call')) return true;
      if (obj.containsKey('tool_calls') && obj['tool_calls'] is List) return true;
    }
    return false;
  }

  static bool isInternalToolJson(String text) {
    final trimmed = text.trim();
    if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return false;

    try {
      final decoded = json.decode(trimmed);
      return isInternalToolPayload(decoded);
    } catch (_) {
      return false;
    }
  }

  /// Separates raw assistant response into clean user-facing narrative and internal tool calls.
  static ExtractedMessagePayload extractUserFacingNarrative(String rawText) {
    if (rawText.trim().isEmpty) {
      return const ExtractedMessagePayload(userFacingNarrative: '');
    }

    String text = rawText.trim();
    final extractedTools = <Map<String, dynamic>>[];

    // 0. Strip XML/pseudo-tool calls, function and parameter call artifacts
    text = text.replaceAll(RegExp(r'<function=[^>]*>[\s\S]*?</function>', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'<function\b[^>]*>[\s\S]*?</function>', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'<parameter=[^>]*>[\s\S]*?</parameter>', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'<parameter\b[^>]*>[\s\S]*?</parameter>', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'<tool_call\b[^>]*>[\s\S]*?</tool_call>', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'<function_call\b[^>]*>[\s\S]*?</function_call>', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'<tool_response\b[^>]*>[\s\S]*?</tool_response>', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'FUNCTIONS\.[A-Z_]+\s*\{[\s\S]*?\}', caseSensitive: false), '');
    text = text.replaceAll(RegExp(r'</?(?:function|parameter|tool_call|function_call|tool_response)(?:=[^>]*)?>', caseSensitive: false), '');
    // Strip trailing unclosed tool tags if model output was cut off
    text = text.replaceAll(RegExp(r'<(?:function|parameter|tool_call|function_call)\b[^>]*>[\s\S]*$', caseSensitive: false), '');

    // 1. Check and extract fenced code blocks containing internal tool JSON
    final codeBlockRegex = RegExp(r'```(?:json|tool|tool_call|function_call)?\s*([\s\S]*?)\s*```', caseSensitive: false);
    text = text.replaceAllMapped(codeBlockRegex, (match) {
      final fenceContent = (match.group(1) ?? '').trim();
      if (isInternalToolJson(fenceContent)) {
        try {
          final decoded = json.decode(fenceContent);
          if (decoded is Map<String, dynamic>) extractedTools.add(decoded);
        } catch (_) {}
        return ''; // Strip internal tool code block from user-facing conversation
      }
      return match.group(0)!; // Preserve normal code blocks
    });

    // 2. Extract embedded standalone raw JSON objects { ... } using balanced brace parser
    final cleanedSegments = <String>[];
    int idx = 0;
    final length = text.length;

    while (idx < length) {
      final startBrace = text.indexOf('{', idx);
      if (startBrace == -1) {
        cleanedSegments.add(text.substring(idx));
        break;
      }

      cleanedSegments.add(text.substring(idx, startBrace));

      int braceDepth = 0;
      int endBrace = -1;
      bool inString = false;
      bool escape = false;

      for (int i = startBrace; i < length; i++) {
        final char = text[i];
        if (escape) {
          escape = false;
          continue;
        }
        if (char == '\\') {
          escape = true;
          continue;
        }
        if (char == '"') {
          inString = !inString;
          continue;
        }
        if (!inString) {
          if (char == '{') {
            braceDepth++;
          } else if (char == '}') {
            braceDepth--;
            if (braceDepth == 0) {
              endBrace = i;
              break;
            }
          }
        }
      }

      if (endBrace != -1) {
        final candidateJson = text.substring(startBrace, endBrace + 1);
        if (isInternalToolJson(candidateJson)) {
          try {
            final decoded = json.decode(candidateJson);
            if (decoded is Map<String, dynamic>) extractedTools.add(decoded);
          } catch (_) {}
          idx = endBrace + 1;
          continue;
        } else {
          cleanedSegments.add(candidateJson);
          idx = endBrace + 1;
          continue;
        }
      } else {
        cleanedSegments.add(text.substring(startBrace));
        break;
      }
    }

    String resultNarrative = cleanedSegments.join('');

    // 3. Strip robotic / internal metadata lines if present
    resultNarrative = resultNarrative
        .replaceAll(RegExp(r'✓ Project understood\n?', caseSensitive: false), '')
        .replaceAll(RegExp(r'✓ Project map created[^\n]*\n?', caseSensitive: false), '')
        .replaceAll(RegExp(r'✓ Work plan prepared[^\n]*\n?', caseSensitive: false), '')
        .replaceAll(RegExp(r'⏸ Workers inactive[^\n]*\n?', caseSensitive: false), '')
        .replaceAll(RegExp(r'Decision:\s*"?[^\n"]*"?\n?', caseSensitive: false), '')
        .replaceAll(RegExp(r'Reason:\s*"?[^\n"]*"?\n?', caseSensitive: false), '')
        .replaceAll(RegExp(r'Planned TASK-\d+[^\n]*\n?', caseSensitive: false), '')
        .replaceAll(RegExp(r'Context selected:\s*(\n\s*[-*]\s*`?[^\n]+`?)+', caseSensitive: false), '');

    // Normalize spacing
    resultNarrative = resultNarrative.replaceAll(RegExp(r'\n{3,}'), '\n\n').trim();

    return ExtractedMessagePayload(
      userFacingNarrative: resultNarrative,
      internalToolCalls: extractedTools,
    );
  }
}
