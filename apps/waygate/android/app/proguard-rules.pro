# sshj and BouncyCastle are reflection-heavy and reference JAAS/GSS classes that
# Android does not ship, so keep them whole and silence the missing references.
-keep class net.schmizz.** { *; }
-keep class com.hierynomus.** { *; }
-keep class org.bouncycastle.** { *; }
-dontwarn net.schmizz.**
-dontwarn com.hierynomus.**
-dontwarn org.bouncycastle.**
-dontwarn org.slf4j.**
-dontwarn org.ietf.jgss.**
-dontwarn javax.security.auth.**
-dontwarn javax.naming.**
