package net.nullcell.movil

import android.content.Context
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * Un favorito guarda la **dirección real** del portal —su clave, 56
 * caracteres— y no el nombre. Un nombre como `nullcell.null` es un registro
 * del DHT que se queda el primero que llega y que caduca si nadie lo renueva:
 * mañana podría apuntar a otro sitio. La dirección no se puede suplantar.
 */
data class Favorito(val direccion: String, val ruta: String, val titulo: String, val nombre: String?, val ts: Long) {
    val url: String get() = "https://$direccion.null$ruta"
    val clave: String get() = "$direccion$ruta"
}

object Favoritos {
    private lateinit var prefs: android.content.SharedPreferences
    private val _lista = MutableStateFlow<List<Favorito>>(emptyList())
    val lista: StateFlow<List<Favorito>> = _lista.asStateFlow()

    fun init(ctx: Context) {
        prefs = ctx.getSharedPreferences("favoritos", Context.MODE_PRIVATE)
        _lista.value = leer()
    }

    private fun leer(): List<Favorito> = runCatching {
        val a = JSONArray(prefs.getString("lista", "[]"))
        (0 until a.length()).map { i ->
            val o = a.getJSONObject(i)
            Favorito(o.getString("d"), o.optString("r", "/"), o.optString("t"), o.optString("n").ifBlank { null }, o.optLong("ts"))
        }
    }.getOrDefault(emptyList())

    private fun escribir(l: List<Favorito>) {
        val a = JSONArray()
        l.forEach { f -> a.put(JSONObject().put("d", f.direccion).put("r", f.ruta).put("t", f.titulo).put("n", f.nombre ?: "").put("ts", f.ts)) }
        prefs.edit().putString("lista", a.toString()).apply()
        _lista.value = l
    }

    /** ¿Está guardada esta página? Compara por dirección real, o por nombre recordado. */
    fun buscar(host: String, ruta: String): Favorito? {
        val h = host.lowercase().removeSuffix(".null")
        return _lista.value.firstOrNull { it.ruta == ruta && (it.direccion == h || it.nombre == h) }
    }

    /**
     * Resuelve el nombre a su dirección real (por la red) y la guarda.
     * Bloquea mientras resuelve: llamar desde una corrutina.
     */
    suspend fun guardar(host: String, ruta: String, titulo: String): Result<Favorito> = withContext(Dispatchers.IO) {
        val h = host.lowercase().removeSuffix(".null")
        val direccion = if (h.length == 56 && h.all { it in 'a'..'z' || it in '2'..'7' }) h
        else runCatching {
            Python.getInstance().getModule("nullcell_movil").callAttr("direccion_real", h).toString()
        }.getOrDefault("")
        if (direccion.length != 56) return@withContext Result.failure(Exception("No se pudo averiguar la dirección real de $h.null"))
        val f = Favorito(direccion, ruta.ifBlank { "/" }, titulo.ifBlank { "$h.null" }, h.takeIf { it != direccion }, System.currentTimeMillis())
        escribir(listOf(f) + _lista.value.filterNot { it.clave == f.clave })
        Result.success(f)
    }

    fun quitar(f: Favorito) = escribir(_lista.value.filterNot { it.clave == f.clave })
}
