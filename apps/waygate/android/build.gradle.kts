// Android port of the Waygate phone client. Toolchain versions are pinned and
// kept in step with each other (AGP 8.7.3 / Kotlin 2.0.21 / Gradle 8.9); this
// port is prepared but deliberately not yet built, so do not assume it compiles
// until a build has actually been run on a machine with the Android SDK.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
}
