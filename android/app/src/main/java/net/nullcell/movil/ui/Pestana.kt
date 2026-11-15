package net.nullcell.movil.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Forum
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.ui.graphics.vector.ImageVector

enum class Pestana(val titulo: String, val icono: ImageVector) {
    INICIO("Inicio", Icons.Filled.Dashboard),
    BUSCAR("Buscar", Icons.Filled.Search),
    NAVEGAR("Navegar", Icons.Filled.Public),
    CHAT("Chat", Icons.Filled.Forum),
    SEMILLAS("Semillas", Icons.Filled.Hub),
    AJUSTES("Ajustes", Icons.Filled.Settings),
}
