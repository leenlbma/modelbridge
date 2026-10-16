# Release procedure

Releases are manual previews. CI does not publish or run real model requests. Do not rebuild an existing tag with different contents; use a new version.

1. Update `bridge/__init__.py`, `package.json`, `package-lock.json`, README versions and the changelog. Review the compatibility matrix without upgrading claims based on mocked tests.
2. Run `python scripts/check_public.py`, Python tests and `npm test`. Confirm the intended commit passes CI on Linux and macOS.
3. Build source: `python3 scripts/build_release.py`. Build the desktop on Apple Silicon macOS: `python3 scripts/build_desktop.py --dmg`. Network downloads are checksum-pinned for Electron and uv.
4. Inspect the app, source ZIP and DMG. Check all bundled sources match the reviewed commit, no local state or unreviewed media is included, and licenses are present. Run `codesign --verify --deep --strict` on the app and `hdiutil verify` on the DMG. Ad-hoc signatures do not establish publisher identity or satisfy notarization.
5. Test the packaged client using an isolated service directory and label. Do not let packaging tests update a daily installation. Record separately whether this was a clean machine or only a fresh directory on an existing Mac.
6. Prepare release notes describing changes, test evidence and known limitations. Combine SHA256 values for the source ZIP, desktop ZIP and DMG into one `SHA256SUMS.txt`.
7. Create a tag for the reviewed commit, publish a GitHub **pre-release**, and upload the three packages and combined checksum file. Do not upload previous local-preview artifacts.
8. Verify public visibility, remote commit/tree equality, correct author attribution, CI conclusion and release asset hashes. Confirm download links work. Enable private vulnerability reporting in repository settings.

Before a stable release, finish clean-Mac installation/login testing, representative real-provider Code tasks and Developer ID signing/notarization. Those steps are outstanding today.
