import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

class AppTheme {
  AppTheme._();

  static ThemeData get darkTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: AppTokens.darkBg,
      colorScheme: const ColorScheme.dark(
        primary: AppTokens.brandPrimaryLight,
        onPrimary: Colors.white,
        surface: AppTokens.darkSurface,
        onSurface: AppTokens.darkTextPrimary,
        error: AppTokens.danger,
        onError: Colors.white,
      ),
      cardTheme: const CardTheme(
        color: AppTokens.darkCard,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: AppTokens.borderRadiusMd,
          side: BorderSide(color: AppTokens.darkBorder, width: 1),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: AppTokens.darkBorder,
        thickness: 1,
        space: 1,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: AppTokens.darkSurface,
        foregroundColor: AppTokens.darkTextPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppTokens.darkSurface,
        border: OutlineInputBorder(
          borderRadius: AppTokens.borderRadiusMd,
          borderSide: const BorderSide(color: AppTokens.darkBorder),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: AppTokens.borderRadiusMd,
          borderSide: const BorderSide(color: AppTokens.darkBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: AppTokens.borderRadiusMd,
          borderSide: const BorderSide(color: AppTokens.brandPrimaryLight, width: 1.5),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space12),
        hintStyle: const TextStyle(color: AppTokens.darkTextMuted, fontSize: 14),
      ),
      textTheme: const TextTheme(
        headlineMedium: TextStyle(color: AppTokens.darkTextPrimary, fontSize: 24, fontWeight: FontWeight.w600),
        titleLarge: TextStyle(color: AppTokens.darkTextPrimary, fontSize: 18, fontWeight: FontWeight.w600),
        titleMedium: TextStyle(color: AppTokens.darkTextPrimary, fontSize: 15, fontWeight: FontWeight.w600),
        bodyLarge: TextStyle(color: AppTokens.darkTextPrimary, fontSize: 14, height: 1.5),
        bodyMedium: TextStyle(color: AppTokens.darkTextSecondary, fontSize: 13, height: 1.4),
        labelSmall: TextStyle(color: AppTokens.darkTextMuted, fontSize: 11, fontWeight: FontWeight.w500),
      ),
    );
  }

  static ThemeData get lightTheme {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      scaffoldBackgroundColor: AppTokens.lightBg,
      colorScheme: const ColorScheme.light(
        primary: AppTokens.brandPrimary,
        onPrimary: Colors.white,
        surface: AppTokens.lightSurface,
        onSurface: AppTokens.lightTextPrimary,
        error: AppTokens.danger,
        onError: Colors.white,
      ),
      cardTheme: const CardTheme(
        color: AppTokens.lightCard,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: AppTokens.borderRadiusMd,
          side: BorderSide(color: AppTokens.lightBorder, width: 1),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: AppTokens.lightBorder,
        thickness: 1,
        space: 1,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: AppTokens.lightSurface,
        foregroundColor: AppTokens.lightTextPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppTokens.lightSurface,
        border: OutlineInputBorder(
          borderRadius: AppTokens.borderRadiusMd,
          borderSide: const BorderSide(color: AppTokens.lightBorder),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: AppTokens.borderRadiusMd,
          borderSide: const BorderSide(color: AppTokens.lightBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: AppTokens.borderRadiusMd,
          borderSide: const BorderSide(color: AppTokens.brandPrimary, width: 1.5),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: AppTokens.space16, vertical: AppTokens.space12),
        hintStyle: const TextStyle(color: AppTokens.lightTextMuted, fontSize: 14),
      ),
      textTheme: const TextTheme(
        headlineMedium: TextStyle(color: AppTokens.lightTextPrimary, fontSize: 24, fontWeight: FontWeight.w600),
        titleLarge: TextStyle(color: AppTokens.lightTextPrimary, fontSize: 18, fontWeight: FontWeight.w600),
        titleMedium: TextStyle(color: AppTokens.lightTextPrimary, fontSize: 15, fontWeight: FontWeight.w600),
        bodyLarge: TextStyle(color: AppTokens.lightTextPrimary, fontSize: 14, height: 1.5),
        bodyMedium: TextStyle(color: AppTokens.lightTextSecondary, fontSize: 13, height: 1.4),
        labelSmall: TextStyle(color: AppTokens.lightTextMuted, fontSize: 11, fontWeight: FontWeight.w500),
      ),
    );
  }
}
