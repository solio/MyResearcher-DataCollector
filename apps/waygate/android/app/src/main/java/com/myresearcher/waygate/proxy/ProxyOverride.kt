// Process-wide WebView proxy override: the Android counterpart of the ArkWeb
// ProxyController call in the HarmonyOS client. ProxyController requires the
// PROXY_OVERRIDE feature, applies asynchronously (load the page only from the
// completion callback) and clears asynchronously as well.
package com.myresearcher.waygate.proxy

import android.content.Context
import androidx.core.content.ContextCompat
import androidx.webkit.ProxyConfig
import androidx.webkit.ProxyController
import androidx.webkit.WebViewFeature

object ProxyOverride {

    fun supported(): Boolean = WebViewFeature.isFeatureSupported(WebViewFeature.PROXY_OVERRIDE)

    fun apply(context: Context, port: Int, done: (Boolean) -> Unit) {
        if (!supported()) {
            done(false)
            return
        }
        val config = ProxyConfig.Builder()
            .addProxyRule("127.0.0.1:" + port)
            .addBypassRule("127.0.0.1")
            .addBypassRule("localhost")
            .build()
        try {
            ProxyController.getInstance().setProxyOverride(config, ContextCompat.getMainExecutor(context)) { done(true) }
        } catch (err: Exception) {
            done(false)
        }
    }

    fun clear(context: Context, done: () -> Unit) {
        if (!supported()) {
            done()
            return
        }
        try {
            ProxyController.getInstance().clearProxyOverride(ContextCompat.getMainExecutor(context)) { done() }
        } catch (err: Exception) {
            done()
        }
    }
}
