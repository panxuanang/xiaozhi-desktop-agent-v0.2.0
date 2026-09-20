# Runtime fixes in v0.1.4

This release fixes the first real complex-task test after installer setup.

## What the test exposed

- On Windows with Harness 0.1.5rc1, `workspace-write` can fail before PowerShell starts with `--profile <name> is required`.
- Retrying the same durable session at a wider mode can fail closed because the session already owns permission state and there is no approval answerer.
- A model can then degrade to text-only filesystem writes and create an HTML/text file named `.doc`; Word may open it, but it is not a real OOXML `.docx`.
- The old Task Center verifier ignored `.doc`, so that degraded result could be incorrectly marked complete.

## v0.1.4 changes

1. On Windows, when the user enables compatibility fallback, complex Harness tasks start directly in `danger-full-access` instead of spending a first model run in the known-broken `workspace-write` path.
2. Non-Windows escalation retries use a fresh session id so permission state is not inherited from the failed session.
3. Agent instructions explicitly prohibit `sandbox_permissions`/`justification` on tool calls; the session preset is used as-is.
4. Legacy `.doc` output is rejected. Word requests require a real `.docx` and `python-docx` must reopen it. Excel/PPT requests similarly require `.xlsx`/`.pptx` and structural reopen checks.
5. If the final response reports shell/Python/approval failure, Task Center refuses to mark the task complete even if a fallback text file was written.
6. Research instructions prohibit claiming a direct-platform item is verified unless source evidence was actually obtained.
7. CI self-checks now verify the Windows mode-selection policy, fresh-session retry behavior, legacy `.doc` rejection, and a real `.docx` reopen path.
