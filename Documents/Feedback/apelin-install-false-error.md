# Báo lỗi Apelin: cài gói xong nhưng báo thất bại

Trạng thái: **đã gửi** — https://github.com/owngoal-dev/Apelin/issues/45

## Tiêu đề

Installing a locally built package reports failure even though the package was installed

## Nội dung (tiếng Anh, để gửi)

**What I did**

1. Built a package for this device from a project that uses its own build script
   (`apelin_project_build`, the script calls `dpkg-deb`, package is rootless,
   `iphoneos-arm64`).
2. Installed that package with Apelin's install tool.

**What happened**

Apelin answered: "Irisin could not complete the installation. Try again."
The installation had in fact succeeded:

- the Irisin log (`irisin-install.log`) ends with `irisin-install exit 0`;
- `dpkg` reports `install ok installed` for the package, and its files are in place.

**Consequence**

Apelin does not record that installation: the package list still shows an older
version of the same package (marked as not removable), so the package can only be
removed from Sileo, and the next install from Apelin works from a stale record.

**Expected**

Read the Irisin log and its exit status. Report success when the package really is
installed; when it is not, show the error Irisin actually reported.

**How to see it again**

1. Build a package from a project whose build script writes a `.deb`.
2. Install it with the install tool.
3. Compare the tool's message with `irisin-install.log` and `dpkg`'s state.