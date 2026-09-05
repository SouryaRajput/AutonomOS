import 'package:flutter_test/flutter_test.dart';
import 'package:autonomos/models/conversation.dart';
import 'package:autonomos/state/chat_controller.dart';

void main() {
  group('User Intent Classification Tests', () {
    final samplePriorMessage = ChatMessage(
      id: 'msg-1',
      conversationId: 'conv-1',
      messageType: MessageType.managerMessage,
      content: 'I analyzed the architecture. Would you like me to proceed with creating a detailed implementation plan for Phase A?',
      sender: 'Manager',
      timestamp: DateTime.now().toIso8601String(),
    );

    test('Classifies summary requests to summarizeFindings without re-researching', () {
      final summaryPrompts = [
        'provide a summary',
        'summarize',
        'give me a summary',
        'summary of findings',
        'summarize your findings',
        'what did you find',
        'what are the findings',
        'give me a recap',
        'tldr',
        'key takeaways',
        'brief me on the findings',
        'overview of findings',
      ];

      for (final prompt in summaryPrompts) {
        final intent = classifyUserIntent(
          prompt: prompt,
          lastAssistantMessage: samplePriorMessage.content,
          conversationMessages: [samplePriorMessage],
        );
        expect(intent, UserIntent.summarizeFindings, reason: 'Failed for prompt: "$prompt"');
      }
    });

    test('Classifies proceed affirmations to proceedWithPlan', () {
      final proceedPrompts = [
        'proceed',
        'proceed with phase a',
        'yes',
        'sure',
        'go ahead',
        'start phase a',
        'start',
        'create the implementation plan',
        'let\'s do it',
        'sounds good',
      ];

      for (final prompt in proceedPrompts) {
        final intent = classifyUserIntent(
          prompt: prompt,
          lastAssistantMessage: samplePriorMessage.content,
          conversationMessages: [samplePriorMessage],
        );
        expect(intent, UserIntent.proceedWithPlan, reason: 'Failed for prompt: "$prompt"');
      }
    });

    test('Classifies questions and follow-ups to simpleQuestion', () {
      final questionPrompts = [
        'what version of flutter is this?',
        'why did you recommend next.js?',
        'where is the main entrypoint?',
        'can you explain the router configuration?',
        'is there a database connection here?',
        'hi',
        'thanks!',
      ];

      for (final prompt in questionPrompts) {
        final intent = classifyUserIntent(
          prompt: prompt,
          lastAssistantMessage: samplePriorMessage.content,
          conversationMessages: [samplePriorMessage],
        );
        expect(intent, UserIntent.simpleQuestion, reason: 'Failed for prompt: "$prompt"');
      }
    });

    test('Classifies explicit deep research requests to complexResearch', () {
      final researchPrompts = [
        '/research evaluate state management options',
        'research the security vulnerabilities in this codebase',
        'conduct an audit of the authentication system',
        'investigate memory leaks in the render tree',
        'deep dive into backend performance bottlenecks',
      ];

      for (final prompt in researchPrompts) {
        final intent = classifyUserIntent(
          prompt: prompt,
          lastAssistantMessage: samplePriorMessage.content,
          conversationMessages: [samplePriorMessage],
        );
        expect(intent, UserIntent.complexResearch, reason: 'Failed for prompt: "$prompt"');
      }
    });

    test('Classifies code writing instructions to codeImplementation', () {
      final codePrompts = [
        'write code to add user authentication',
        'create file src/components/Header.tsx',
        'fix bug in login form validation',
        'refactor database query helper',
      ];

      for (final prompt in codePrompts) {
        final intent = classifyUserIntent(
          prompt: prompt,
          lastAssistantMessage: samplePriorMessage.content,
          conversationMessages: [samplePriorMessage],
        );
        expect(intent, UserIntent.codeImplementation, reason: 'Failed for prompt: "$prompt"');
      }
    });
  });
}
