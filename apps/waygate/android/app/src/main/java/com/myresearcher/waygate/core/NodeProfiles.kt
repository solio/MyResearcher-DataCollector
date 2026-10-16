// Per-node SSH login material, keyed by node_id, sealed in the platform keystore so a
// node is imported once and later verifications are one tap. Nothing is sent to the
// hub and nothing is written to logs (mirrors services/NodeSsh.ets storage half).
package com.myresearcher.waygate.core

import android.content.Context
import org.json.JSONException
import org.json.JSONObject

class NodeProfiles(private val context: Context) {

    fun load(nodeId: String): NodeProfile? {
        val stored = SecureStore.get(context, keyFor(nodeId)) ?: return null
        return try {
            NodeProfile.from(JSONObject(stored))
        } catch (err: JSONException) {
            throw IllegalStateException("无法读取节点 SSH 凭据，请解锁后重试")
        }
    }

    fun save(profile: NodeProfile) {
        SecureStore.put(context, keyFor(profile.nodeId), profile.toJson().toString())
    }

    fun delete(nodeId: String) {
        SecureStore.delete(context, keyFor(nodeId))
    }

    private fun keyFor(nodeId: String) = "waygate-node-$nodeId-v1"
}
