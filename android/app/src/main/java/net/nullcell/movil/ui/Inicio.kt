package net.nullcell.movil.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Explore
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import net.nullcell.movil.Estado
import net.nullcell.movil.Fase
import net.nullcell.movil.Semillas

@Composable
fun PantallaInicio(
    e: Estado,
    s: Semillas,
    abrir: (String) -> Unit,
    irA: (Pestana) -> Unit,
    compartir: (String) -> Unit,
    reintentar: () -> Unit,
) {
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { Cabecera(e) }
        item { TarjetaRed(e, reintentar) }
        item {
            Fila {
                Dato("Celdas", e.celdas.toString(), C.cian, Modifier.weight(1f), "conocidas")
                Dato("Relevos", e.relevos.toString(), C.magenta, Modifier.weight(1f), "usables")
            }
        }
        item {
            Fila {
                Dato("Semillas", "${s.verificadas}/${s.total}", C.verde, Modifier.weight(1f), "contestan")
                Dato("Saltos", if (e.saltos > 0) e.saltos.toString() else "—", C.ambar, Modifier.weight(1f), "por circuito")
            }
        }
        item { TituloSeccion("Accesos") }
        item {
            Fila {
                Acceso(Icons.Filled.Public, "nullcell.null", "El portal del proyecto", C.cian, Modifier.weight(1f)) { abrir("nullcell.null") }
                Acceso(Icons.Filled.Search, "Buscar", "Portales de la red", C.magenta, Modifier.weight(1f)) { irA(Pestana.BUSCAR) }
            }
        }
        item {
            Fila {
                Acceso(Icons.Filled.Hub, "Semillas", "${s.total} guardadas", C.verde, Modifier.weight(1f)) { irA(Pestana.SEMILLAS) }
                Acceso(Icons.Filled.Share, "Invitar", "Comparte tu entrada", C.ambar, Modifier.weight(1f)) {
                    (s.invitacion ?: e.invitacion)?.let(compartir)
                }
            }
        }
        if (e.eventos.isNotEmpty()) {
            item { TituloSeccion("Actividad del nodo") }
            item {
                Tarjeta(relleno = 4.dp) {
                    e.eventos.take(8).forEachIndexed { i, ev ->
                        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.Top) {
                            Box(Modifier.padding(top = 6.dp).size(7.dp).clip(RoundedCornerShape(50)).background(colorEvento(ev.tipo)))
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(ev.texto, style = MaterialTheme.typography.bodyMedium, color = C.texto, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                Text("${ev.tipo} · ${hace(ev.ts)}", style = MaterialTheme.typography.labelSmall, color = C.tenue)
                            }
                        }
                        if (i < minOf(7, e.eventos.size - 1)) Box(Modifier.fillMaxWidth().height(1.dp).background(C.borde))
                    }
                }
            }
        }
        item { Spacer(Modifier.height(80.dp)) }
    }
}

@Composable
private fun Cabecera(e: Estado) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
        Column(Modifier.weight(1f)) {
            Text("NULLCELL", style = MaterialTheme.typography.headlineMedium, color = C.cian)
            Text(
                "tu celda · ${corto(e.nid)}.null",
                style = MaterialTheme.typography.labelSmall, color = C.tenue,
            )
        }
        val (txt, col) = when (e.fase) {
            Fase.ARRANCANDO -> "arrancando" to C.tenue
            Fase.CONECTANDO -> "conectando" to C.ambar
            Fase.EN_RED -> "en la red" to C.verde
            Fase.ERROR -> "error" to C.rojo
        }
        Row(
            Modifier.clip(RoundedCornerShape(50)).background(col.copy(alpha = .12f)).padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Latido(col, 8.dp); Spacer(Modifier.width(8.dp))
            Text(txt, color = col, style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun TarjetaRed(e: Estado, reintentar: () -> Unit) {
    Tarjeta(acento = if (e.anonimo) C.verde else if (e.rutasListas) C.cian else C.ambar, relleno = 18.dp) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Anillo(
                progreso = e.operadores / 3f,
                color = if (e.anonimo) C.verde else C.ambar,
                modifier = Modifier.size(118.dp),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    if (e.fase == Fase.ARRANCANDO) {
                        CircularProgressIndicator(Modifier.size(28.dp), color = C.cian, strokeWidth = 3.dp)
                    } else {
                        // Lo grande es si navegas; el anillo, cuánto falta para el anonimato.
                        // Con un solo operador ya se navega: «1/3» no es un requisito.
                        val (grande, col) = when {
                            e.anonimo -> "Anónimo" to C.verde
                            e.rutasListas -> "Conectado" to C.verde
                            else -> "Buscando" to C.ambar
                        }
                        Text(grande, fontSize = 17.sp, fontWeight = FontWeight.Bold, color = col)
                        Text(
                            "${e.operadores} operador${if (e.operadores == 1) "" else "es"}",
                            style = MaterialTheme.typography.labelSmall, color = C.texto,
                        )
                        if (!e.anonimo) Text("anonimato ${minOf(e.operadores, 3)}/3", style = MaterialTheme.typography.labelSmall, color = C.tenue)
                    }
                }
            }
            Spacer(Modifier.width(16.dp))
            Column(Modifier.weight(1f)) {
                Candado(e.anonimo)
                Spacer(Modifier.height(10.dp))
                Text(
                    when {
                        e.fase == Fase.ERROR -> "El nodo no pudo arrancar"
                        e.fase == Fase.ARRANCANDO -> "Encendiendo tu celda…"
                        e.anonimo -> "Navegación anónima"
                        e.rutasListas -> "Conectado y cifrado"
                        else -> "Buscando relevos…"
                    },
                    style = MaterialTheme.typography.titleMedium, color = C.texto,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    when {
                        e.fase == Fase.ERROR -> e.error ?: "Error desconocido"
                        e.anonimo -> "3 saltos por operadores distintos: nadie en el camino sabe a la vez quién eres y adónde vas."
                        e.rutasListas -> "Ya puedes navegar: con un operador basta. Va cifrado de extremo a extremo; el anonimato se enciende solo cuando la red tenga 3 operadores."
                        else -> e.porQueNo ?: "Entrando a la red por tus semillas."
                    },
                    style = MaterialTheme.typography.bodySmall, color = C.tenue,
                )
            }
        }
        AnimatedVisibility(e.fase == Fase.ERROR) {
            Text(
                "Reintentar",
                color = C.cian, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth().padding(top = 14.dp).clip(RoundedCornerShape(12.dp))
                    .border(1.dp, C.cian.copy(alpha = .5f), RoundedCornerShape(12.dp)).clickable(onClick = reintentar).padding(12.dp),
            )
        }
    }
}

@Composable
private fun Acceso(icono: ImageVector, titulo: String, sub: String, color: Color, modifier: Modifier, accion: () -> Unit) {
    Column(
        modifier
            .clip(RoundedCornerShape(18.dp))
            .background(Brush.linearGradient(listOf(color.copy(alpha = .16f), C.superficie)))
            .border(1.dp, color.copy(alpha = .35f), RoundedCornerShape(18.dp))
            .clickable(onClick = accion)
            .padding(16.dp),
    ) {
        Box(
            Modifier.size(38.dp).clip(RoundedCornerShape(12.dp)).background(color.copy(alpha = .18f)),
            contentAlignment = Alignment.Center,
        ) { Icon(icono, null, tint = color, modifier = Modifier.size(20.dp)) }
        Spacer(Modifier.height(12.dp))
        Text(titulo, style = MaterialTheme.typography.titleMedium, color = C.texto, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(sub, style = MaterialTheme.typography.bodySmall, color = C.tenue, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

private fun colorEvento(tipo: String) = when {
    tipo.startsWith("control.seed") -> C.verde
    tipo.startsWith("control") -> C.magenta
    tipo.startsWith("node") -> C.cian
    else -> C.tenue
}

@Suppress("unused")
private val iconoExplorar = Icons.Filled.Explore
