# Aidy: Google AI interpretation

## Command scope and browser execution follow-up

See [AGENT_COMMAND_AUDIT.md](AGENT_COMMAND_AUDIT.md) for the November 10 follow-up,
including the reported WhatsApp + GPT app shorthand, real browser batch closure,
generic UIA/cache/transport fixes, longer spoken tasks, file discovery/copying and
lazy optional speaker encoders. This supersedes the older restrictions on compact
ChatGPT/multi-app planning and per-tab-only browser operations below.
API mode no longer starts the unused local intent classifier. All accepted API
tasks still reach Google; semantic browser/app contracts preserve complete action
arrays and use the general planner when context or capabilities require it.

## Processing latency, 2026-11-10

The accepted API command now hands its already-open capture device to the
serial cancellation listener. It no longer closes/reopens the microphone between
recognition and planning. Empty/error captures, original mode and push-to-talk
retain their cleanup; executors and final feedback still close capture. The
700 ms phrase endpoint, Whisper model, identity and confirmation rules remain.

Installed-app discovery starts during API startup. The first command consumes
that result and reuses its registered ChatGPT target instead of scanning the
AppsFolder twice. A native catalog measurement found 1,897 ms of startup work
and a 28 ms subsequent refresh; this moves work, rather than eliminating it.

Complete explicit app chains with repeated verbs (for example, "Open WhatsApp
and then open Discord") use a small structured AI contract. Every command still
calls Google. The model returns the ordered actions; exact local coverage checks
reject missing, extra, reordered or substituted actions before any effect. A
rejected compact plan falls back to the general AI planner through the existing
bounded correction loop. Ambiguous names, ChatGPT naming, pronouns, negations,
delays, writing/search, pending task clarifications and UI tasks keep the general
planner. Each accepted atomic model action retains a separate task goal.

The general task schema now enumerates supported tools, continuation types and
goal IDs. Structural validation errors give the correction attempt a specific
reason. Nested array-count constraints were deliberately left to local validation:
combining them with tool enums was rejected by the real Gemini endpoint.

Successful app chains retain detailed status in the UI but speak "Done." only
for verified results, or "Requests sent." for dispatched requests. Failures,
clarifications, answers and document results retain their actual information.
Logs now include preparation_ms and request_to_feedback_ms, so preparation is
visible alongside model, dispatch, execution and feedback time.

Validation and limits:

- 415 deterministic regression tests pass with `Run-AidyTests.ps1` (403 before).
  New coverage includes exact app/action/order preservation, fallback without
  partial execution, generic-browser preferences, ambiguity, truthful feedback,
  and capture handoff/cleanup boundaries.
- Six counterbalanced real-Google app-chain pairs: median time including all
  attempts was 2,715 ms before and 1,635 ms after. Before completed 5/6 with one
  eight-second timeout; after completed 6/6. Among successful attempts the medians
  were 2,357 ms (n=5) and 1,635 ms (n=6). This measures planning/validation with
  mocked Windows effects, not microphone-to-action latency. Individual requests
  still varied substantially with the service/network.
- A separate eight-case API run passed 8/8 for both snapshots, including negation,
  exceptions, search and 100-word writing. The final compact wire format was then
  checked by the six app-chain pairs above. No general speech-accuracy percentage
  or uniform cloud speedup is established by these small synthetic samples.
- Real SAPI rendering to silent WAVs shortened the successful unverified app-chain
  reply from 4,725 to 1,815 ms (three repeats each); audible playback was not tested.
- The first unpaced API attempt encountered rate limits. Its blocked requests
  are preserved separately and are not treated as fast successful requests.

Evidence: `.validation/processing-chains-final.json`,
`processing-api-before-after.json`, `processing-stages.json`, and
`processing-complete-tests.log`. Reproduce the API comparison with
`scripts/benchmark_processing.py`; its default quota pacing is excluded from
measured request times. Test requests are synthetic, and desktop effects are mocked.
The script uses the pre-change archive recorded in
`.validation/processing-backup.txt`. That archive preserves the previous source
and Git state for selective restoration without reverting older user changes.

## Brightness readback and independent command chains

Brightness uses native WMI with a zero transition timeout and verifies the actual
panel level. Some Windows adapters return no output object from WmiSetBrightness
even when it succeeds; readback handles this case. An adjustment beyond 0/100 is
a verified no-op. If no brightness provider exists and no setter was attempted,
the controller can still execute a following independent app launch/search/control
action and reports partial completion. An uncertain change, cancellation, denied
permission or dependent UI action still stops the chain.

`scripts/verify_brightness_chains.py` checks a one-percent native adjustment and
restoration, then real Google planning followed by brightness + Notepad and
brightness + browser search. Results: `.validation/brightness-desktop.json`.

## Whole-task understanding and persistent goals

The reported “open the chatgpt up and then open the whatsapp” reached Google
intact, but the old response contained only the first launch. Initial full
planning now returns one data-only plan_task with an ordered array of semantic
goals, typed actions and observable completion criteria. Google interprets the
whole utterance; there is no local keyword/conjunction task splitter.

Independent bound actions survive replanning; UI references are refreshed within
the current goal. Completing one goal never completes later goals. Actual app,
document and file results satisfy explicit criteria. Document saves bind the
actual verified text to its writing goal, including local length corrections,
instead of copying an earlier generated draft into a file.

Completed goal summaries retain targets/window references for “the first one”.
Clarifications retain unfinished goals; identity changes and cancellation discard
pending authority. Shared limits, role checks, sensitive-action confirmations and
whole-task numeric gates remain. Intentional app launches release the preceding
focus binding; subsequent input still needs its own verified target.

Full initial plans encode action arguments as strict JSON data to fit Gemini's
nested-schema limits. Local decoding and exact per-tool validation precede every
effect. No generated code is evaluated. Reproduce with
scripts/verify_task_understanding.py and its --desktop option.

## Essay recognition and generation reliability

Reported speech such as “write and I say you ... two hundred and fifty words” and
“write in a saying ... 500 world” now supplies scoped interpretation hints to
Google alongside the unchanged raw transcript. Quoted dictation, negations and
save/search instructions are preserved. Spoken word counts are recognized.
Ordinary lengths target ±5%; explicit “exactly” still enforces the exact count.
Ordinary repairs keep complete sentences and the introduction/conclusion, extending
a short draft rather than repeatedly discarding it. Generated paragraph separators
are normalized before counting and insertion. The writing deadline scales with
length (45 seconds for 500 words by default), bounded by the total task deadline.

The planner can mark a document_write as satisfying the whole task. This finishes
only after editor readback; known remaining plan steps prevent early completion.
Tasks with saving or other remaining actions continue the observed planning loop.
Verified writing reports the actual word count. Existing nonempty documents are
preserved, with safe pre-input rejections allowing a fresh-document plan.

Reproduce with scripts/verify_essay_reliability.py (add --exact for an exact-count
case), scripts/verify_document_workflows.py and scripts/verify_document_speech.py.
Six synthetic speech replays passed with the cached English tiny.en model; these
do not establish live microphone/accent accuracy. See UNIVERSAL_AGENT.md for results.

## Contextual closing and composed document writing

The screenshot failures were reproduced in the local voice log. Full planning now
uses native typed Gemini function calls; initial completion and unobserved-control
actions are unavailable. Exact recently used windows are retained for “close it”.
The new document_write capability creates fresh Notepad/Word documents, generates
the actual draft, validates word counts and verifies insertion. Writing goals
cannot complete after only launching an app. Safe stale-view failures are refreshed
and replanned; uncertain side effects remain non-retriable. Speech hints now include
Notepad, Word, Excel and document terms, preserving tiny.en/English and 700 ms VAD.
See UNIVERSAL_AGENT.md and `.validation/document-*` for tests and limitations.

## Observed Windows agent follow-up

General desktop/file/browser actions now extend this integration. See
[UNIVERSAL_AGENT.md](UNIVERSAL_AGENT.md) for working executors, limits, authority
boundaries, live verification and selective restoration. Earlier records follow.

## Pause tolerance, numbers and simple-command latency

Current follow-up: `codex/pause-number-latency`, based on `f6ab6e0`.

- Default end-of-phrase silence is now 700 ms in Python and WPF settings, replacing
  240 ms. Existing numeric and confirmation replies use 400 ms. Ordinary command
  previews start at 280 ms, so brief hesitations do not start wasted decodes.
  Previews never execute actions. Only a confident standalone wake retains the
  120 ms endpoint; wake-plus-command phrases get the full command pause allowance.
- Pending step-count replies use a numeric recognition context without app-name
  hotwords. Short numeric context and quiet padding retain the same tiny.en model,
  English language, full recorded samples and confidence/negation checks. Whole
  numeric replies normalize to digits; reported `zen`/`buff`/`baf` confusions are
  repaired only in this pending-number context, never inside general commands.
- Explicit single named-app requests use a smaller Google prompt/schema with
  open/close tools. Every request still goes to Google and returns a typed plan.
  Contextual, compound, delayed, negated, ChatGPT and search requests retain the
  full contract. “Open the browser” cannot return a search tool in the compact
  contract; actual search queries remain supported by the full contract.

Validation: **298 Python tests pass**. Two synthesized phrases with an inserted
400 ms pause each split into two utterances at the previous 240 ms endpoint and
remain whole at 700 ms using real Silero and Whisper: “open the browser” and
“don't close Steam”. Both full transcripts, including the negation, matched.
Twelve synthesized number replies (Zira, two rates) improved from 10/12 to 12/12;
median warm decode was 673 ms before and 380 ms after. Five cancellation/negation/
new-command replies were also checked in numeric context and did not become numbers.
These are synthetic replays, not measurements of the user's accent or microphone.

For three identical simple requests, counterbalanced real Google calls measured
1,562 ms median before and 962 ms after (n=3 each). All selected the expected tools
and app IDs. No OS actions or confirmations ran in this comparison; the sample is
too small for a stable tail or general speed guarantee. No accepted task bypasses AI.

Evidence: `.validation/pause-tests.log`, `pause-build.log`, `pause-api-speed.json`,
`pause-numbers-speed.json`, `pause-capture-replay.json`. Reproduce speech/capture
checks with `scripts/verify_pause_numbers_speed.py` and
`scripts/verify_pause_capture_replay.py` using the venv; add `--api` to the first
script for the six-request cloud comparison. The starting-source restoration
manifest includes the new files and original files changed by this follow-up.

## Dialogue, audio and desktop follow-up

Current fixes are on `codex/dialogue-and-desktop-fixes`, based on `f0cb4fd`.

- Confirmed closing of multiple named apps uses one “Are you sure?” clip and one
  yes/no answer. Each action retains its own frozen window selection; permissions
  are checked again before each close. Dependent `$last` actions remain sequential.
- Volume/brightness plans without explicit amounts stop before any action and
  play the existing “By how much?” clip. A numeric answer completes every pending
  adjustment. Already specified amounts are preserved. Identity change, cancellation
  or expiry discards the pending plan. Every task input still goes through Gemini.
- API acknowledgements are short. Successful action chains get one summary;
  failures name the problem without reading a long execution report. Query tools
  retain their actual answers. Launches remain `dispatched`, not verified completion.
- ChatGPT desktop discovery reads the actual registered Windows AppsFolder entry,
  with a 1.5-second bound and no guessed executable. `chatgpt_app` opens that entry;
  explicit website requests retain `gpt`. No desktop-to-website fallback is used.
- `web_search(query)` opens encoded Google search results in the saved/default
  browser. “Open browser and write number 3” is one search action. The query is
  never shell text or keyboard input into another application.
- Interpretation now has an eight-second default/local deadline. Cancellation
  transcription runs off-thread and cannot hold that deadline or execute actions.
  Late HTTP/cancellation results are discarded. Removing tasklist verification
  avoids the observed extra seconds after app launch. Cloud latency still varies.

Validation: **286 Python tests pass**, Release builds with zero warnings/errors.
Both existing prompt WAVs were played successfully through the real audio API.
Seven real Google requests with mocked desktop/confirmation actions passed, including
the two-control clarification/reply, grouped closing, desktop versus website, search,
and nonsense-word clarification. Those text-to-mocked-result requests took
1,315–2,120 ms; this is a small sample, excluding speech recognition and real desktop
completion. The installed ChatGPT catalog entry was found locally. Full live voice
accuracy and hardware brightness support are not established by these tests.

Evidence: `.validation/dialogue-tests.log`, `dialogue-build.log`, `dialogue-audio.log`,
`dialogue-desktop-api.json`. Reproduce API-only checks with
`python -X utf8 scripts/verify_dialogue_desktop_api.py` using the project venv.
The existing restoration manifest includes the new files.

## Voice command corrections after live feedback

Follow-up fixes are on local branch `codex/voice-command-fixes`, based on
`6fe2636` (the completed initial API integration). The current implementation adds:

- API dialogue uses `COMMAND_LISTENING` / “LISTENING FOR COMMAND…” instead of
  the legacy numeric `FOLLOWUP` label. Wake-only replies never become model
  clarification requests; “no” cancels locally. Three consecutive unresolved
  clarifications end the automatic listening window.
- “Close Aidy”, “close yourself” and the reported ASR forms “close ad” / “close
  the 80” select a confirmed graceful WPF shutdown. Ordinary bulk closing still
  protects Aidy. Aidy is added to Whisper's command hotwords; the model, English
  language and audio/VAD configuration are unchanged.
- “Close it” can select the foreground window when no recent app reference exists.
  Confirmations bind the exact HWND and PID before speaking. If Aidy is foreground,
  it uses graceful assistant exit. Unknown windows can be selected from a bounded
  current window catalog, instead of requiring an entry in apps.json.
- “Close everything except ChatGPT” has a typed exception list. A missing exception
  blocks execution; an unlocatable exception also blocks execution. Matching ChatGPT
  windows are preserved, including all tabs in those windows. To identify a website,
  its window title must expose the site; inactive browser tabs are not inspected.
  New window-closing actions send WM_CLOSE and check whether the window disappeared.
  Unsaved-work prompts remain for the user; they are never force-killed by these actions.
- Current window titles (up to 24 short labels) are sent as untrusted context to
  Google only after authentication, alongside opaque window IDs. No document body
  or arbitrary page content is read. Native process lookup avoids per-window tasklist
  subprocesses. Permissions are rechecked during multi-window closing.
- Model context is compacted to four recent entries without verbose result text,
  output is capped at 512 tokens, and a completed HTTP result is consumed before
  further cancellation-audio polling. No accepted voice task bypasses AI interpretation.

Validation of these corrections: **259 Python tests pass**; real Gemini selects
the expected tools for six reported/contextual requests with mocked actions and
confirmations. A real hidden Win32 test window was created and closed successfully
through the new HWND/PID and WM_CLOSE path; no user window was closed by the test.
Four SAPI-synthesized phrases were replayed through real Whisper and transcribed
correctly, including “close Aidy”. A read-only check also found the current ChatGPT
window. These are not live-accent or live-microphone accuracy measurements.

Speed remains dependent on Google. A two-phrase comparison initially measured
1,596 ms median before and 2,203 ms after (n=2 each), so it does **not** establish a
cloud speedup. After further prompt compaction, separate requests for the exception
and self-close cases took 2,216 ms and 1,381 ms. The fixed unsupported-command and
clarification loops remove avoidable extra requests, but no stable latency improvement
is claimed. Logs: `.validation/voice-fixes-tests.log`, `voice-fixes-api.json`,
`voice-fixes-compact-api.json`, `voice-fixes-window.log`, `aidy-name-replay.json`.
Reproduce the focused checks using `verify_voice_fixes_api.py`,
`verify_ai_window_close.py` and `verify_aidy_name_replay.py` in `scripts`.

The starting-source restoration manifest has been extended to include these files;
the same `Restore.ps1` instructions at the end of this document remain valid.

## Initial implementation record

Implemented in `C:\Users\Rami\Desktop\Aidy-main\Aidy-voice-id-beta_2-`.
The older neighboring `Aidy-main` application was not used or modified.

The implementation uses the `whisper-added-v1` application at
`bd3dedcd95c1fa022b116fe8a6f6ecc976a53b29`. The GitHub branch still pointed to that
commit when checked. All 166 application files matched locally, allowing CRLF/LF
differences. The GitHub prefix is `Aidy-main/WpfApp1/`; locally those files are
at this application's root.

The directory initially had no Git repository. A local `codex/api-reasoning`
branch now records the verified starting files in commit `d4d5648`. Pre-existing
local additions were preserved, including `.claude`, `.vscode`, and voice-clip
backups. No changes were pushed or published.

## Start and switch modes

From this application directory:

```powershell
.\Start-Aidy.ps1
```

The existing Release executable is `bin\Release\net8.0-windows\WpfApp1.exe`.
This installation is configured for API interpretation. The existing English
Whisper tiny.en recognizer, microphone/VAD, wake word, PTT, Voice ID and voice
settings are retained. Activate Aidy as before, then speak a task. After a result
there is an eight-second follow-up window; clarification replies have twenty
seconds. PTT still requires the key.

Examples: “Open Discord”; “Now close it”; “Make it a little quieter”; “A little
more”; “Open the browser and then open Discord”; “Actually, open Steam instead”.
“Play music” opens the preferred music application; it does not promise playback.

Immediate return to the original interpreter:

```powershell
.\Set-AidyInterpretation.ps1 -Mode original
```

Enable the API again:

```powershell
.\Set-AidyInterpretation.ps1 -Mode api
```

The script updates both the source and Release `ai.local.json` atomically. The
running assistant reloads the setting; an outstanding AI request is cancelled.
Already dispatched Windows actions cannot necessarily be stopped or undone.
The original router remains in place, including its existing local intent API
and offline rules. Cloud errors never silently invoke it.

Say “cancel”, “stop”, “abort”, or “never mind” to cancel an active request or
dialogue. Cancellation speech during HTTP is recognized from completed audio
only. Press Escape while the Aidy window has focus for local cancellation
(except while choosing a PTT key). This is not a system-wide Escape hotkey.
To remove an already scheduled action, ask to cancel scheduled task N or all
scheduled tasks. Closing Aidy clears its in-memory schedules and context.

## Provider, model and credentials

The provider is explicitly Google. The default model is `gemini-3.5-flash-lite`,
using Google's `generateContent` endpoint with `responseJsonSchema`. Both the
model and structured output are documented by Google:
[model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite),
[structured output](https://ai.google.dev/gemini-api/docs/structured-output),
[REST reference](https://ai.google.dev/api/generate-content).

`ai.example.json` is secret-free. `ai.local.json` is ignored by Git and contains
only mode/provider/model/timeouts/retry settings. After editing its model, run
the mode-switch script to update the Release copy. `AIDY_AI_CONFIG` can select
another settings file. `AIDY_INTERPRETATION_MODE=original` overrides the mode for
a process. The Google endpoint is fixed in the adapter; credentials cannot be
sent to a model-supplied URL or an HTTP redirect.

The three supplied keys are stored outside the project at
`%LOCALAPPDATA%\Aidy\ai.credentials.local.json`. No key is embedded in source,
tests, documentation, build content, or Git commits. To replace them through
masked interactive input:

```powershell
.\Configure-AidyCredentials.ps1 -Count 3
```

Restart Aidy after changing keys. Alternatively, configure `GEMINI_API_KEY` or
`AIDY_GOOGLE_API_KEYS` (semicolon-separated, up to three keys) in the launching
environment. `AIDY_AI_CREDENTIALS` overrides the local credential-file path.
The file format is an object with `provider: "google"` and a `keys` array; never
commit it. This local prototype uses a local file/environment credential loader
that can be replaced with a server-based credential source for distribution.

All three keys passed one minimal model-metadata GET check. Generation uses one
active key; invalid/expired keys are excluded and only authentication failures
allow bounded fallback. Current default: two attempts, 8-second interpretation deadline,
3-second connect/8-second read timeout. Connections are reused. Rate limits put
all keys into a shared cooldown; daily/billing quota exhaustion stops further
requests until credentials/provider are reloaded after the account issue is
resolved. Retry-After (seconds or date) and Google RetryInfo are respected.
Google documents limits [per project rather than per key](https://ai.google.dev/gemini-api/docs/rate-limits).
The application does not rotate keys to evade limits. Remaining quota is not
inferable from a successful key check; inspect Google AI Studio for that.

No microphone samples, voice embeddings, profile names, or arbitrary document
contents are sent to Google. Requests contain task text, app IDs, and up to eight
recent dialogue/result entries. Context expires after five minutes and is cleared
on authenticated user/role change or mode change. API mode never gives the model
a general-purpose shell, eval, executable path, or arbitrary URL tool.

## Implementation and capability boundaries

- `ai_provider.py`: replaceable Google transport/credentials and strict response parsing.
- `ai_tools.py`: closed tool registry, type/range/target checks and local policy metadata.
- `ai_engine.py`: serial bounded dialogue, six tool steps, confirmations, duplicate guards,
  explicit partial results, context, repeat/undo and up to five delayed tasks.
- `ai_runtime.py`: existing Aidy/Windows executors, profile authorization, same-user
  confirmations, optional process-state checks, dynamic UI and existing SAPI feedback.
- `voice_auth.py`: retains identity matching and adds uncached profile/role/expiry checks
  at execution time. The model never chooses an identity or changes a permission rule.
- WPF bridge: `AI_RESPONSE` displays clarifications/results in the existing last-command
  area; Escape sends a deterministic cancellation control.

See [COMMAND_INVENTORY.md](COMMAND_INVENTORY.md) for the complete 408-phrase,
31-family baseline inventory, parameter limits, permissions and regression map.
New user-facing tools provide current time/date, remaining timer time, and listing
or cancelling scheduled AI tasks. Enrollment, settings, desktop dock, custom slots,
mouse controls, application preferences, study mode and workspace restoration remain.
Local WPF settings/profile-management controls retain their existing password/UI
workflow; they are not instructions delegated to the model.

Plans are fully parsed and validated before dispatch. Each actual action is
authorized again after any confirmation. Dependent steps run in order and stop
on failure, denial or cancellation. A model retry cannot execute a desktop action:
the HTTP worker returns data only, and execution IDs are consumed before calling
an executor. Late responses from cancelled/timed-out requests are discarded.
Scheduled tasks keep an authenticated principal, not voice recordings, and recheck
current profile permissions at execution. They are cleared on identity/mode change.

Only observable state is reported as verified. App process checks can verify a
running/closed process, but not successful login or page loading. Mouse/keyboard,
web launches, shutdown requests, custom modes, and asynchronous study setup may
report “dispatched” with the final state explicitly unverified. Absolute volume
failure is reported instead of silently performing a different adjustment.
Custom slots are frozen at confirmation, use saved configuration, and stop at
the first failed slot. Undo is available only for supported inverses and requires
fresh authorization/confirmation; it cannot restore lost work after force-closing
an application. Existing OS calls are not transactional or generally reversible.

## Verification and measurements

Run deterministic tests without consuming live credentials:

```powershell
.\Run-AidyTests.ps1
dotnet build WpfApp1.csproj -c Release --nologo
```

The test wrapper uses Python UTF-8 and forces original mode for legacy fixtures;
the new tests inject mocked provider responses and explicitly enable their API
path. Running the legacy test fixtures against live API configuration without
that override is not a supported test setup.

Recorded results on this machine:

- Starting snapshot: **174/174 tests pass with UTF-8**. An initial plain-console
  run failed one enrollment test because Windows could not print a checkmark;
  this was reproduced before edits and resolved by the launcher's existing UTF-8 mode.
- Updated suite: **231/231 tests pass**, including 57 new cases for typed tool
  validation, parsing, permissions, confirmations, identity isolation, preferences,
  scheduling, cancellation, malformed responses, timeouts, invalid keys, quotas,
  no fallback, duplicate prevention, partial failures, mode switching and cleanup.
- Release build: zero warnings, zero errors.
- `verify_two_stage.py`: real prerecorded Whisper wake/queued-command replay passes,
  including the command captured while the wake response is being handled. OS and
  audio-output effects were mocked.
- `verify_voice_id.py`: real offline ECAPA 192-dimensional encoding, five synthetic
  enrollment phrases and Admin authorization pass in a temporary profile database.
  This is not live human-speaker discrimination testing.
- Real Gemini smoke test: **8/8 pass**, using synthetic text and mocked authorization,
  confirmation and desktop executors. Median 1,160 ms, minimum 1,077 ms, maximum
  1,490 ms from text submission to mocked result. This sample is too small for a
  reliable API tail percentile. No real application was opened or closed by this test.

The original `verify_latency.py` stops on Whisper's accepted “a d” wake alias where
it expects exact “aidy”; this pre-existing assertion failure was recorded.
`benchmark_ai_pipeline.py` instead retains every transcript and measures the same
two synthesized commands, “open browser” and “close steam”, ten times each.
The final before run imports the preserved starting source snapshot; the after run
imports the edited application in original mode. Both mock OS actions/confirmation/
voice playback. Timing includes test instrumentation overhead.

| Stage, milliseconds | Before median / p95, n=20 | After original mode median / p95, n=20 |
|---|---:|---:|
| Warm endpoint-to-transcript decoding | 167.66 / 175.24 | 164.86 / 173.48 |
| Transcript-to-target interpretation | 2.04 / 3.33 | 1.92 / 2.66 |
| Interpretation-to-mocked dispatch | 0.03 / 0.04 | 0.02 / 0.04 |
| Total transcript-to-mocked dispatch | 2.08 / 3.36 | 1.94 / 2.67 |
| Mock action completion | 0 / 0 | 0 / 0 |

For an exact phrase comparison, two additional real API calls used the same
`open browser` and `close steam` requests with mocked desktop executors: 2,042 ms
and 1,185 ms respectively (n=2, median 1,614 ms). Both selected the expected action
and target. These are text-to-result timings, excluding speech, actual confirmation
and desktop execution; the sample is too small for a tail estimate. Results are in
`.validation/api-same-requests.json`. Reproduce with
`python -X utf8 scripts/verify_ai_smoke.py --compare-baseline` using this project's venv.

These small replay samples do not establish a speed improvement. VAD endpoint
silence remains 240 ms by default and is separate from the decode measurement.
Real OS completion and voice-feedback onset were **not measured** in this replay;
no live end-of-speech number is claimed. Runtime API logs record interpretation,
interpretation-to-dispatch (including permissions/confirmation), executor duration,
and feedback-call onset without prompts or credentials. Executor duration can include
legacy feedback for timer/study actions. A feedback-call timestamp is not an acoustic
measurement of sound starting at the speaker.

An initial candidate model, `gemini-3.1-flash-lite`, produced one fast request but
later took 11–14 seconds or timed out at 15 seconds. A complex repeated union schema
also caused a provider rejection with the full app catalog. The shipped compact
schema keeps tool-specific validation local; stable `gemini-3.5-flash-lite` passed
the full eight-request sequence above. Cloud latency remains variable.

Optional explicit network verification (no OS actions):

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/verify_ai_smoke.py --validate-keys
.\.venv\Scripts\python.exe -X utf8 scripts/verify_ai_smoke.py
```

Detailed machine results are retained in `.validation/api-final-tests.log`,
`api-smoke.json`, `api-before-final-benchmark.json`, `api-after-final-benchmark.json`,
`api-two-stage.log`, `api-voice-id.log`, and `api-build.log`.

Live microphone use, live human Voice ID, audible response timing, and real desktop
side effects have not been tested in this change. The existing Windows/hardware
limitations remain: not every display exposes brightness controls, and a process
launch does not prove the application is usable. No recognition model or speech
language was changed or downloaded for this integration. No new dependency is required.

## Restore the exact starting source behavior

The pre-edit snapshot is
`C:\Users\Rami\Desktop\Aidy-main\Aidy-api-baseline-20261107`.
Its SHA-256 manifest covers 323 source/configuration/local-addition files. Personal
voice databases, generated dependencies/build output, logs, and credentials were
excluded. None of these were needed to recover the files modified by this work.

Close this Aidy instance, then run:

```powershell
& 'C:\Users\Rami\Desktop\Aidy-main\Aidy-api-baseline-20261107\Restore.ps1' -VerifyOnly
& 'C:\Users\Rami\Desktop\Aidy-main\Aidy-api-baseline-20261107\Restore.ps1'
```

The script verifies backup hashes and confines every target to this application.
It first archives current versions of every affected file, restores only the
original files changed by this integration, moves newly added implementation files
and their copied Python payloads into the archive, and rebuilds Release. It preserves
pre-existing local additions, current voice profiles, user settings, credentials,
dependencies, unrelated files, and Git metadata. Later source edits are preserved
in the `before-restore-*` archive rather than discarded. No destructive Git reset
or recursive deletion is used. Full source restoration requires the .NET 8 SDK;
the immediate original-mode switch above needs no rebuild.
