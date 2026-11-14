package net.nullcell.movil

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * Mantiene vivo el proceso —y con él el nodo y sus circuitos— mientras la app
 * está en segundo plano. Sin esto Android lo mata al cambiar de app y hay que
 * volver a entrar a la red.
 */
class NodoService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CANAL, "Nodo Nullcell", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Mantiene la conexión con la red mientras usas el navegador"
                setShowBadge(false)
            }
        )
        val abrir = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val n: Notification = Notification.Builder(this, CANAL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Nullcell")
            .setContentText("Conectado a la red · cifrado de extremo a extremo")
            .setContentIntent(abrir)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, n)
        }
        Nodo.arrancar()
        return START_STICKY
    }

    companion object {
        private const val CANAL = "nodo"
        fun iniciar(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, NodoService::class.java))
        }
    }
}
