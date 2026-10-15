#!/bin/sh
# Cross-build OpenSSL 3.0.15 for aarch64-linux-ohos, for the Waygate phone-side
# SSH module. libssh2 1.11.1 enables ED25519 only with its OpenSSL backend, and
# the existing worker keys (ED25519 on one node) require that.
#
# The OHOS NDK here is Windows-hosted, so this runs inside WSL (which provides
# perl + GNU make) and drives the NDK clang.exe through WSL interop.
#
#   wsl -d Ubuntu-26.04 -- sh /mnt/d/.../third_party/build-openssl.sh
set -e

NDK_BIN="/mnt/d/kits/DevEco Studio/sdk/default/openharmony/native/llvm/bin"
NDK_SYSROOT="D:/kits/DevEco Studio/sdk/default/openharmony/native/sysroot"
SRC="$(cd "$(dirname "$0")" && pwd)/openssl-3.0.15"

printf '%s\n' '#!/bin/sh' "exec \"$NDK_BIN/clang.exe\" --target=aarch64-linux-ohos --sysroot=\"$NDK_SYSROOT\" \"\$@\"" > /tmp/ohos-cc.sh
printf '%s\n' '#!/bin/sh' "exec \"$NDK_BIN/llvm-ar.exe\" \"\$@\"" > /tmp/ohos-ar.sh
printf '%s\n' '#!/bin/sh' "exec \"$NDK_BIN/llvm-ranlib.exe\" \"\$@\"" > /tmp/ohos-ranlib.sh
chmod +x /tmp/ohos-cc.sh /tmp/ohos-ar.sh /tmp/ohos-ranlib.sh

cd "$SRC"
CC=/tmp/ohos-cc.sh AR=/tmp/ohos-ar.sh RANLIB=/tmp/ohos-ranlib.sh \
    ./Configure linux-aarch64 no-shared no-tests \
    --prefix="$SRC" --openssldir="$SRC"
make -j"$(nproc)" build_libs

mkdir -p lib
cp libcrypto.a libssl.a lib/
echo "OpenSSL ready: $SRC/lib/libcrypto.a $SRC/lib/libssl.a"
