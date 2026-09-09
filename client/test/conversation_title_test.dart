import 'package:flutter_test/flutter_test.dart';
import 'package:autonomos/models/conversation.dart';
import 'package:autonomos/services/conversation_title_service.dart';

void main() {
  group('Conversation Title Service Tests', () {
    void verifyTitleUnderFiveWords(String title) {
      expect(title.isNotEmpty, isTrue, reason: 'Title should not be empty');
      final words = title.split(RegExp(r'\s+')).where((w) => w.isNotEmpty).toList();
      expect(
        words.length,
        lessThan(5),
        reason: 'Title "$title" has ${words.length} words, but MUST strictly be under 5 words (< 5)',
      );
    }

    test('1. Summarizes complex business complaints and automation research prompt under 5 words', () {
      const userPrompt =
          'can you research about what many people complaning about in their businesses which I can automate '
          'using softwares and charge them \$500-1000? Can you also find emails/contact details like instagram or '
          'reddit profiles of users whom I can pitch to sell because they have a specific issue? '
          'also, tell me why you chose those people to pitch. Do not hallucinate';

      final title = generateConversationTitle(userPrompt);
      expect(title, 'Business Automation Research');
      verifyTitleUnderFiveWords(title);
    });

    test('2. Summarizes 3D portfolio website creation prompt under 5 words and preserves 3D acronym', () {
      const userPrompt = 'Create an interactive 3D portfolio website using Three.js and particles';
      final title = generateConversationTitle(userPrompt);
      expect(title, contains('3D'));
      verifyTitleUnderFiveWords(title);
    });

    test('3. Summarizes game creation prompt under 5 words', () {
      const userPrompt = 'I want to build a real-time multiplayer chess game in Flutter with websockets';
      final title = generateConversationTitle(userPrompt);
      verifyTitleUnderFiveWords(title);
    });

    test('4. Summarizes bug fix prompt into short title with Fix prefix', () {
      const userPrompt = 'fix the login button alignment in lib/ui/login.dart';
      final title = generateConversationTitle(userPrompt);
      expect(title.startsWith('Fix'), isTrue);
      verifyTitleUnderFiveWords(title);
    });

    test('5. Summarizes OAuth / PKCE questions and preserves tech acronyms', () {
      const userPrompt = 'can you help me setup oauth 2.0 pkce authentication in flutter?';
      final title = generateConversationTitle(userPrompt);
      expect(title.contains('OAuth') || title.contains('PKCE'), isTrue);
      verifyTitleUnderFiveWords(title);
    });

    test('6. Summarizes concept questions cleanly', () {
      const userPrompt = 'what is quantum computing and how does it work?';
      final title = generateConversationTitle(userPrompt);
      expect(title, 'Quantum Computing');
      verifyTitleUnderFiveWords(title);
    });

    test('7. Handles slash commands and strips command prefixes', () {
      const userPrompt = '/research distributed consensus algorithms in raft';
      final title = generateConversationTitle(userPrompt);
      expect(title.startsWith('/'), isFalse);
      verifyTitleUnderFiveWords(title);
    });

    test('8. Handles simple greeting', () {
      const userPrompt = 'hello';
      final title = generateConversationTitle(userPrompt);
      expect(title, 'Hello');
      verifyTitleUnderFiveWords(title);
    });

    test('9. Handles plan proceeding and summary requests', () {
      expect(generateConversationTitle('proceed with the plan'), 'Proceed With Plan');
      expect(generateConversationTitle('summarize your findings'), 'Findings Summary');
      verifyTitleUnderFiveWords(generateConversationTitle('proceed with the plan'));
      verifyTitleUnderFiveWords(generateConversationTitle('summarize your findings'));
    });

    test('10. Strictly enforces under 5 words on verbose, non-matching sentences', () {
      const userPrompt =
          'the quick brown fox jumps over the lazy dog and then runs across the field to find shelter';
      final title = generateConversationTitle(userPrompt);
      verifyTitleUnderFiveWords(title);
    });

    test('11. sanitizeConversationTitle strips noisy LLM prefixes, markdown, and extra words', () {
      const noisyLlmOutput = 'Title: "**The Real-Time Interactive 3D Portfolio Website Experience**"';
      final sanitized = sanitizeConversationTitle(noisyLlmOutput);
      expect(sanitized.contains('Title:'), isFalse);
      expect(sanitized.contains('*'), isFalse);
      expect(sanitized.contains('"'), isFalse);
      verifyTitleUnderFiveWords(sanitized);
    });

    test('12. ChatConversation copyWith immutably updates title and fields', () {
      const original = ChatConversation(
        id: 'conv-1',
        projectId: 'proj-1',
        title: 'New Conversation',
        createdAt: '2026-09-09T00:00:00Z',
        updatedAt: '2026-09-09T00:00:00Z',
      );

      final updated = original.copyWith(title: 'Business Automation Research');
      expect(updated.id, 'conv-1');
      expect(updated.title, 'Business Automation Research');
      expect(original.title, 'New Conversation');
    });
  });
}
