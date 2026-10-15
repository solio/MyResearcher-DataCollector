cmake_minimum_required(VERSION 3.5.0)
project(waygate_ssh C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# Pinned third-party sources are fetched by third_party/fetch.ps1 and are not
# committed (see runs.md, milestone M2). Versions must match that script.
set(WG_THIRD_PARTY "${CMAKE_CURRENT_SOURCE_DIR}/../../../../third_party")
set(WG_MBEDTLS "${WG_THIRD_PARTY}/mbedtls-3.6.2")
set(WG_LIBSSH2 "${WG_THIRD_PARTY}/libssh2-libssh2-1.11.1")

if(NOT EXISTS "${WG_MBEDTLS}/CMakeLists.txt" OR NOT EXISTS "${WG_LIBSSH2}/CMakeLists.txt")
    message(FATAL_ERROR
        "Third-party sources missing. Run apps/waygate/harmony/third_party/fetch.ps1 first.")
endif()

# ---- mbedTLS: static, no programs/tests, warnings not fatal ----
set(ENABLE_PROGRAMS OFF CACHE BOOL "" FORCE)
set(ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(MBEDTLS_FATAL_WARNINGS OFF CACHE BOOL "" FORCE)
set(USE_STATIC_MBEDTLS_LIBRARY ON CACHE BOOL "" FORCE)
set(USE_SHARED_MBEDTLS_LIBRARY OFF CACHE BOOL "" FORCE)
set(DISABLE_PACKAGE_CONFIG_AND_INSTALL ON CACHE BOOL "" FORCE)
add_subdirectory("${WG_MBEDTLS}" "${CMAKE_CURRENT_BINARY_DIR}/mbedtls" EXCLUDE_FROM_ALL)

# ---- libssh2: static, mbedTLS backend, no zlib, no examples/tests ----
set(CRYPTO_BACKEND "mbedTLS" CACHE STRING "" FORCE)
set(BUILD_STATIC_LIBS ON CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(ENABLE_ZLIB_COMPRESSION OFF CACHE BOOL "" FORCE)
# Satisfy libssh2's cmake/FindMbedTLS.cmake from the subproject build above.
set(MBEDTLS_INCLUDE_DIR "${WG_MBEDTLS}/include" CACHE PATH "" FORCE)
set(MBEDCRYPTO_LIBRARY
    "${CMAKE_CURRENT_BINARY_DIR}/mbedtls/library/libmbedcrypto.a" CACHE FILEPATH "" FORCE)
add_subdirectory("${WG_LIBSSH2}" "${CMAKE_CURRENT_BINARY_DIR}/libssh2" EXCLUDE_FROM_ALL)

add_library(waygate_ssh SHARED
    napi_init.cpp
)

target_include_directories(waygate_ssh PRIVATE
    "${WG_LIBSSH2}/include"
)

target_link_libraries(waygate_ssh PUBLIC
    libace_napi.z.so
    libssh2_static
    mbedcrypto
    mbedx509
    mbedtls
)
