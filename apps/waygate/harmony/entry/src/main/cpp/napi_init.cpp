// Native bridge for the Waygate phone side.
// Step 1 only proves the NDK/NAPI toolchain builds and loads in this project;
// the SSH client (libssh2) is layered on top of this module in later steps.
#include "napi/native_api.h"

static napi_value Ping(napi_env env, napi_callback_info info) {
    napi_value result = nullptr;
    napi_create_string_utf8(env, "waygate-native-ok", NAPI_AUTO_LENGTH, &result);
    return result;
}

EXTERN_C_START
static napi_value Init(napi_env env, napi_value exports) {
    napi_property_descriptor descriptors[] = {
        {"ping", nullptr, Ping, nullptr, nullptr, nullptr, napi_default, nullptr},
    };
    napi_define_properties(env, exports,
                           sizeof(descriptors) / sizeof(descriptors[0]), descriptors);
    return exports;
}
EXTERN_C_END

static napi_module waygateModule = {
    .nm_version = 1,
    .nm_flags = 0,
    .nm_filename = nullptr,
    .nm_register_func = Init,
    .nm_modname = "waygate_ssh",
    .nm_priv = nullptr,
    .reserved = {0},
};

extern "C" __attribute__((constructor)) void RegisterWaygateModule(void) {
    napi_module_register(&waygateModule);
}
