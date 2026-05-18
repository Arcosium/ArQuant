import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.hilt)
    alias(libs.plugins.ksp)
}

// local.properties → BuildConfig (git에 커밋되지 않는 비밀값 주입 경로).
// 토큰 미설정 시 빈 문자열로 폴백하여 빌드는 계속 성공시키되, 실행 시 인증 실패가 발생.
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun localProp(key: String, default: String = "") = (localProps.getProperty(key) ?: default).trim()

android {
    namespace = "com.arquant.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.arquant.mobile"
        minSdk = 28
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"

        buildConfigField("String", "ARQUANT_BASE_URL", "\"https://arquant.ai-ve.uk\"")
        buildConfigField("String", "ARQUANT_WS_URL", "\"wss://arquant.ai-ve.uk/ws\"")
        // 사장 피드백 2026-05-16: Cloudflare Access 제거 → 앱 자체 로그인(세션 토큰).
        // CF_ACCESS_* BuildConfig 및 local.properties 의 cf.access.* 항목은 더 이상 사용하지 않음.
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.core.splashscreen)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.material)

    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)

    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging.interceptor)

    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)
    implementation(libs.hilt.navigation.compose)

    debugImplementation(libs.androidx.compose.ui.tooling)
}
