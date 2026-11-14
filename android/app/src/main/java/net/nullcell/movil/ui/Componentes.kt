package net.nullcell.movil.ui

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Tarjeta con borde fino y un brillo sutil arriba: la pieza base de la interfaz. */
@Composable
fun Tarjeta(
    modifier: Modifier = Modifier,
    acento: Color = C.cian,
    relleno: Dp = 16.dp,
    contenido: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier
            .clip(RoundedCornerShape(18.dp))
            .background(Brush.verticalGradient(listOf(C.superficie2, C.superficie)))
            .border(1.dp, Brush.verticalGradient(listOf(acento.copy(alpha = .35f), C.borde)), RoundedCornerShape(18.dp))
            .padding(relleno),
        content = contenido,
    )
}

@Composable
fun Pastilla(texto: String, color: Color, modifier: Modifier = Modifier, icono: ImageVector? = null) {
    Row(
        modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = .14f))
            .border(1.dp, color.copy(alpha = .45f), RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icono != null) {
            Icon(icono, null, tint = color, modifier = Modifier.size(14.dp)); Spacer(Modifier.width(5.dp))
        }
        Text(texto, color = color, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold)
    }
}

/** El candado: anónimo o no, siempre a la vista. */
@Composable
fun Candado(anonimo: Boolean, modifier: Modifier = Modifier) =
    if (anonimo) Pastilla("ANÓNIMO", C.verde, modifier, Icons.Filled.Lock)
    else Pastilla("SIN ANONIMATO", C.ambar, modifier, Icons.Filled.LockOpen)

@Composable
fun Dato(etiqueta: String, valor: String, color: Color = C.texto, modifier: Modifier = Modifier, detalle: String? = null) {
    Tarjeta(modifier, acento = color, relleno = 14.dp) {
        Text(etiqueta.uppercase(), style = MaterialTheme.typography.labelSmall, color = C.tenue)
        Spacer(Modifier.padding(top = 4.dp))
        Text(valor, fontSize = 26.sp, fontWeight = FontWeight.Bold, color = color, fontFamily = Mono)
        if (detalle != null) Text(detalle, style = MaterialTheme.typography.bodySmall, color = C.tenue, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

/** Punto que late: «vivo». */
@Composable
fun Latido(color: Color, tam: Dp = 10.dp) {
    val t = rememberInfiniteTransition(label = "latido")
    val a by t.animateFloat(.35f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    Box(Modifier.size(tam).clip(CircleShape).background(color.copy(alpha = a)))
}

/** Anillo de progreso hacia el anonimato: operadores / 3. */
@Composable
fun Anillo(progreso: Float, color: Color, modifier: Modifier = Modifier, grosor: Dp = 10.dp, centro: @Composable () -> Unit) {
    val p by animateFloatAsState(progreso.coerceIn(0f, 1f), tween(800), label = "anillo")
    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.matchParentSize()) {
            val s = Stroke(grosor.toPx(), cap = StrokeCap.Round)
            drawArc(C.borde, 135f, 270f, false, style = s)
            drawArc(
                Brush.sweepGradient(listOf(C.cian, color, C.magenta), center = Offset(size.width / 2, size.height / 2)),
                135f, 270f * p, false, style = s,
            )
        }
        centro()
    }
}

@Composable
fun TituloSeccion(texto: String, modifier: Modifier = Modifier, accion: (@Composable () -> Unit)? = null) {
    Row(modifier.fillMaxWidth().padding(top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(texto, style = MaterialTheme.typography.titleMedium, color = C.texto, modifier = Modifier.weight(1f))
        accion?.invoke()
    }
}

@Composable
fun Fila(modifier: Modifier = Modifier, espacio: Dp = 12.dp, contenido: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit) =
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(espacio), content = contenido)

fun corto(nid: String?): String =
    if (nid.isNullOrBlank()) "…" else if (nid.length > 16) "${nid.take(6)}…${nid.takeLast(6)}" else nid

fun hace(ts: Double?): String {
    if (ts == null || ts <= 0) return "nunca"
    val s = (System.currentTimeMillis() / 1000.0 - ts).toLong().coerceAtLeast(0)
    return when {
        s < 45 -> "hace un momento"
        s < 3600 -> "hace ${s / 60} min"
        s < 86400 -> "hace ${s / 3600} h"
        else -> "hace ${s / 86400} d"
    }
}
