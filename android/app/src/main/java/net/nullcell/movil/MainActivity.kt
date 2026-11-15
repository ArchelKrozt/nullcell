package net.nullcell.movil

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import net.nullcell.movil.ui.C
import net.nullcell.movil.ui.DialogoActualizacion
import net.nullcell.movil.ui.PantallaAjustes
import net.nullcell.movil.ui.PantallaBuscar
import net.nullcell.movil.ui.PantallaChat
import net.nullcell.movil.ui.PantallaInicio
import net.nullcell.movil.ui.PantallaNavegador
import net.nullcell.movil.ui.PantallaSemillas
import net.nullcell.movil.ui.Pestana
import net.nullcell.movil.ui.TemaNullcell
import kotlin.system.exitProcess

class MainActivity : ComponentActivity() {
    private var invitacion by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        pedirNotificaciones()
        NodoService.iniciar(this)
        Nodo.arrancar()
        Actualizador.alArrancar()
        invitacion = invitacionDe(intent)
        setContent { TemaNullcell { App() } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        invitacionDe(intent)?.let { invitacion = it }
    }

    private fun invitacionDe(i: Intent?): String? =
        i?.data?.takeIf { it.scheme == "nullcell" }?.toString()

    private fun pedirNotificaciones() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) registerForActivityResult(ActivityResultContracts.RequestPermission()) {}.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun compartir(texto: String) {
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"; putExtra(Intent.EXTRA_TEXT, texto)
        }, "Compartir"))
    }

    @Composable
    private fun App() {
        val estado by Nodo.estado.collectAsStateWithLifecycle()
        val semillas by Nodo.semillas.collectAsStateWithLifecycle()
        val sinLeer by Nodo.sinLeer.collectAsStateWithLifecycle()
        var pestana by remember { mutableStateOf(if (invitacion != null) Pestana.SEMILLAS else Pestana.INICIO) }
        var destino by remember { mutableStateOf<String?>(null) }
        val avisos = remember { SnackbarHostState() }
        val alcance = rememberCoroutineScope()
        val avisar: (String) -> Unit = { m -> alcance.launch { avisos.showSnackbar(m) } }
        val abrir: (String) -> Unit = { d -> destino = d; pestana = Pestana.NAVEGAR }
        if (invitacion != null && pestana != Pestana.SEMILLAS) pestana = Pestana.SEMILLAS

        Scaffold(
            containerColor = C.fondo,
            snackbarHost = {
                SnackbarHost(avisos) { Snackbar(it, containerColor = C.superficie2, contentColor = C.texto, actionColor = C.cian) }
            },
            bottomBar = {
                NavigationBar(containerColor = C.superficie, tonalElevation = 0.dp) {
                    Pestana.entries.forEach { p ->
                        NavigationBarItem(
                            selected = pestana == p,
                            onClick = { pestana = p },
                            icon = {
                                // Seis pestañas: la etiqueta sólo en la elegida, para que quepan.
                                if (p == Pestana.CHAT && sinLeer > 0 && pestana != Pestana.CHAT)
                                    BadgedBox(badge = { Badge(containerColor = C.magenta) { Text(if (sinLeer > 99) "99+" else "$sinLeer") } }) { Icon(p.icono, p.titulo) }
                                else Icon(p.icono, p.titulo)
                            },
                            label = { Text(p.titulo, maxLines = 1) },
                            alwaysShowLabel = false,
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = C.fondo, indicatorColor = C.cian,
                                selectedTextColor = C.cian, unselectedIconColor = C.tenue, unselectedTextColor = C.tenue,
                            ),
                        )
                    }
                }
            },
        ) { margen ->
            Box(Modifier.fillMaxSize().background(C.fondo).padding(margen)) {
                AnimatedContent(pestana, transitionSpec = { fadeIn() togetherWith fadeOut() }, label = "pestana") { p ->
                    when (p) {
                        Pestana.INICIO -> PantallaInicio(estado, semillas, abrir, { pestana = it }, ::compartir) { Nodo.arrancar() }
                        Pestana.BUSCAR -> PantallaBuscar(abrir)
                        Pestana.NAVEGAR -> PantallaNavegador(destino, estado.anonimo, ::compartir, avisar)
                        Pestana.CHAT -> PantallaChat(estado, avisar)
                        Pestana.SEMILLAS -> PantallaSemillas(semillas, avisar, ::compartir, invitacion) { invitacion = null }
                        Pestana.AJUSTES -> PantallaAjustes(estado, avisar) {
                            stopService(Intent(this@MainActivity, NodoService::class.java))
                            finishAndRemoveTask(); exitProcess(0)
                        }
                    }
                }
            }
        }
        DialogoActualizacion()
    }
}
