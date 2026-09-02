import 'package:flutter/material.dart';

/// Centralized design tokens for AutonomOS: Obsidian & Amber agentic IDE aesthetic.
class AppTokens {
  AppTokens._();

  // --- Spacing ---
  static const double space2 = 2.0;
  static const double space4 = 4.0;
  static const double space6 = 6.0;
  static const double space8 = 8.0;
  static const double space10 = 10.0;
  static const double space12 = 12.0;
  static const double space14 = 14.0;
  static const double space16 = 16.0;
  static const double space18 = 18.0;
  static const double space20 = 20.0;
  static const double space24 = 24.0;
  static const double space28 = 28.0;
  static const double space32 = 32.0;
  static const double space40 = 40.0;
  static const double space48 = 48.0;

  // --- Border Radius ---
  static const double radiusXs = 4.0;
  static const double radiusSm = 6.0;
  static const double radiusMd = 8.0;
  static const double radiusLg = 12.0;
  static const double radiusXl = 16.0;
  static const double radiusFull = 999.0;

  static const BorderRadius borderRadiusXs = BorderRadius.all(Radius.circular(radiusXs));
  static const BorderRadius borderRadiusSm = BorderRadius.all(Radius.circular(radiusSm));
  static const BorderRadius borderRadiusMd = BorderRadius.all(Radius.circular(radiusMd));
  static const BorderRadius borderRadiusLg = BorderRadius.all(Radius.circular(radiusLg));
  static const BorderRadius borderRadiusXl = BorderRadius.all(Radius.circular(radiusXl));
  static const BorderRadius borderRadiusFull = BorderRadius.all(Radius.circular(radiusFull));

  // --- Animation Durations ---
  static const Duration durationFast = Duration(milliseconds: 150);
  static const Duration durationNormal = Duration(milliseconds: 250);
  static const Duration durationSlow = Duration(milliseconds: 350);

  // --- Breakpoints ---
  static const double breakpointMobile = 600.0;
  static const double breakpointTablet = 960.0;
  static const double breakpointDesktop = 1200.0;

  // --- Palette - Obsidian & Amber Dark ---
  static const Color darkBg = Color(0xFF0E0F12); // True obsidian dark backdrop
  static const Color darkBackground = darkBg;
  static const Color darkSidebar = Color(0xFF141519); // Obsidian sidebar
  static const Color darkSurface = Color(0xFF181A20); // Canvas card & container surface
  static const Color darkElevated = Color(0xFF20222A); // Elevated interactive items
  static const Color darkCard = darkSurface;
  static const Color darkBorder = Color(0xFF282B35); // Hairline border
  static const Color darkBorderMuted = Color(0xFF1F2128);
  static const Color darkTextPrimary = Color(0xFFF3F4F6); // High contrast text
  static const Color darkTextSecondary = Color(0xFF9CA3AF);
  static const Color darkTextMuted = Color(0xFF6B7280);

  // --- Palette - Light (Crisp Paper) ---
  static const Color lightBg = Color(0xFFFBFBFC);
  static const Color lightBackground = lightBg;
  static const Color lightSidebar = Color(0xFFF3F4F6);
  static const Color lightSurface = Color(0xFFFFFFFF);
  static const Color lightElevated = Color(0xFFFFFFFF);
  static const Color lightCard = Color(0xFFFFFFFF);
  static const Color lightBorder = Color(0xFFE4E4E7);
  static const Color lightBorderMuted = Color(0xFFECECEE);
  static const Color lightTextPrimary = Color(0xFF18181B);
  static const Color lightTextSecondary = Color(0xFF52525B);
  static const Color lightTextMuted = Color(0xFFA1A1AA);

  // --- Brand & Accent Colors ---
  static const Color brandPrimary = Color(0xFFF59E0B); // Warm Amber
  static const Color brandPrimaryLight = Color(0xFFFBBF24);
  static const Color brandPrimaryDark = Color(0xFFD97706);
  static const Color brandSecondary = Color(0xFF3B82F6); // Cobalt Blue
  static const Color brandIndigo = Color(0xFF6366F1);

  // --- Semantic & Diff Colors ---
  static const Color diffAdded = Color(0xFF10B981); // Emerald +diff
  static const Color diffRemoved = Color(0xFFEF4444); // Ruby -diff
  static const Color success = Color(0xFF10B981);
  static const Color successBg = Color(0xFF064E3B);
  static const Color warning = Color(0xFFF59E0B);
  static const Color warningBg = Color(0xFF78350F);
  static const Color danger = Color(0xFFEF4444);
  static const Color dangerBg = Color(0xFF7F1D1D);
  static const Color info = Color(0xFF3B82F6);
  static const Color infoBg = Color(0xFF1E3A8A);
  static const Color purple = Color(0xFF8B5CF6);
}
