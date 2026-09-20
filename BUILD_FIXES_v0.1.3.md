# Build/runtime fixes in v0.1.3

## API Key save / Windows DPAPI

`win32crypt.CryptProtectData(...)` returns encrypted `bytes` directly in current pywin32.
v0.1.2 incorrectly indexed `[1]`, which converted the second encrypted byte into an integer and then failed in Base64 encoding with:

`a bytes-like object is required, not 'int'`

v0.1.3 normalizes the documented pywin32 return shapes safely and performs an actual DPAPI encrypt/decrypt/delete round trip during both staging and installed-installer smoke tests. The test also verifies that the plaintext secret is not written to disk.

Expected Windows build markers now include:

```text
DPAPI_ROUNDTRIP_OK
SELF_CHECK_OK
```

These markers appear once before installer creation and again after GitHub silently installs the newly built `XiaoZhiSetup.exe`.

## Earlier fixes retained

- SQLite self-check explicitly closes `TaskCenter` before temporary-directory cleanup, avoiding `WinError 32` on Windows.
- Python embeddable `python312._pth` contains `..\app\src`, so `xiaozhi_agent` imports work without `PYTHONPATH`.
- GitHub Actions checks exit codes, dependency imports, staged runtime, generated installer existence/size, and installed-runtime smoke tests before uploading the artifact.
