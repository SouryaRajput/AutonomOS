/// Service for generating and sanitizing concise conversation titles from user prompts.
/// All titles are strictly enforced to be under 5 words (1 to 4 words maximum).

const Set<String> _stopWords = {
  'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an', 'and',
  'any', 'are', 'aren\'t', 'as', 'at', 'be', 'because', 'been', 'before', 'being',
  'below', 'between', 'both', 'but', 'by', 'can', 'can\'t', 'cannot', 'could',
  'couldn\'t', 'did', 'didn\'t', 'do', 'does', 'doesn\'t', 'doing', 'don\'t',
  'down', 'during', 'each', 'few', 'for', 'from', 'further', 'had', 'hadn\'t',
  'has', 'hasn\'t', 'have', 'haven\'t', 'having', 'he', 'her', 'here', 'hers',
  'herself', 'him', 'himself', 'his', 'how', 'i', 'if', 'in', 'into', 'is',
  'isn\'t', 'it', 'it\'s', 'its', 'itself', 'let\'s', 'me', 'more', 'most',
  'my', 'myself', 'no', 'nor', 'not', 'of', 'off', 'on', 'once', 'only', 'or',
  'other', 'our', 'ours', 'ourselves', 'out', 'over', 'own', 'same', 'she',
  'should', 'shouldn\'t', 'so', 'some', 'such', 'than', 'that', 'the', 'their',
  'theirs', 'them', 'themselves', 'then', 'there', 'these', 'they', 'this',
  'those', 'through', 'to', 'too', 'under', 'until', 'up', 'very', 'was',
  'wasn\'t', 'we', 'were', 'weren\'t', 'what', 'when', 'where', 'which', 'while',
  'who', 'whom', 'why', 'with', 'won\'t', 'would', 'wouldn\'t', 'you', 'your',
  'yours', 'yourself', 'yourselves', 'also', 'just', 'using', 'onto',
  'like', 'please', 'really', 'want', 'need', 'help', 'give', 'tell', 'show',
  'work', 'works', 'working',
};

final Map<String, String> _acronymMap = {
  'oauth': 'OAuth',
  'pkce': 'PKCE',
  '3d': '3D',
  '2d': '2D',
  'api': 'API',
  'apis': 'APIs',
  'ui': 'UI',
  'ux': 'UX',
  'css': 'CSS',
  'html': 'HTML',
  'js': 'JS',
  'ts': 'TS',
  'sql': 'SQL',
  'nosql': 'NoSQL',
  'cli': 'CLI',
  'ai': 'AI',
  'ml': 'ML',
  'crm': 'CRM',
  'jwt': 'JWT',
  'sdk': 'SDK',
  'rest': 'REST',
  'grpc': 'gRPC',
  'graphql': 'GraphQL',
  'url': 'URL',
  'http': 'HTTP',
  'https': 'HTTPS',
  'ssh': 'SSH',
  'aws': 'AWS',
  'gcp': 'GCP',
  'db': 'DB',
};

/// Formats a list of words into Title Case, respecting tech acronyms and enforcing <= 4 words.
String _formatTitle(List<String> words) {
  if (words.isEmpty) return 'General Inquiry';

  // Strictly enforce under 5 words (1 to 4 words maximum)
  final capped = words.length > 4 ? words.sublist(0, 4) : words;

  final formatted = capped.map((w) {
    final lower = w.toLowerCase();
    if (_acronymMap.containsKey(lower)) {
      return _acronymMap[lower]!;
    }
    // Keep uppercase acronyms of length 2-4
    if (w.toUpperCase() == w && w.length >= 2 && w.length <= 4 && !RegExp(r'^\d+$').hasMatch(w)) {
      return w;
    }
    if (w.length == 1) return w.toUpperCase();
    return w[0].toUpperCase() + w.substring(1).toLowerCase();
  }).toList();

  return formatted.join(' ');
}

/// Strips common conversational fillers and stop words from a string, returning meaningful tokens.
List<String> _extractMeaningfulTokens(String raw, {int maxTokens = 4}) {
  final clean = raw.replaceAll(RegExp(r'[^\w\.\-]'), ' ').trim();
  final tokens = clean.split(RegExp(r'\s+')).where((t) => t.isNotEmpty).toList();

  final filtered = tokens.where((t) {
    final lower = t.toLowerCase();
    return !_stopWords.contains(lower) && !RegExp(r'^\d+$').hasMatch(t);
  }).toList();

  if (filtered.isNotEmpty) {
    return filtered.take(maxTokens).toList();
  }
  return tokens.take(maxTokens).toList();
}

/// Generates a concise summary title for a conversation based on the first prompt.
/// Strictly guaranteed to be under 5 words (1 to 4 words maximum).
String generateConversationTitle(String prompt) {
  var text = prompt.trim();
  if (text.isEmpty) return 'New Chat';

  // 1. Remove slash commands if present
  if (text.startsWith('/')) {
    text = text.replaceFirst(RegExp(r'^/[a-zA-Z0-9_\-]+\s*'), '').trim();
  }

  // 2. Strip conversational preambles and boilerplate question starters
  final preambleRegex = RegExp(
    r"^(?:can you(?: please)?|could you(?: please)?|would you(?: please)?|please|will you|help me(?: to)?|can we|i want you to|i want to|i need you to|let's|i'd like you to|i'd like to|tell me about|give me(?: a)?|show me(?: how to)?|explain to me(?: how)?|how do i|how can i|how to|what is(?: the)?|what are(?: the)?|why is(?: the)?|why does|why do|why did|is there a|is it possible to)\s+",
    caseSensitive: false,
  );
  while (preambleRegex.hasMatch(text)) {
    text = text.replaceFirst(preambleRegex, '').trim();
  }

  final lower = text.toLowerCase();

  // 3. Special intent: Proceeding with existing plan
  if (RegExp(r'^(?:proceed|yes,? proceed|go ahead|start execution|start building)\b', caseSensitive: false).hasMatch(lower)) {
    return 'Proceed With Plan';
  }

  // 4. Special intent: Summary requests
  if (RegExp(r'^(?:summarize|summary|recap|what did you find|show findings)\b', caseSensitive: false).hasMatch(lower)) {
    return 'Findings Summary';
  }

  // 5. Special domain: Business complaints, pain points, and software automation
  if ((lower.contains('complain') || lower.contains('pain point') || lower.contains('struggl') || lower.contains('issue')) &&
      (lower.contains('business') || lower.contains('client') || lower.contains('market')) &&
      (lower.contains('automat') || lower.contains('softwar') || lower.contains('pitch'))) {
    return 'Business Automation Research';
  }
  if (lower.contains('business') && lower.contains('automat')) {
    return 'Business Automation';
  }

  // 6. Special domain: Bug fixes / Debugging
  final fixMatch = RegExp(
    r"^(?:fix|debug|repair|resolve|troubleshoot)\s+(?:the\s+)?([a-zA-Z0-9_\-]+(?:\s+[a-zA-Z0-9_\-]+){0,2})",
    caseSensitive: false,
  ).firstMatch(text);
  if (fixMatch != null) {
    final target = fixMatch.group(1)!.trim();
    final tokens = _extractMeaningfulTokens(target, maxTokens: 2);
    if (tokens.isNotEmpty) {
      return _formatTitle(['Fix', ...tokens]);
    }
  }

  // 7. Special domain: Software / app / feature creation
  final createMatch = RegExp(
    r"^(?:create|build|develop|make|implement|code|generate|design|craft|setup|set up)\s+(?:an?|the|some)?\s*([a-zA-Z0-9_\-]+(?:\s+[a-zA-Z0-9_\-]+){0,5})",
    caseSensitive: false,
  ).firstMatch(text);
  if (createMatch != null) {
    final rawEntity = createMatch.group(1)!.trim();
    final tokens = _extractMeaningfulTokens(rawEntity, maxTokens: 4);
    if (tokens.isNotEmpty) {
      return _formatTitle(tokens);
    }
  }

  // 8. Special domain: Research / investigation / analysis
  final researchMatch = RegExp(
    r"^(?:research(?:\s+(?:about|on|into))?|investigate|analyze|analyse|audit|explore|find(?:\s+out(?:\s+about)?)?)\s+([a-zA-Z0-9_\-]+(?:\s+[a-zA-Z0-9_\-]+){0,5})",
    caseSensitive: false,
  ).firstMatch(text);
  if (researchMatch != null) {
    final rawTopic = researchMatch.group(1)!.trim();
    final tokens = _extractMeaningfulTokens(rawTopic, maxTokens: 3);
    if (tokens.isNotEmpty) {
      if (tokens.length <= 3 && !tokens.any((t) => t.toLowerCase() == 'research')) {
        return _formatTitle([...tokens, 'Research']);
      }
      return _formatTitle(tokens);
    }
  }

  // 9. General clause extraction: Take first clause before punctuation or conjunctions
  final firstClause = text
      .split(RegExp(r'[?.!\n;,]|(?:\b(?:which|that|because|where|when|also|and|so that)\b)'))[0]
      .trim();
  final tokens = _extractMeaningfulTokens(firstClause, maxTokens: 4);
  if (tokens.isNotEmpty) {
    return _formatTitle(tokens);
  }

  // Fallback: raw words of text capped strictly to <= 4 words
  final fallbackTokens = text.split(RegExp(r'\s+')).where((t) => t.isNotEmpty).take(4).toList();
  return _formatTitle(fallbackTokens);
}

/// Sanitizes an LLM-generated conversation title.
/// Strips markdown, quotes, prefixes, punctuation, and guarantees <= 4 words.
String sanitizeConversationTitle(String raw) {
  var clean = raw.trim();
  if (clean.isEmpty) return '';

  // Strip markdown, quotes, formatting
  clean = clean.replaceAll(RegExp(r'[*`"''_#]'), ' ');

  // Remove common LLM prefixes ("Title:", "Suggested Title:", "Summary:", "Topic:")
  clean = clean.replaceFirst(
    RegExp(r'^(?:Title|Suggested Title|Conversation Title|Topic|Subject|Summary):\s*', caseSensitive: false),
    '',
  );

  // Take first line only
  clean = clean.split('\n')[0].trim();

  // Strip trailing punctuation
  clean = clean.replaceAll(RegExp(r'[.!?:;,\-]+$'), '').trim();

  // Split into tokens
  final words = clean.split(RegExp(r'\s+')).where((w) => w.isNotEmpty).toList();
  if (words.isEmpty) return '';

  // Strictly enforce under 5 words (1 to 4 words maximum)
  final capped = words.length > 4 ? words.sublist(0, 4) : words;

  return _formatTitle(capped);
}
