package net.nullcell.movil.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** La paleta de nullcell.null: fondo casi negro, cian para datos, magenta de acento. */
object C {
    val fondo = Color(0xFF07090D)
    val superficie = Color(0xFF0D1117)
    val superficie2 = Color(0xFF121922)
    val borde = Color(0xFF1D2733)
    val texto = Color(0xFFE6EDF3)
    val tenue = Color(0xFF7D8DA3)
    val cian = Color(0xFF22D3EE)
    val magenta = Color(0xFFFF4FD8)
    val verde = Color(0xFF34D399)
    val ambar = Color(0xFFFBBF24)
    val rojo = Color(0xFFF87171)
}

val Mono = FontFamily.Monospace

private val esquema = darkColorScheme(
    primary = C.cian, onPrimary = C.fondo,
    secondary = C.magenta, onSecondary = C.fondo,
    tertiary = C.verde,
    background = C.fondo, onBackground = C.texto,
    surface = C.superficie, onSurface = C.texto,
    surfaceVariant = C.superficie2, onSurfaceVariant = C.tenue,
    surfaceContainer = C.superficie, surfaceContainerHigh = C.superficie2,
    surfaceContainerHighest = C.superficie2, surfaceContainerLow = C.superficie,
    outline = C.borde, outlineVariant = C.borde,
    error = C.rojo,
)

private val tipografia = Typography(
    headlineMedium = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 26.sp, letterSpacing = 4.sp),
    titleLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 20.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 16.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 17.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 14.sp),
    labelSmall = TextStyle(fontFamily = Mono, fontSize = 11.sp, letterSpacing = 0.5.sp),
)

@Composable
fun TemaNullcell(contenido: @Composable () -> Unit) =
    MaterialTheme(colorScheme = esquema, typography = tipografia, content = contenido)
