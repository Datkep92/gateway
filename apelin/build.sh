#!/bin/sh
# Đóng gói wiki.qaq.gateway: dịch vụ nền giữ cổng Gateway luôn chạy + lệnh gatewayctl.
set -eu
: "${APELIN_PACKAGE_DIR:?run this through apelin_project_build}"
: "${APELIN_PROJECT_ROOT:?}" "${APELIN_BOOTSTRAP:?}" "${APELIN_ARCHITECTURE:?}"
: "${APELIN_BUILD_ID:?}" "${TMPDIR:?}"
: "${APELIN_PREFIX?}"          # luôn được đặt, rỗng với roothide
: "${APELIN_CONFIGURATION:=debug}"

stage="$(mktemp -d "${TMPDIR:?}stage.XXXXXX")"
trap 'rm -rf -- "${stage:?}"' EXIT

PKG_ROOT="${stage:?}${APELIN_PREFIX}"
DAEMON_DIR="${PKG_ROOT:?}/Library/LaunchDaemons"
BIN_DIR="${PKG_ROOT:?}/usr/bin"
mkdir -p "${DAEMON_DIR:?}" "${BIN_DIR:?}" "${stage:?}/DEBIAN"

# plist: thay @PREFIX@ bằng gốc bootstrap của bản dựng này
sed -e "s|@PREFIX@|${APELIN_PREFIX}|g" \
    "${APELIN_PROJECT_ROOT:?}/packaging/daemon.plist.in" \
    > "${DAEMON_DIR:?}/wiki.qaq.gateway.plist"

cp "${APELIN_PROJECT_ROOT:?}/packaging/gatewayctl" "${BIN_DIR:?}/gatewayctl"
sed -e "s|@PREFIX@|${APELIN_PREFIX}|g" \
    "${APELIN_PROJECT_ROOT:?}/packaging/gateway-supervise.in" \
    > "${BIN_DIR:?}/gateway-supervise"
chmod 0755 "${BIN_DIR:?}/gatewayctl" "${BIN_DIR:?}/gateway-supervise" "${DAEMON_DIR:?}/wiki.qaq.gateway.plist"

cp "${APELIN_PROJECT_ROOT:?}/packaging/postinst" "${stage:?}/DEBIAN/postinst"
cp "${APELIN_PROJECT_ROOT:?}/packaging/prerm" "${stage:?}/DEBIAN/prerm"
chmod 0755 "${stage:?}/DEBIAN/postinst" "${stage:?}/DEBIAN/prerm"

SIZE=$(du -sk "${stage:?}" | awk '{print $1}')
cat > "${stage:?}/DEBIAN/control" <<EOF
Package: wiki.qaq.gateway
Version: 1.0.3
Architecture: ${APELIN_ARCHITECTURE:?}
Installed-Size: ${SIZE:?}
Name: Gateway (tự chạy)
Author: Datkep92
Maintainer: Datkep92
Section: Utilities
Priority: optional
Description: Giữ cổng Gateway luôn chạy.
 Dịch vụ nền tự bật cổng khi máy khởi động lại và tự bật lại nếu cổng tắt.
 Kèm lệnh gatewayctl on|off|restart|status để bật/tắt bằng tay.
EOF

dpkg-deb -b --root-owner-group "${stage:?}" "${APELIN_PACKAGE_DIR:?}/" >/dev/null

echo "build: ${APELIN_BOOTSTRAP} ${APELIN_ARCHITECTURE} ${APELIN_CONFIGURATION} ${APELIN_BUILD_ID}"
ls -l "${APELIN_PACKAGE_DIR:?}"