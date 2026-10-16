plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.myresearcher.waygate"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.myresearcher.waygate"
        // 28 avoids the older platform BouncyCastle provider on API 26/27 shadowing
        // the bundled one, which is what supplies Ed25519 for sshj.
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources.excludes += setOf(
            "META-INF/LICENSE",
            "META-INF/LICENSE.txt",
            "META-INF/NOTICE",
            "META-INF/NOTICE.txt",
            "META-INF/DEPENDENCIES",
            "META-INF/INDEX.LIST",
            "META-INF/*.kotlin_module",
            "META-INF/versions/9/OSGI-INF/**",
        )
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    // ProxyController provides the process-wide WebView proxy override, the Android
    // equivalent of the ArkWeb ProxyController used by the HarmonyOS client.
    implementation("androidx.webkit:webkit:1.17.1")
    implementation("com.hierynomus:sshj:0.41.1")
    // sshj reaches Ed25519 through a full BouncyCastle; the provider bundled with
    // Android is stripped down and does not cover it.
    implementation("org.bouncycastle:bcprov-jdk18on:1.84")
    implementation("org.bouncycastle:bcpkix-jdk18on:1.84")
    implementation("org.slf4j:slf4j-nop:2.0.17")
    testImplementation("junit:junit:4.13.2")
}
