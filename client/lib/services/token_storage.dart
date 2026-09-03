import 'dart:convert';
import 'dart:io';

class DailyTokenUsage {
  final int prompt;
  final int completion;
  final int total;

  const DailyTokenUsage({
    this.prompt = 0,
    this.completion = 0,
    this.total = 0,
  });

  factory DailyTokenUsage.fromJson(Map<String, dynamic> json) {
    return DailyTokenUsage(
      prompt: json['prompt'] as int? ?? 0,
      completion: json['completion'] as int? ?? 0,
      total: json['total'] as int? ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
    'prompt': prompt,
    'completion': completion,
    'total': total,
  };
}

class TokenUsageData {
  final int lifetimePrompt;
  final int lifetimeCompletion;
  final int lifetimeTotal;
  final Map<String, DailyTokenUsage> daily;

  const TokenUsageData({
    this.lifetimePrompt = 0,
    this.lifetimeCompletion = 0,
    this.lifetimeTotal = 0,
    this.daily = const {},
  });

  DailyTokenUsage getUsageForDate(String dateKey) {
    return daily[dateKey] ?? const DailyTokenUsage();
  }

  DailyTokenUsage get usageToday => getUsageForDate(TokenStorage.todayKey());

  factory TokenUsageData.fromJson(Map<String, dynamic> json) {
    final lifetime = (json['lifetime'] as Map<String, dynamic>?) ?? {};
    final rawDaily = (json['daily'] as Map<String, dynamic>?) ?? {};

    final dailyMap = <String, DailyTokenUsage>{};
    rawDaily.forEach((k, v) {
      if (v is Map<String, dynamic>) {
        dailyMap[k] = DailyTokenUsage.fromJson(v);
      }
    });

    return TokenUsageData(
      lifetimePrompt: lifetime['prompt'] as int? ?? 0,
      lifetimeCompletion: lifetime['completion'] as int? ?? 0,
      lifetimeTotal: lifetime['total'] as int? ?? 0,
      daily: dailyMap,
    );
  }

  Map<String, dynamic> toJson() => {
    'lifetime': {
      'prompt': lifetimePrompt,
      'completion': lifetimeCompletion,
      'total': lifetimeTotal,
    },
    'daily': daily.map((k, v) => MapEntry(k, v.toJson())),
  };
}

class TokenStorage {
  TokenStorage._();

  static String todayKey() {
    final now = DateTime.now();
    final year = now.year.toString().padLeft(4, '0');
    final month = now.month.toString().padLeft(2, '0');
    final day = now.day.toString().padLeft(2, '0');
    return '$year-$month-$day';
  }

  static File _getStorageFile() {
    final home = Platform.environment['HOME'] ?? '.';
    final dir = Directory('$home/.autonomos');
    if (!dir.existsSync()) {
      try {
        dir.createSync(recursive: true);
      } catch (_) {}
    }
    return File('$home/.autonomos/token_usage.json');
  }

  static TokenUsageData loadUsage() {
    try {
      final file = _getStorageFile();
      if (file.existsSync()) {
        final content = file.readAsStringSync();
        if (content.trim().isNotEmpty) {
          final data = json.decode(content);
          if (data is Map<String, dynamic>) {
            return TokenUsageData.fromJson(data);
          }
        }
      }
    } catch (_) {}
    return const TokenUsageData();
  }

  static void saveUsage(TokenUsageData data) {
    try {
      final file = _getStorageFile();
      file.writeAsStringSync(json.encode(data.toJson()), flush: true);
    } catch (_) {}
  }

  static TokenUsageData recordUsage(int prompt, int completion) {
    final current = loadUsage();
    final today = todayKey();
    final currentToday = current.getUsageForDate(today);

    final updatedToday = DailyTokenUsage(
      prompt: currentToday.prompt + prompt,
      completion: currentToday.completion + completion,
      total: currentToday.total + prompt + completion,
    );

    final updatedDaily = Map<String, DailyTokenUsage>.from(current.daily);
    updatedDaily[today] = updatedToday;

    final updatedData = TokenUsageData(
      lifetimePrompt: current.lifetimePrompt + prompt,
      lifetimeCompletion: current.lifetimeCompletion + completion,
      lifetimeTotal: current.lifetimeTotal + prompt + completion,
      daily: updatedDaily,
    );

    saveUsage(updatedData);
    return updatedData;
  }
}
