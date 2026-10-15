cmake_minimum_required(VERSION 3.5.0)
project(waygate_ssh C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# Pinned third-party sources are prepared by third_party/fetch.ps1 (+
# third_party/build-openssl.sh for the mbedTLS-free OpenSSL backend). None of
# them is committed; see runs.md.
set(WG_THIRD_PARTY "${CMAKE_CURRENT_SOURCE_DIR}/../../../../third_party")
set(WG_LIBSSH2 "${WG_THIRD_PARTY}/libssh2-libssh2-1.11.1")
set(WG_OPENSSL "${WG_THIRD_PARTY}/openssl-3.0.15")

if(NOT EXISTS "${WG_LIBSSH2}/CMakeLists.txt")
    message(FATAL_ERROR
        "libssh2 source missing. Run apps/waygate/harmony/third_party/fetch.ps1 first.")
endif()
if(NOT EXISTS "${WG_OPENSSL}/include/openssl/ssl.h")
    message(FATAL_ERROR
        "OpenSSL for aarch64-linux-ohos missing. Run third_party/fetch.ps1 then "
        "third_party/build-openssl.sh inside WSL first.")
endif()

# The OpenSSL crypto backend is required: it is the only libssh2 1.11.1 backend
# that enables ED25519 (LIBSSH2_ED25519 1), which the existing worker keys need.
set(OPENSSL_ROOT_DIR "${WG_OPENSSL}" CACHE PATH "" FORCE)
set(OPENSSL_INCLUDE_DIR "${WG_OPENSSL}/include" CACHE PATH "" FORCE)
set(OPENSSL_CRYPTO_LIBRARY "${WG_OPENSSL}/lib/libcrypto.a" CACHE FILEPATH "" FORCE)
set(OPENSSL_SSL_LIBRARY "${WG_OPENSSL}/lib/libssl.a" CACHE FILEPATH "" FORCE)
set(OPENSSL_USE_STATIC_LIBS TRUE CACHE BOOL "" FORCE)
find_package(OpenSSL REQUIRED)

set(CRYPTO_BACKEND "OpenSSL" CACHE STRING "" FORCE)
set(BUILD_STATIC_LIBS ON CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(ENABLE_ZLIB_COMPRESSION OFF CACHE BOOL "" FORCE)
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
    OpenSSL::Crypto
)
