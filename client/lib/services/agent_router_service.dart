import 'dart:convert';
import 'dart:io';

enum UserIntent {
  proceedWithPlan,
  summarizeFindings,
  simpleQuestion,
  codeImplementation,
  complexCreation,
  complexResearch,
}

class AgentRoutingDecision {
  final UserIntent intent;
  final int workerCount;
  final List<String> workers;
  final String reasoning;

  const AgentRoutingDecision({
    required this.intent,
    required this.workerCount,
    required this.workers,
    required this.reasoning,
  });

  factory AgentRoutingDecision.fromJson(Map<String, dynamic> json) {
    final intentStr = json['intent'] as String? ?? 'simpleQuestion';
    final count = json['workerCount'] as int? ?? (json['workers'] as List?)?.length ?? 1;
    final workersList = (json['workers'] as List?)?.map((e) => e.toString()).toList() ?? ['Manager'];
    final reasoningStr = json['reasoning'] as String? ?? '';

    UserIntent parsedIntent;
    switch (intentStr) {
      case 'complexCreation':
        parsedIntent = UserIntent.complexCreation;
        break;
      case 'codeImplementation':
        parsedIntent = UserIntent.codeImplementation;
        break;
      case 'complexResearch':
        parsedIntent = UserIntent.complexResearch;
        break;
      case 'proceedWithPlan':
        parsedIntent = UserIntent.proceedWithPlan;
        break;
      case 'summarizeFindings':
        parsedIntent = UserIntent.summarizeFindings;
        break;
      case 'simpleQuestion':
      default:
        parsedIntent = UserIntent.simpleQuestion;
        break;
    }

    return AgentRoutingDecision(
      intent: parsedIntent,
      workerCount: count,
      workers: workersList,
      reasoning: reasoningStr,
    );
  }

  Map<String, dynamic> toJson() => {
    'intent': intent.name,
    'workerCount': workerCount,
    'workers': workers,
    'reasoning': reasoning,
  };
}

/// Fallback deterministic intent classifier when AI is offline or times out.
/// Intelligently analyzes creation requests, research requests, code fixes, and questions.
AgentRoutingDecision classifyUserIntentSmart({
  required String prompt,
  required String? lastAssistantMessage,
}) {
  final clean = prompt.trim();
  final lower = clean.toLowerCase();

  // 0. Slash Commands
  if (clean.startsWith('/research')) {
    return const AgentRoutingDecision(
      intent: UserIntent.complexResearch,
      workerCount: 2,
      workers: ['Manager', 'Researcher'],
      reasoning: 'Explicit /research command invoked',
    );
  }
  if (clean.startsWith('/plan')) {
    return const AgentRoutingDecision(
      intent: UserIntent.proceedWithPlan,
      workerCount: 3,
      workers: ['Manager', 'Programmer', 'Tester'],
      reasoning: 'Explicit /plan command invoked',
    );
  }

  // 1. Plan proceeding affirmations
  final hasPendingPlanProposal = lastAssistantMessage != null &&
      (lastAssistantMessage.contains('Would you like me to proceed with creating a detailed implementation plan') ||
          lastAssistantMessage.contains('proceed with creating an implementation plan') ||
          lastAssistantMessage.contains('Detailed Implementation Plan') ||
          lastAssistantMessage.contains('implementation plan for'));

  final isProceedAffirmative = lower == 'proceed' ||
      lower == 'yes' ||
      lower == 'yep' ||
      lower == 'sure' ||
      lower == 'go ahead' ||
      lower == 'continue' ||
      lower == "let's do it" ||
      lower.contains('proceed with') ||
      (lower.contains('proceed') && lower.contains('plan'));

  if (hasPendingPlanProposal && isProceedAffirmative || isProceedAffirmative) {
    return const AgentRoutingDecision(
      intent: UserIntent.proceedWithPlan,
      workerCount: 3,
      workers: ['Manager', 'Programmer', 'Tester'],
      reasoning: 'User confirmed proceeding with implementation plan',
    );
  }

  // 2. Summary / Recap requests
  final isSummaryRequest = lower.contains('summary') ||
      lower.contains('summarize') ||
      lower.contains('summarise') ||
      lower.contains('recap') ||
      lower == 'tldr' ||
      lower == 'tl;dr' ||
      lower.contains('what did you find');

  if (isSummaryRequest) {
    return const AgentRoutingDecision(
      intent: UserIntent.summarizeFindings,
      workerCount: 1,
      workers: ['Manager'],
      reasoning: 'User requested a summary of findings',
    );
  }

  // 3. Greetings & Status checks
  final isGreetingOrStatus = RegExp(
    r'^(hi|hello|hey|greetings|thanks|thank you|good morning|status|what is running|progress)[\s!.]*$',
    caseSensitive: false,
  ).hasMatch(clean);

  if (isGreetingOrStatus) {
    return const AgentRoutingDecision(
      intent: UserIntent.simpleQuestion,
      workerCount: 1,
      workers: ['Manager'],
      reasoning: 'Greeting or status check',
    );
  }

  // 4. Creation / Building requests (All 4 workers required: Manager, Researcher, Programmer, Tester)
  // Detects: "Use latest tech stack to make a portfolio website", "Create a new website", "build a real-time chat app", etc.
  final hasCreationVerb = RegExp(
    r'\b(create|build|develop|make|implement|generate|design|craft|setup|set up|construct)\b',
    caseSensitive: false,
  ).hasMatch(clean);

  final hasSoftwareEntity = RegExp(
    r'\b(website|portfolio|web app|webapp|application|app|dashboard|platform|landing page|frontend|backend|fullstack|full-stack|system|ui|component|navbar|game)\b',
    caseSensitive: false,
  ).hasMatch(clean);

  final hasTechStackImperative = RegExp(
    r'\b(react|next\.js|nextjs|vue|svelte|angular|flutter|three\.js|threejs|tailwind|node|express)\b',
    caseSensitive: false,
  ).hasMatch(clean);

  // If prompt asks to create/build/make a software entity, or use a tech stack to make something
  if (hasCreationVerb && hasSoftwareEntity || (hasTechStackImperative && hasCreationVerb)) {
    return const AgentRoutingDecision(
      intent: UserIntent.complexCreation,
      workerCount: 4,
      workers: ['Manager', 'Researcher', 'Programmer', 'Tester'],
      reasoning: 'Software creation/engineering request requires full workforce (4 workers)',
    );
  }

  // 5. Explicit Research & Discovery (Manager + Researcher, 2 workers, NO coding)
  // Detects market research, business complaint analysis, competitor discovery without building
  final hasResearchVerb = RegExp(
    r'\b(research|investigate|analyze|analyse|audit|explore|find out|deep dive)\b',
    caseSensitive: false,
  ).hasMatch(clean);

  final hasMarketOrBusinessDiscoveryTopic = lower.contains('complain') ||
      lower.contains('pain point') ||
      lower.contains('who to pitch') ||
      lower.contains('pitch to sell') ||
      lower.contains('find emails') ||
      lower.contains('reddit profiles') ||
      lower.contains('instagram profiles') ||
      lower.contains('pricing model');

  if (hasResearchVerb || hasMarketOrBusinessDiscoveryTopic) {
    return const AgentRoutingDecision(
      intent: UserIntent.complexResearch,
      workerCount: 2,
      workers: ['Manager', 'Researcher'],
      reasoning: 'Domain/market research inquiry requires Manager and Researcher (2 workers)',
    );
  }

  // 6. Targeted Code Fixes / Minor Modifications (3 workers: Manager, Programmer, Tester)
  final isCodeFix = RegExp(
    r'\b(fix bug|fix error|refactor|modify file|edit file|update code|fix alignment|debug)\b',
    caseSensitive: false,
  ).hasMatch(clean);

  if (isCodeFix) {
    return const AgentRoutingDecision(
      intent: UserIntent.codeImplementation,
      workerCount: 3,
      workers: ['Manager', 'Programmer', 'Tester'],
      reasoning: 'Targeted code fix requires Manager, Programmer, and QA Tester (3 workers)',
    );
  }

  // 7. Informational Questions (1 worker: Manager)
  if (clean.endsWith('?') || RegExp(r'^(what|where|how|why|who|when|which)\b', caseSensitive: false).hasMatch(clean)) {
    return const AgentRoutingDecision(
      intent: UserIntent.simpleQuestion,
      workerCount: 1,
      workers: ['Manager'],
      reasoning: 'Direct informational question handled by Manager (1 worker)',
    );
  }

  // Default fallback: If substantial text mentions web/app/software, route to complexCreation, else complexResearch
  if (hasSoftwareEntity) {
    return const AgentRoutingDecision(
      intent: UserIntent.complexCreation,
      workerCount: 4,
      workers: ['Manager', 'Researcher', 'Programmer', 'Tester'],
      reasoning: 'Software entity mentioned in request, routing to full workforce (4 workers)',
    );
  }

  return const AgentRoutingDecision(
    intent: UserIntent.simpleQuestion,
    workerCount: 1,
    workers: ['Manager'],
    reasoning: 'General conversational inquiry handled by Manager',
  );
}

/// Service that uses the LLM to intelligently analyze user intent and determine
/// the exact number and roles of autonomous agents required for the request.
class AgentRouterService {
  final HttpClient _httpClient = HttpClient()
    ..connectionTimeout = const Duration(seconds: 5);

  Future<AgentRoutingDecision> routeUserIntentWithAi({
    required String baseUrl,
    required String apiKey,
    required String model,
    required String userPrompt,
    String? lastAssistantMessage,
  }) async {
    final cleanPrompt = userPrompt.trim();
    if (cleanPrompt.isEmpty) {
      return classifyUserIntentSmart(prompt: cleanPrompt, lastAssistantMessage: lastAssistantMessage);
    }

    // Check immediate slash commands / quick proceed without waiting for network
    if (cleanPrompt.startsWith('/') ||
        cleanPrompt.toLowerCase() == 'proceed' ||
        cleanPrompt.toLowerCase() == 'yes') {
      return classifyUserIntentSmart(prompt: cleanPrompt, lastAssistantMessage: lastAssistantMessage);
    }

    if (baseUrl.trim().isEmpty) {
      return classifyUserIntentSmart(prompt: cleanPrompt, lastAssistantMessage: lastAssistantMessage);
    }

    final systemPrompt = '''
You are the AutonomOS Autonomous Workforce Orchestrator & Intent Router.
Given a user's prompt, analyze their intent and determine EXACTLY which and how many autonomous workers are required.

AutonomOS has 4 specialized autonomous workers:
1. "Manager" (Executive Orchestrator) - Always engaged for architecture, planning, and final delivery synthesis.
2. "Researcher" (Specialist) - Researches libraries, frameworks, design patterns, APIs, and domain landscape.
3. "Programmer" (Senior Engineer) - Writes source code, creates components, and deploys files to workspace.
4. "Tester" (QA Engineer) - Validates code syntax, checks HTML/CSS/JS integrity, runs tests, and verifies quality.

ROUTING PROFILES:
- "complexCreation" (4 workers: ["Manager", "Researcher", "Programmer", "Tester"]):
  Trigger when the user wants to create, build, develop, generate, or overhaul an app, website, portfolio, fullstack system, dashboard, component, or deliverable. E.g.: "Use React/Next.js to make a portfolio website", "Build a real-time chat app", "Create a landing page with 3D canvas".
- "codeImplementation" (3 workers: ["Manager", "Programmer", "Tester"]):
  Trigger for targeted code edits, bug fixes, adding a specific function/component to an existing codebase without deep research. E.g.: "Fix the login button alignment", "Add JWT validation to auth service".
- "complexResearch" (2 workers: ["Manager", "Researcher"]):
  Trigger for domain investigation, market research, business complaints analysis, lead generation, or technology comparison where NO CODE is to be written or deployed. E.g.: "Research what people complain about in their businesses to automate", "Find emails or profiles to pitch".
- "simpleQuestion" (1 worker: ["Manager"]):
  Trigger for direct questions, explanations, greetings, or status checks. E.g.: "What is React?", "How does Docker work?", "Hello".
- "proceedWithPlan" (3 workers: ["Manager", "Programmer", "Tester"]):
  Trigger when the user confirms or approves an implementation plan. E.g.: "Proceed", "Yes, go ahead".
- "summarizeFindings" (1 worker: ["Manager"]):
  Trigger when the user asks to summarize past research or findings. E.g.: "Summarize findings", "Recap".

Respond STRICTLY with a valid JSON object:
{
  "intent": "complexCreation" | "codeImplementation" | "complexResearch" | "simpleQuestion" | "proceedWithPlan" | "summarizeFindings",
  "workerCount": 1 | 2 | 3 | 4,
  "workers": ["Manager", ...],
  "reasoning": "brief explanation"
}
''';

    try {
      final cleanUrl = baseUrl.trim().endsWith('/')
          ? baseUrl.trim().substring(0, baseUrl.trim().length - 1)
          : baseUrl.trim();
      final endpointUrl = cleanUrl.endsWith('/chat/completions')
          ? cleanUrl
          : '$cleanUrl/chat/completions';
      final uri = Uri.parse(endpointUrl);

      final userContext = lastAssistantMessage != null && lastAssistantMessage.isNotEmpty
          ? 'Prior Assistant Context: ${lastAssistantMessage.substring(0, lastAssistantMessage.length > 300 ? 300 : lastAssistantMessage.length)}\n\nUser Prompt: $cleanPrompt'
          : 'User Prompt: $cleanPrompt';

      final payload = {
        'model': model.trim().isNotEmpty ? model.trim() : 'glm-5.3',
        'messages': [
          {'role': 'system', 'content': systemPrompt},
          {'role': 'user', 'content': userContext},
        ],
        'temperature': 0.1,
        'stream': false,
      };

      final request = await _httpClient.postUrl(uri);
      request.headers.set(HttpHeaders.contentTypeHeader, 'application/json; charset=utf-8');
      request.headers.set(HttpHeaders.acceptHeader, 'application/json');
      request.headers.set('User-Agent', 'AutonomOS-AgentRouter/1.0');
      if (apiKey.trim().isNotEmpty) {
        request.headers.set(HttpHeaders.authorizationHeader, 'Bearer ${apiKey.trim()}');
      }

      final bodyBytes = utf8.encode(json.encode(payload));
      request.contentLength = bodyBytes.length;
      request.add(bodyBytes);

      final response = await request.close().timeout(const Duration(seconds: 4));
      final responseBody = await response.transform(utf8.decoder).join();

      if (response.statusCode >= 200 && response.statusCode < 300) {
        final data = json.decode(responseBody);
        final choices = data['choices'] as List?;
        if (choices != null && choices.isNotEmpty) {
          final message = choices.first['message'] as Map<String, dynamic>?;
          final text = message?['content'] as String? ?? choices.first['text'] as String? ?? '';

          // Extract JSON block from response text
          final match = RegExp(r'\{[\s\S]*\}').firstMatch(text);
          if (match != null) {
            final jsonStr = match.group(0)!;
            final parsed = json.decode(jsonStr) as Map<String, dynamic>;
            return AgentRoutingDecision.fromJson(parsed);
          }
        }
      }
    } catch (_) {
      // Fallback seamlessly to intelligent deterministic router on timeout or failure
    }

    return classifyUserIntentSmart(prompt: cleanPrompt, lastAssistantMessage: lastAssistantMessage);
  }
}
