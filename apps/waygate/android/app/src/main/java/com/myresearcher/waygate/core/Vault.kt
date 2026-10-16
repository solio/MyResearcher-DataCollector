// Credentials persistence mirroring services/Vault.ets: the console token goes to the
// platform sealed store, the rest to plain preferences, and the pending claim id is
// kept so a restart can still read the original probe result.
package com.myresearcher.waygate.core

import android.content.Context
import java.util.UUID

class Vault(private val context: Context) {
    private val prefs = context.getSharedPreferences("waygate", Context.MODE_PRIVATE)

    fun load(): Credentials {
        var device = prefs.getString("device", "") ?: ""
        if (device.isEmpty()) {
            device = UUID.randomUUID().toString()
            prefs.edit().putString("device", device).apply()
        }
        return Credentials(
            hub = prefs.getString("hub", "") ?: "",
            token = SecureStore.get(context, TOKEN) ?: "",
            deviceId = device,
            push = prefs.getBoolean("push", false),
            pendingClaim = prefs.getString("pendingClaim", "") ?: "",
        )
    }

    fun save(credentials: Credentials) {
        SecureStore.put(context, TOKEN, credentials.token)
        prefs.edit().putString("hub", credentials.hub).putBoolean("push", credentials.push).apply()
    }

    fun claim(value: String) {
        prefs.edit().putString("pendingClaim", value).apply()
    }

    private companion object {
        const val TOKEN = "console-token"
    }
}
