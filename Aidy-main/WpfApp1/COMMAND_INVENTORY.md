# Aidy command and regression inventory

Current audit: [AGENT_COMMAND_AUDIT.md](AGENT_COMMAND_AUDIT.md). The CSV inventory
below is not a phrase allow-list for API mode. The current 35 tool families compose
new tasks, including 17 browser and 9 file operations. Browser batch operations
support all/other/matching/except tabs, selecting titles/positions and preserving
unrelated windows; file operations now include listing, scoped search and copying.

Full API interpretation builds an ordered semantic goal plan from the whole
utterance. Known later actions persist while current UI actions are replanned.
Completion criteria bind actual app/document/file outcomes; document saves refer
to verified writing goals. Completed summaries preserve context for “the first
one”; clarifications retain later goals. Existing permissions and confirmations
apply to every effect.

`document_write(application, new_document, text, word_count, finish_task)` composes editor
creation and verified text insertion for Notepad/Word; the model supplies the
complete generated content. Recent document HWND/PID targets support contextual
closing. Writing requests are verified as a whole before completion is accepted.
Word counts target ±5% unless “exactly” is requested; actual counts are reported.
Scoped ASR hints handle the reported essay/saying/say confusions and spoken counts,
while the original transcript still reaches Google. finish_task permits completion
only after verified insertion and when the planner says no further action remains.

The API also exposes `observe_desktop`, `window_action`, `ui_action`, `key_input`,
`mouse_input`, `browser_action`, `open_url`, `file_action`, `save_document` and the
data-only `task_complete`. They compose new tasks without adding phrase intents.
Installed app IDs come from actual Windows registrations. See
[UNIVERSAL_AGENT.md](UNIVERSAL_AGENT.md) for scopes, confirmations and verification.

Baseline: `whisper-added-v1`, `bd3dedcd95c1fa022b116fe8a6f6ecc976a53b29`.
All 166 published application files matched the local starting state. No applicable AGENTS.md was found in the application, ancestors, or branch tree.

The existing `commands.csv` contains 408 entries in 31 intent families. These are recognition hints, not the new AI router. The original CSV and deterministic router are unchanged. The API accepts natural phrasing and validates a closed tool vocabulary.

Speech endpoints now wait 700 ms for normal/combined wake-command phrases; confident standalone wake words retain their short endpoint. Pending numeric step answers use a separate recognition context and normalize complete numbers without changing general command words. Simple explicit named-app tasks use a compact Google schema; contextual and multi-action tasks retain the full AI vocabulary. Every accepted task still makes an AI interpretation request.

| Baseline capability | API tool / arguments | Execution permission with Voice ID | Confirmation / context |
|---|---|---|---|
| Open/close apps and known websites | open_app / close_app: exact app ID | open_app / close_app; Guest allowed | Close always confirmed. $last resolves only from recent executed results; $active is pinned before confirmation. |
| Close a visible window/current app | close_window: current window ID or $active | close_active; Guest allowed | Frozen HWND/PID, confirmation, WM_CLOSE and post-check; no process-wide killing. |
| Close Aidy itself | exit_assistant | close_app; Guest allowed | Explicit self-close only, confirmed, graceful WPF exit; terminal step. |
| Close everything with exceptions | close_everything: except_apps, up to 8 app/window IDs | close_everything; Guest allowed | Exceptions must resolve before confirmation; protected windows and matching exceptions remain open. |
| Preferred browser and music | open_app browser/music | open_app; Guest allowed | Resolves locally to configured preference; music opens the player, does not guarantee playback. |
| Installed ChatGPT desktop app | open_app chatgpt_app | open_app; Guest allowed | Discovered from the real Windows AppsFolder registration. Explicit website uses gpt; no silent website substitution. |
| Browser search from speech | web_search: query, 1..500 characters | open_app; Guest allowed | Encoded Google search URL in saved/default browser; no arbitrary URL, shell command or active-window typing. |
| Volume adjustments, numeric/more/less follow-ups | volume_change: direction, 1..10 steps; set_volume: 0..100 | volume_change / set_volume; Guest allowed | Missing amount clarified; no fallback from failed absolute volume to relative adjustment. |
| Brightness and follow-ups | brightness_change: direction, 1..10 steps | brightness_change; Guest allowed | Missing amount stops the entire plan before execution. One numeric answer fills all pending controls, preserving explicit amounts; hardware support required. |
| Shutdown/restart | system_action | Admin only | Fresh same-user confirmation before each action; schedules reconfirm permissions when due. |
| Lock, desktop, screenshot | system_action | Existing allow-list; Guest allowed | Reports dispatched if final state cannot be checked. |
| Empty command prompt/task manager | system_action | Admin or User | No model-supplied code or command-line parameters. |
| Mouse movement/click/double/right/scroll | mouse_action: fixed action enum | mouse_control; Guest allowed | Saved distances/scroll settings retained. |
| Alt-Tab selection | window_switch: start/left/right/done/cancel | switch_window; Guest allowed | Each task input interpreted in API mode; cancellation stays local. |
| Countdown timer | timer_start: 1..43200 seconds; timer_cancel | timer / cancel_timer; Guest allowed | Existing timer badge, sound and watchdog retained. |
| Study mode | study_mode: start/stop/status | Corresponding study permission; Guest allowed | Start/stop confirmed; workspace restoration binds to the initiating authenticated user in API mode. |
| Custom/local mode | custom_mode: start/stop | Admin only | Saved slot list shown in confirmation and frozen; up to 8 slots, stop on failure. Local mode means saved custom mode, not interpreter selection. |
| Assistant mute/unmute | assistant_audio: muted boolean | mute / unmute; Guest allowed | Existing audio/echo architecture; response remains visible while muted. |
| Repeat / undo last or chain | repeat; undo: last/chain | Expanded underlying actions reauthorized | Inverses re-confirm closing; refuses irreversible actions. |
| Delayed tasks | delay_seconds on one eligible tool, 1..3600 | Underlying action's permission | Max 5 scheduled tasks; permissions rechecked; not persisted across restart/mode/identity change. |
| Voice access grant/revoke/list | voice_access: action, role, duration 0..2592000 | Admin only | Same-speaker confirmation; grant uses existing pending-unknown enrollment mechanism. No biometric data sent to API. |
| Voice enrollment / profile settings / settings / dock | Existing WPF and Python controls | Existing local settings/password workflow | UI controls retained; no new model tool to alter identity or enroll a profile. |
| New: timer remaining | timer_status | timer; Guest allowed | Reads local timer state. |
| New: time/date | current_time | timer; Guest allowed | Reads Windows local clock. |
| New: scheduled task inspection/cancellation | scheduled_status / cancel_scheduled: ID (0=all) | timer / cancel_timer; Guest allowed | Uses current identity session's AI task queue. |

When Voice ID is off, the existing local-user policy is retained. With Voice ID on, authentication precedes cloud inference and execution checks current profile ID, role, expiry and revocation. Context is not shared between two users with the same role. All accepted voice task commands use the API in API mode; activation, cancellation, confirmation yes/no and local settings are deterministic controls. Original-mode parsing is never a fallback on cloud failure.

Flow: BufferedInput microphone capture -> existing VAD/endpoints -> wake/PTT gate -> existing English Whisper tiny.en final transcript -> Voice ID -> Gemini structured plan -> whole-plan typed validation and missing-amount gate -> permission/confirmation -> existing Windows actions -> observed result or dispatch status -> concise AI_RESPONSE UI feedback and guarded clips/SAPI playback. Independent closing chains share one confirmation with per-action target bindings and permission checks. Model communication uses a reused requests.Session on one data-only daemon worker; UI and audio callback threads never perform HTTP. The assistant thread owns confirmations and action dispatch. It captures only cancellation during HTTP; cancellation decoding is off-thread and late results are ignored.

Regression evidence: original Python suite, test_ai_layer.py, verify_two_stage.py, verify_voice_id.py, real-API verify_ai_smoke.py with mocked actions, and benchmark_ai_pipeline.py. Tests do not establish that every natural-language paraphrase or every installed desktop application works.

## Complete baseline phrase inventory

### brightness down (8)

`brightness down`, `decrease brightness`, `dim screen`, `make screen darker`, `turn down brightness`, `lower brightness`, `reduce brightness`, `too bright`

### brightness up (8)

`brightness up`, `increase brightness`, `make screen brighter`, `brighten screen`, `turn up brightness`, `raise brightness`, `screen too dark`, `too dark`

### click (3)

`click`, `left click`, `mouse click`

### close active (10)

`close this`, `close it`, `close window`, `close current window`, `close current app`, `close current application`, `close active app`, `close active window`, `close this window`, `close this app`

### close app (52)

`close youtube`, `close you tube`, `close utube`, `close yt`, `quit youtube`, `stop youtube`, `close gpt`, `close g p t`, `close chatgpt`, `close chat gpt`, `quit gpt`, `stop gpt`, `close yandex music`, `quit yandex music`, `exit yandex music`, `stop yandex music`, `close music`, `quit music`, `exit music`, `stop music`, `close spotify`, `quit spotify`, `exit spotify`, `stop spotify`, `close telegram`, `quit telegram`, `exit telegram`, `close tg`, `quit tg`, `close discord`, `quit discord`, `exit discord`, `close chrome`, `quit chrome`, `exit chrome`, `close browser`, `quit browser`, `close opera`, `quit opera`, `exit opera`, `close yandex browser`, `quit yandex browser`, `exit yandex browser`, `close yandex`, `quit yandex`, `close vscode`, `quit vscode`, `exit vscode`, `close visual studio code`, `close steam`, `quit steam`, `exit steam`

### close everything (5)

`close everything`, `close all`, `close all apps`, `close all applications`, `close everything please`

### custom_mode (45)

`custom mode`, `custom mode on`, `enable custom mode`, `turn on custom mode`, `start custom mode`, `custom mod`, `custom mold`, `custom mode please`, `personal mode`, `personal mode on`, `enable personal mode`, `turn on personal mode`, `start personal mode`, `local mode`, `local mode on`, `enable local mode`, `turn on local mode`, `start local mode`, `my mode`, `my mode on`, `start my mode`, `quick mode`, `quick mode on`, `start quick mode`, `custom mode off`, `disable custom mode`, `turn off custom mode`, `stop custom mode`, `exit custom mode`, `personal mode off`, `disable personal mode`, `turn off personal mode`, `stop personal mode`, `exit personal mode`, `local mode off`, `disable local mode`, `turn off local mode`, `stop local mode`, `exit local mode`, `my mode off`, `stop my mode`, `exit my mode`, `quick mode off`, `stop quick mode`, `exit quick mode`

### double_click (2)

`double click`, `double`

### lock (5)

`lock`, `lock screen`, `lock pc`, `lock computer`, `secure computer`

### mouse_down (3)

`mouse down`, `move down`, `cursor down`

### mouse_left (3)

`mouse left`, `move left`, `cursor left`

### mouse_right (3)

`mouse right`, `move right`, `cursor right`

### mouse_up (3)

`mouse up`, `move up`, `cursor up`

### mute (11)

`mute`, `mute aidy`, `mute ady`, `mute eddie`, `mute edy`, `shut up`, `shutup`, `shat up`, `shut ap`, `shut op`, `shot up`

### open app (119)

`open chrome`, `open google chrome`, `open browser`, `open the browser`, `start the browser`, `launch the browser`, `open my browser`, `launch chrome`, `start chrome`, `run chrome`, `open opera`, `open opera browser`, `open the opera browser`, `launch opera`, `start opera`, `run opera`, `open yandex`, `open yandex browser`, `open the yandex browser`, `open my yandex`, `launch yandex`, `start yandex browser`, `open spotify`, `launch spotify`, `start spotify`, `run spotify`, `play spotify`, `open telegram`, `open tg`, `launch telegram`, `launch tg`, `start telegram`, `start tg`, `run telegram`, `open telegram desktop`, `open my telegram`, `open discord`, `launch discord`, `start discord`, `run discord`, `open discord app`, `open my discord`, `open explorer`, `open file explorer`, `open files`, `launch explorer`, `start explorer`, `open my files`, `show files`, `go to files`, `open settings`, `open windows settings`, `launch settings`, `start settings`, `open system settings`, `open pc settings`, `go to settings`, `show settings`, `open calculator`, `open calc`, `launch calculator`, `start calculator`, `run calculator`, `open the calculator`, `open notepad`, `launch notepad`, `start notepad`, `run notepad`, `open text editor`, `open notes`, `open vscode`, `open visual studio code`, `open vs code`, `start coding`, `open code editor`, `open steam`, `launch steam`, `start steam`, `run steam`, `open steam app`, `start steam client`, `open my games`, `open youtube`, `open up youtube`, `open you tube`, `open u tube`, `open utube`, `open you toob`, `open you too`, `open youtub`, `go youtube`, `launch youtube`, `start youtube`, `run youtube`, `go to youtube`, `visit youtube`, `show youtube`, `open yt`, `open up yt`, `launch yt`, `start yt`, `open gpt`, `open up gpt`, `open gp t`, `open g p t`, `launch gpt`, `start gpt`, `run gpt`, `go to gpt`, `visit gpt`, `show gpt`, `go gpt`, `open chatgpt`, `open chat gpt`, `open chat gp t`, `open chat g p t`, `open chat pt`, `launch chatgpt`, `start chatgpt`

### open cmd (6)

`open cmd`, `open command prompt`, `command prompt`, `open terminal`, `terminal`, `cmd`

### play_music (18)

`open yandex music`, `launch yandex music`, `start yandex music`, `run yandex music`, `yandex music`, `open music`, `play music`, `start music`, `music`, `open my music`, `open the music`, `open music player`, `play on yandex music`, `play on music`, `play a song`, `i want to listen to music`, `put on music`, `put on some music`

### repeat (12)

`repeat`, `repeat that`, `repeat it`, `repeat last command`, `repeat last`, `do it again`, `again`, `repeet`, `repete`, `repit`, `repet`, `ripit`

### restart (5)

`restart`, `restart pc`, `restart computer`, `reboot`, `reboot system`

### right_click (1)

`right click`

### screenshot (5)

`screenshot`, `take screenshot`, `capture screen`, `screen capture`, `grab screen`

### scroll_down (2)

`scroll down`, `scroll bottom`

### scroll_up (2)

`scroll up`, `scroll top`

### show desktop (5)

`show desktop`, `go to desktop`, `minimize all`, `hide windows`, `desktop view`

### shutdown (6)

`shutdown`, `shut down pc`, `power off`, `turn computer off`, `shut it down`, `system shutdown`

### switch window (5)

`switch window`, `switch app`, `next window`, `change window`, `cycle windows`

### task manager (5)

`task manager`, `open task manager`, `show task manager`, `show processes`, `process manager`

### undo_action (17)

`undo`, `undo last`, `undo that`, `undo it`, `go back`, `revert`, `cancel that`, `undo all`, `undo everything`, `undo all that`, `undo everything you did`, `undo all you did`, `undo the last actions`, `cancel everything`, `cancel all`, `cancel all actions`, `cancel everything you did`

### unmute (17)

`unmute`, `unmute aidy`, `unmute ady`, `unmute eddie`, `unmute edy`, `un mute`, `an mute`, `and mute`, `on mute`, `one mute`, `unmuted`, `unmoot`, `unmoot aidy`, `sound back`, `sound on`, `turn on sound`, `turn sound on`

### volume down (10)

`volume down`, `decrease volume`, `turn it down`, `make it quieter`, `quieter`, `lower volume`, `reduce volume`, `sound down`, `less volume`, `too loud`

### volume up (12)

`volume up`, `increase volume`, `turn it up`, `make it louder`, `louder`, `raise volume`, `sound up`, `audio up`, `more volume`, `too quiet`, `cant hear it`, `can't hear it`
