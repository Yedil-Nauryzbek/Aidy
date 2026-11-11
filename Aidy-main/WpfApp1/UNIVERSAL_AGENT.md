# Aidy observed Windows agent

The current follow-up is documented in [AGENT_COMMAND_AUDIT.md](AGENT_COMMAND_AUDIT.md).
It adds semantic browser batches/selection, file discovery/copying, 45-second API
utterances, optional speaker-model loading and cache-based UIA with an explicit
parent permit before input. Google interpretation remains active for every task.

Implemented in `Aidy-voice-id-beta_2-`, starting at `c3b9384`. The neighboring
`Aidy-main` was not changed. The general agent is enabled by default in API mode.
Original mode and the existing voice/audio/identity pipeline remain available.

## Working capabilities

- AppsFolder, 32/64-bit App Paths and executable Start Menu shortcuts supply real
  launch targets, including apps absent from `apps.json`. Websites remain distinct.
  Installation/removal and command-bearing terminal shortcuts are excluded. Up to
  16 relevant registrations are shown per request. Duplicate names need clarification.
  Registry/menu changes invalidate the cache; a 60-second TTL covers other changes.
- Window IDs survive reordering and are never reassigned. Focus, restore, minimize
  and maximize have state checks. Existing confirmed closing retains frozen
  HWND/PID selections, permission rechecks and exceptions.
- UI Automation reads real controls, types, names, automation/runtime IDs, values
  and selection/toggle states. Invoke, Select, Toggle, Expand/Collapse, reading and
  editable text are implemented. Current snapshots, process ownership and focus
  are checked before input. Once input is bound, unexpected foreground changes
  stop the task. Limited keys, element-centered hit-tested clicks and bounded
  scrolling are available. No visual fallback is implemented.
- Ordinary edit controls use ValuePattern. XAML documents and Save As filename
  edits use focused Unicode input: some providers expose ValuePattern without
  committing application state. Documents retain prefix readback. Filename edits
  receive one complete bounded input burst to avoid shell autocompletion between
  chunks, then an actual Tab to commit focus, exact path readback, and Save.
  Terminal input is blocked.
- `document_write` creates a fresh Notepad or Word document and inserts a complete
  generated draft. Notepad uses its real New window command and an undoable native
  RichEdit insertion, checked through UIA. Word uses a separate registered COM
  instance, Documents.Add and the exact document Content range. It verifies text
  readback and retains the precise document window for follow-ups such as “close it”.
- Search and HTTP(S) addresses retain the saved browser. Observed browser tools
  list/select tabs, create/cycle/close tabs, go back/forward, enter Find text and
  read accessible page documents. Find binds only its expected owned browser
  popup. Tab closing requires confirmation. “Open the browser” remains a launch.
- Explicit local paths permit new UTF-8 text files/folders, bounded text reads,
  rename/move without overwrite, and confirmed recycling with IFileOperation's
  RECYCLEONDELETE flag. Both move endpoints must be granted. Links/junctions,
  device/network paths, alternate streams and executable outputs are refused.
  Folder grants cover descendants; quote paths containing spaces.
- Save As fills the actual common dialog filename edit, activates its Save button
  and verifies the resulting file against text entered in this task. Existing
  files are never overwritten. A new draft cannot replace a nonempty document
  without an explicit edit request. Notepad may restore old user tabs after
  launch; create a new document/window before entering a new draft.

## Planning, authority and limits

Explicit Notepad read/find/edit/save workflows without a requested word count use
a compact typed action array. Text and paths are real JSON fields rather than
JSON objects nested inside strings. The model selects every action and its order;
the local mapper validates all actions before executing any of them. Window
references are constrained to observed Notepad IDs or exact supported aliases.
Saving binds to the latest verified creation/read/edit goal and its actual text.
Mixed-app tasks return to the full planner, which also exposes `save_document`
during initial planning. A preparation failure or explicit pre-action rejection
does not consume an action that never ran; uncertain mutations remain nonretryable.

On 2026-11-11, real API/Windows tests passed create/append/replace/save, multiline
Unicode and quoted text with a Cyrillic filename, and create/find/read/save. Each
workflow used one model request and exact file-content readback. The automated
suite passed 459 tests. No live microphone, Voice ID or arbitrary-app guarantee
is inferred from these text-driven desktop checks.

Every accepted API task still goes to Google. A data-only worker returns a plan;
the assistant thread validates typed arguments, rechecks role/identity, obtains
required confirmation and executes. Full initial interpretation returns all
semantic goals and their initial actions. Independent bound actions and later
goals persist while the controller refreshes UI references and replans within
the current goal from observed outcomes.

General tasks use Google's native function calls. Initial plan_task action
arguments are JSON-serialized objects, strictly decoded and validated locally;
UI replans use direct native argument objects. Both enforce the same per-tool
schemas. The simplified initial schema fits Gemini's nested-decoder limits. Initial
planning cannot choose task_complete or unobserved element actions. Safe validation
and stale-view rejections can be replanned before any input; unknown outcomes,
focus loss and unexpected dialogs still stop execution. Nothing is evaluated as
Python, PowerShell or shell code. Simple named-app tasks retain their compact schema.

Ordinary draft lengths target the requested count within five percent. Local
whole-sentence selection retains the opening and conclusion of an overlong draft;
short drafts receive bounded prose extensions. Explicit “exactly” retains the
indexed word editor and exact local count check. All model calls count against
the task budget. Double-escaped generated paragraph breaks become real paragraphs
before counting. A new document is the default for
open/create-and-write requests, so existing work is preserved. A missing essay topic
uses a neutral general topic; essential ambiguity still gets a short question.

The raw transcript always reaches Google, with separate conservative speech hints
for reported essay/say/saying/save confusions and spoken word counts. Literal
quotes and save/search requests are not rewritten. Document writing can carry
finish_task=true when it satisfies the entire request: only successful text
readback permits that completion, and a known remaining plan tail prevents it.
Saving/formatting/other actions retain the observe/replan loop. This avoids a
redundant final cloud call reporting failure after a document was already verified.

Existing locked `comtypes`/`pywin32` dependencies are reused; none was installed.
A persistent disposable UIA process bounds observations/native actions to three
seconds. Semantic text entry has a length-based allowance capped at 30 seconds;
all operations respect the remaining task time. Cancellation stops remaining input. Timed-out or
unknown mutations are never automatically retried. Unknown outcomes allow only
observation; consumed action/state pairs prevent repeated dispatch. Late provider
replies cannot execute. General tasks are not resumed after application restart.

Window/page/document text is untrusted context, never authority. General read,
control and file tools have separate permissions; Guest cannot use them. Existing
Voice ID roles, confirmed closing, numeric replies and “close everything except
ChatGPT” remain intact. Sensitive activation requires explicit intent and fresh
confirmation. Unknown activation effects also require confirmation.

Optional `ai.local.json` settings (source and Release copies):

| Setting | Default | Range |
|---|---:|---:|
| agent_enabled | true | boolean |
| task_timeout_seconds | 90 | 10–300 |
| max_actions | 20 | 1–60 |
| max_model_calls | 12 | 1–30 |
| max_observation_retries | 1 | 0–3 |
| max_context_chars | 18000 | 4000–48000 |
| writing_timeout_seconds | 25 | 8–60 |
| max_plan_retries | 2 | 0–3 |

The writing planning allowance adds requested_words/25 seconds to the configured
writing base, capped at 60 seconds and the total task deadline. Thus the default
500-word planning allowance is 45 seconds. Small app commands retain eight seconds.

The character limit covers dynamic context, with static contracts separate. UI
trees also have bounded depth/node/text limits. Only observations may be retried.
The existing API deadline and transport retry settings remain separate.

Disable only general actions, retaining the API interpreter:

```powershell
.\Set-AidyAgent.ps1 -State off
```

Use `-State on` to enable again. The original interpreter remains available through
`Set-AidyInterpretation.ps1 -Mode original`. Reloading settings cancels outstanding
tasks; it cannot undo input already sent.

The pre-change snapshot and selective restoration script are at
`C:\Users\Rami\AppData\Local\Aidy\backups\universal-agent-20261108-083305`.
Close this Aidy, run its `Restore.ps1 -VerifyOnly`, then `Restore.ps1`, then
`Start-Aidy.ps1`. The script archives current affected files, restores this change's
source, archives added Release modules and rebuilds. It preserves current settings,
voice profiles, credentials and unrelated uncommitted files.

## Verification and practical limits

Whole-task follow-up: full initial plans contain up to eight semantic goals and
20 initial actions, also subject to the configured lower action/model/time limits.
Observable criteria track app launches, verified document writing, file saving and
file reading. Other observed workflows retain model completion checks. Fixed
atomic operations finish after their actual result, avoiding a redundant replan
executing a later goal twice. Future-goal effects are rejected inside an unfinished
current goal unless they are declared prerequisites.

Saving a document refers to its source writing goal. The executor uses the actual
verified editor text and Save As readback, including length corrections, even if
the model supplied an earlier file-text draft. This retains the saved document's
correct content and filename. Role checks and original path grants still apply.

Validation for this follow-up: 396 Python tests; eight real Google interpretation
checks with mocked OS effects, including natural phrasing, three apps, negations,
shared closing confirmation, exact amounts/timer duration and “the first one”.
Real desktop execution opened ChatGPT and dispatched the configured WhatsApp Web
launch in the requested order. A separate real chain created a Notepad essay,
saved the exact verified content and opened ChatGPT afterward. A new 500-word
request wrote and verified a 495-word draft. These are text-driven tests; no new
microphone/accent accuracy claim is made. WhatsApp is configured as whatsapp_web
on this machine; its browser navigation remains dispatched rather than verified
page loading. No user app was closed by the mocked closing tests.

Evidence: `.validation/task-all-tests.log`, `task-semantic-api.json`,
`task-desktop.json`, `task-essay-500.log`, `task-build.log` and
`task-release-hashes.json`. Reproduce with
`scripts/verify_task_understanding.py` (add `--desktop` for real effects).
The selective source backup is
`C:\Users\Rami\AppData\Local\Aidy\backups\task-understanding-20261108-140140`.
Its verified Restore.ps1 archives affected current files, restores this follow-up's
starting source and rebuilds, preserving current settings, credentials and profiles.

```powershell
.\Run-AidyTests.ps1
.\.venv\Scripts\python.exe -X utf8 scripts/verify_universal_desktop.py --api --notepad --installed --browser
.\.venv\Scripts\python.exe -X utf8 scripts/verify_document_workflows.py
.\.venv\Scripts\python.exe -X utf8 scripts/verify_document_speech.py
.\.venv\Scripts\python.exe -X utf8 scripts/verify_essay_reliability.py --exact
.\.venv\Scripts\python.exe -X utf8 scripts/benchmark_universal_api.py
.\.venv\Scripts\python.exe -X utf8 scripts/benchmark_universal_stages.py
dotnet build WpfApp1.csproj -c Release --nologo
```

The Windows harness creates native test windows and a new Notepad window, uses
disposable files, opens Paint from AppsFolder and a separate browser test window,
and closes only test windows. It checks real UI text/button actions, stale views,
focus changes, cancellation, an unexpected modal dialog, search URL, Find field,
tabs and Notepad Save As readback. Google plans/executes against the isolated test
window. Mocked boundary tests cover paths, confirmations, permissions, limits,
unknown outcomes, duplicate actions and late continuation responses.

Evidence: `.validation/universal-*.log` / `universal-*.json`. API before/after uses
the same four text requests, counterbalanced, n=4 per revision, with no desktop,
ASR or spoken feedback. The live agent measurement separates cumulative API and
execution time. These samples establish neither microphone/accent accuracy nor a
stable speedup. The 700 ms endpoint, tiny.en/English, numeric recognition, Voice ID,
sounds, dock and existing modes were not changed.

Earlier general-agent validation: 337/337 Python tests (298 at baseline), Release with zero
warnings/errors and matching hashes for all 56 copied Python sources. Four
counterbalanced real API requests per version measured median 1219.55 ms before
and 952.63 ms after; no desktop effects occurred in that comparison. Separate
synthetic ASR replay (n=4/version) measured 215.71 / 215.28 ms; SAPI feedback
generation to WAV (n=4/version) measured 3.32 / 2.91 ms. Audible playback was not
measured. These independent stage samples are not one combined end-to-end trial.
The final real Google/native-UI task took 6.33 seconds: cumulative API 5.41 seconds,
execution 0.71 seconds, five model calls, four semantic actions (one sample).
Notepad/save and browser test timings include application startup. An intermediate
Save As timeout exposed broad same-process dialog selection; the final executor
requires the exact document owner chain and reads only the two required controls.

Document/pronoun follow-up: 356 Python tests pass. Real Google planning and native
Windows execution verified contextual “close it” on an isolated test window,
Notepad creation + exactly 250 words + Save As/file readback, Word creation +
exactly 250 words, and the reported no-topic Notepad utterance. The existing close
confirmation was exercised with a simulated yes; no user app was closed. Four
SAPI-generated document/close/negation phrases passed real tiny.en decoding. These
are text-driven desktop tests and synthetic speech tests, not a live accent claim.
Repeated cloud verification hit a project rate limit once; the cooldown was
respected without rotating credentials. Google limits remain an external boundary.
Evidence: `.validation/document-*.json` and `document-*.log`.
The pre-follow-up source snapshot is
`C:\Users\Rami\AppData\Local\Aidy\backups\document-followup-20261108-095716`.
Its Restore.ps1 selectively reverts this follow-up while preserving current user
settings, credentials and profiles, and archives the outgoing source first.

Essay reliability follow-up: 371 Python tests pass. Real Google and real editor
readback passed all three screenshot transcripts (500, 247 and 501 actual words),
a clean economics request (489 words), and Microsoft Word creation (501 words).
Those five final runs took 4.48–5.67 seconds and one or two model calls each.
A separate explicit exactly-500-word Notepad request produced 500 words in 7.14
seconds with three calls, first expanding coherent prose before the final small
count edit. A new 250-word Notepad draft also passed Save As and file-content
readback, and contextual closing passed on an isolated native test window.
These are small text-driven samples, not latency guarantees.
Six synthetic Zira utterances passed real tiny.en English decoding, including
essay/500-word/spoken-count phrases and a negated close. No live accent claim.
Evidence: `.validation/essay-*-history.json`, `essay-*.txt`, `essay-all-tests.log`,
`essay-speech.log` and `document-speech.json`. Repeated development calls hit a
Google rate limit; final verified atomic writes no longer need a terminal API call.
Credentials were not rotated to bypass quota. The source backup for this follow-up
is `C:\Users\Rami\AppData\Local\Aidy\backups\essay-reliability-20261108-105928`.

Limits: applications must expose usable accessibility information; elevated or
protected windows may refuse access/focus; custom save dialogs and unsupported
encodings need manual handling. Page/document extraction is bounded. A launch or
click alone remains `dispatched`. Goal completion still depends on the planner's
interpretation of observed results; arbitrary app workflows are not guaranteed.
Failures identify the obstacle instead of substituting another app or method.

Official references: [UIA patterns](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-control-patterns-overview),
[application identities](https://learn.microsoft.com/en-us/windows/configuration/store/find-aumid),
[application registration](https://learn.microsoft.com/en-us/windows/win32/shell/app-registration),
[foreground restrictions](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow),
[recycling flags](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags),
[Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output).

Follow-up references: [native function calling](https://ai.google.dev/gemini-api/docs/function-calling),
[Word document creation](https://learn.microsoft.com/en-us/office/vba/api/word.documents.add),
[Word text ranges](https://learn.microsoft.com/en-us/office/vba/api/word.range.text),
[RichEdit insertion](https://learn.microsoft.com/en-us/windows/win32/controls/em-replacesel).
