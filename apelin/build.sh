#!/bin/sh
# Đóng gói wiki.qaq.gateway: bộ chạy cổng theo phiên Apelin + lệnh gatewayctl + app "Cổng".
set -eu
: "${APELIN_PACKAGE_DIR:?run this through apelin_project_build}"
: "${APELIN_PROJECT_ROOT:?}" "${APELIN_BOOTSTRAP:?}" "${APELIN_ARCHITECTURE:?}"
: "${APELIN_BUILD_ID:?}" "${TMPDIR:?}"
: "${APELIN_PREFIX?}"          # luôn được đặt, rỗng với roothide
: "${APELIN_CONFIGURATION:=debug}"

stage="$(mktemp -d "${TMPDIR:?}stage.XXXXXX")"
trap 'rm -rf -- "${stage:?}"' EXIT

PKG_ROOT="${stage:?}${APELIN_PREFIX}"
BIN_DIR="${PKG_ROOT:?}/usr/bin"
mkdir -p "${BIN_DIR:?}" "${stage:?}/DEBIAN"

# Từ 1.3.0 KHÔNG có LaunchDaemon: cổng sống theo phiên Apelin (apelind chạy
# <prefix>/usr/bin/gateway-session), nên gói chỉ mang script + app.
sed -e "s|@PREFIX@|${APELIN_PREFIX}|g" \
    "${APELIN_PROJECT_ROOT:?}/packaging/gateway-session.in" \
    > "${BIN_DIR:?}/gateway-session"
cp "${APELIN_PROJECT_ROOT:?}/packaging/gatewayctl" "${BIN_DIR:?}/gatewayctl"
chmod 0755 "${BIN_DIR:?}/gateway-session" "${BIN_DIR:?}/gatewayctl"

# script cài/gỡ cũng cần gốc bootstrap của bản dựng này
sed -e "s|@PREFIX@|${APELIN_PREFIX}|g" \
    "${APELIN_PROJECT_ROOT:?}/packaging/postinst" > "${stage:?}/DEBIAN/postinst"
sed -e "s|@PREFIX@|${APELIN_PREFIX}|g" \
    "${APELIN_PROJECT_ROOT:?}/packaging/prerm" > "${stage:?}/DEBIAN/prerm"
chmod 0755 "${stage:?}/DEBIAN/postinst" "${stage:?}/DEBIAN/prerm"

# ---------------------------------------------------------------------------
# App công tắc riêng (không dùng HTML): GatewaySwitch.app
# Biên dịch bằng clang có sẵn trên máy, ký bằng ldid với quyền ngoài hộp cát.
# ---------------------------------------------------------------------------
APP_SRC="${APELIN_PROJECT_ROOT:?}/app"
APP_DIR="${PKG_ROOT:?}/Applications/GatewaySwitch.app"

CLANG="${GATEWAY_CLANG:-$(command -v clang 2>/dev/null || echo /var/jb/usr/bin/clang)}"
LDID_BIN="${GATEWAY_LDID:-$(command -v ldid 2>/dev/null || echo /var/jb/usr/bin/ldid)}"
SDK="${GATEWAY_SDK:-}"
if [ -z "${SDK}" ]; then
    for d in /var/mobile/Documents/wiki.qaq.apelin/SDKs/*/; do
        [ -f "${d}SDKSettings.json" ] && { SDK="${d%/}"; break; }
    done
fi

[ -x "${CLANG}" ] || { echo "build: thiếu clang để dựng app công tắc (${CLANG})" >&2; exit 1; }
[ -x "${LDID_BIN}" ] || { echo "build: thiếu ldid để ký app (${LDID_BIN})" >&2; exit 1; }
[ -n "${SDK}" ] && [ -d "${SDK}" ] || { echo "build: không thấy SDK iPhoneOS (đặt GATEWAY_SDK)" >&2; exit 1; }

mkdir -p "${APP_DIR:?}"
"${CLANG}" -target arm64-apple-ios16.0 -isysroot "${SDK}" -fobjc-arc -O2 -Wall \
    -framework UIKit -framework Foundation -framework QuartzCore \
    -o "${APP_DIR:?}/gwswitch" "${APP_SRC:?}/main.m"
"${LDID_BIN}" -S"${APP_SRC:?}/ents.plist" "${APP_DIR:?}/gwswitch"
cp "${APP_SRC:?}/Info.plist" "${APP_DIR:?}/Info.plist"
cp "${APP_SRC:?}"/AppIcon60x60@2x.png "${APP_SRC:?}"/AppIcon60x60@3x.png "${APP_DIR:?}/"
chmod 0755 "${APP_DIR:?}/gwswitch"
chmod 0644 "${APP_DIR:?}/Info.plist" "${APP_DIR:?}"/AppIcon60x60@*.png

SIZE=$(du -sk "${stage:?}" | awk '{print $1}')
cat > "${stage:?}/DEBIAN/control" <<EOF
Package: wiki.qaq.gateway
Version: 1.3.0
Architecture: ${APELIN_ARCHITECTURE:?}
Installed-Size: ${SIZE:?}
Name: Gateway (theo phiên Apelin)
Author: Datkep92
Maintainer: Datkep92
Section: Utilities
Priority: optional
Description: Cổng Gateway sống theo phiên Apelin, kèm app mở/tắt bằng tay.
 Không còn dịch vụ nền: Apelin chạy bộ chạy cổng khi mở cổng và cổng tự tắt
 khi Apelin hết việc, nên không tốn pin lúc rảnh và không cần reboot máy.
 Kèm app GatewaySwitch (biểu tượng "Cổng") và lệnh gatewayctl on|off|restart|status
 để mở/tắt bằng tay khi cần.
EOF

dpkg-deb -b --root-owner-group "${stage:?}" "${APELIN_PACKAGE_DIR:?}/" >/dev/null

echo "build: ${APELIN_BOOTSTRAP} ${APELIN_ARCHITECTURE} ${APELIN_CONFIGURATION} ${APELIN_BUILD_ID}"
ls -l "${APELIN_PACKAGE_DIR:?}"
