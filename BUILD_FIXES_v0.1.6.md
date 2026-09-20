# Installer fixes in v0.1.6

## Real-machine failure

On a fresh Windows PC, Setup could fail near the end with:

`IPersistFile::Save failed; code 0x80070005. Access denied.`

The failing target was the per-user Startup-folder shortcut:

`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\小智电脑助手后台.lnk`

Some Windows security configurations deny installer writes to the Startup folder. v0.1.5 also hid this problem because the GitHub installer smoke test explicitly disabled the `startup` task.

## v0.1.6 changes

1. Removed the Startup-folder `.lnk` autostart mechanism.
2. Autostart now uses the standard per-user registry value `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\XiaoZhiAssistant`.
3. The installer remains per-user (`PrivilegesRequired=lowest`) and requires no administrator rights for this startup entry.
4. Upgrade/uninstall performs a best-effort cleanup of the legacy Startup shortcut, but failure to delete a protected legacy shortcut never aborts installation.
5. GitHub installer smoke testing now installs with startup **enabled**, verifies the actual HKCU Run value points at the installed `pythonw.exe`, `main.py`, and `--background`, and verifies cleanup after uninstall.
6. All v0.1.5 schedule-routing, Task Center, Harness policy, Office validation, DPAPI, and embedded-Python fixes are retained.

## Expected CI markers

A valid Windows build should include:

- `STARTUP_REGISTRY_OK`
- `INSTALLED_APP_PATH_OK`
- `INSTALLED_RUNTIME_IMPORTS_OK`
- application self-check markers
- `INSTALLER_SMOKE_OK`
- `STARTUP_REGISTRY_CLEANUP_OK`
