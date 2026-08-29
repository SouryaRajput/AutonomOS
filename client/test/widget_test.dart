import 'package:flutter_test/flutter_test.dart';
import 'package:autonomos/app.dart';
import 'package:autonomos/state/app_state.dart';
import 'package:autonomos/services/mock_api_client.dart';

void main() {
  testWidgets('ClaudeShell renders conversation sidebar and chat view', (WidgetTester tester) async {
    final mockClient = MockAutonomOSApiClient();
    final appState = AppState(client: mockClient);

    await tester.pumpWidget(AutonomOSAppRoot(appState: appState));
    await tester.pumpAndSettle();

    // Verify app title and New Chat button render
    expect(find.text('AutonomOS'), findsOneWidget);
    expect(find.text('New Chat'), findsOneWidget);
    expect(find.text('Settings'), findsOneWidget);
  });
}
