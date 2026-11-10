# api_added_v1

Windows WPF voice assistant with Google API task interpretation and bounded,
typed desktop tools. The original interpretation mode remains available.

## Included

- API configuration and a local credential file; no API keys in this repository.
- Ordered task goals, application discovery, window inspection, browser actions,
  document generation, and explicit file/folder operations.
- Recycle Bin opening, bounded listing, individual restore, and confirmed emptying.
- File Explorer navigation, selection, listing/search, and scoped file operations.
- Notepad document creation, reading, appending, exact replacement, finding, and
  Save As with content verification.
- Windows Calculator arithmetic with display readback and Scientific-mode precedence.
- Navigation to fixed Windows Settings pages and observed accessibility controls.
- Recovery after failed/timed-out API calls: cancelled sessions are retired,
  late responses cannot execute or change the next request's planning state.

The experimental clap-triggered shutdown feature is excluded from this version.
Regular explicitly requested Windows shutdown commands retain their confirmation.

## Validation

- `Run-AidyTests.ps1`: 443 tests passed.
- `dotnet build WpfApp1.csproj -c Release --no-restore`: passed, zero warnings/errors.
- Real API and desktop checks verified Recycle Bin opening and Calculator
  `2 + 3 * 4 = 14`.
- The new end-to-end Notepad create/edit/save smoke test has not passed yet:
  the tested cloud planning requests failed or timed out before execution.
  Do not treat arbitrary multi-step desktop tasks as universally reliable.
- Emptying the user's Recycle Bin was not performed during validation.

## Run

From this project directory on Windows x64:

```powershell
.\setup.ps1
.\Configure-AidyCredentials.ps1
.\Set-AidyInterpretation.ps1 -Mode api
.\Start-Aidy.ps1
```

See `AI_INTEGRATION.md`, `UNIVERSAL_AGENT.md`, and `ai.example.json` for configuration.
Local credentials, `ai.local.json`, voice profiles, virtual environments, build
outputs, editor settings, logs, and validation recordings are excluded.
