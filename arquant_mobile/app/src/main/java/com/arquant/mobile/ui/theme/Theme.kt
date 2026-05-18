package com.arquant.mobile.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

private val DarkScheme = darkColorScheme(
    primary = AqColors.Primary,
    onPrimary = AqColors.TextPrimary,
    secondary = AqColors.Accent,
    onSecondary = AqColors.TextPrimary,
    tertiary = AqColors.Green,
    background = AqColors.Background,
    surface = AqColors.Surface,
    onBackground = AqColors.TextPrimary,
    onSurface = AqColors.TextPrimary,
    surfaceVariant = AqColors.Surface2,
    outline = AqColors.Border,
    error = AqColors.Red,
)

@Composable
fun ArQuantTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkScheme,
        content = content,
    )
}
