package net.nullcell.movil

import android.app.Application
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/** Arranca Python una vez por proceso; el nodo lo arranca [Nodo] cuando hace falta. */
class NullcellApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        Nodo.init(this)
        Actualizador.init(this)
    }
}
