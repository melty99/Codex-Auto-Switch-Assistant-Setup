# Codex Auto Switch Assistant

Save your sleep

Version: **v1.0** (plugin version `1.0.0`). Developed by **melty** using **Codex**.

[简体中文](README.zh-CN.md) · [Quick start](QUICK_START.md) · [Verification record](VERIFICATION.md)

A local Windows tool that monitors Codex subscription quota, switches accounts through CC Switch, and attempts to continue local chats it has paused. It includes a Chinese/English settings interface and a separate task queue for each chat.

**This is a locally tested release.** Continuing a chat does not guarantee lossless recovery of every task or external tool operation. See the limits below.

## Quick start

On a new device, prepare Codex, CC Switch and your signed-in accounts. Extract the setup bundle to a permanent folder, then ask Codex:

> The files are in “XXX”. Follow “INSTALL_WITH_CODEX.md” in that folder to install, configure and verify Codex Auto Switch Assistant.

After installation, open CC Switch's **Codex** provider page and select **不使用项目** (no project). Open **setting.cmd** (or **Open Settings.cmd**) in the extracted release folder and run **Quick setup**. Enable the plugin in Codex, enable automatic management and resumption of assistant-paused tasks in Settings, save, then turn **Auto switch accounts** green. Confirm that current quota is readable and the monitor reports no error. You do not need to invoke the Skill in each chat.

For an existing installation, open Settings directly; updates preserve saved thresholds and switches. See [Quick start](QUICK_START.md) for the short checklist.

## Requirements

- Windows, the official Store version of Codex Desktop, and a Codex CLI that supports local plugin installation.
- Managed ChatGPT accounts imported and signed in through CC Switch. Account switching needs at least two accounts; API keys and third-party model providers are outside this scope.
- Python 3.11+ with tkinter and Node.js 18+. Existing Codex runtimes are preferred; no additional pip/npm dependencies are required.
- Codex and CC Switch must use the same Codex configuration directory. Quick setup checks paths and directories; fill in any missing paths manually.

The verified adapter baselines are **Codex Desktop 26.924.2738.0 and the Chinese CC Switch 3.20.1 interface**. English in this assistant does not imply support for CC Switch's English interface.

The setup bundle contains an installation guide, local marketplace, plugin and its one required Skill. It does not contain application installers, credentials or chats from another device. Keep the extracted folder after installation: it remains the local marketplace source. Opening the settings launcher alone does not install the plugin.

## Everyday use

### Home

The account list shows remaining 5-hour and weekly quota, reading times and reset countdowns. The 5-hour countdown uses hours; the weekly countdown uses days, both with one decimal place. `≈` means an estimate, `—` means missing or failed data, and **Refresh needed** means the previous estimate has elapsed. Cached readings are marked and are not new query results.

**Switch account now** selects another account using the saved rule. It does not require the automatic switching toggle, but the plugin, automatic management and automatic resumption must be enabled. Manual and automatic switching use the same sequence:

1. Check candidate quota and save records of tasks paused by the assistant.
2. Confirm that Codex has exited normally.
3. Activate the target account through CC Switch, then reopen Codex.
4. Check current quota and the original chat state before continuing eligible tasks.

An unconfirmed exit or uncertain operation stops the sequence for review. The assistant does not force-kill Codex or blindly repeat a switch. A submitted request is not a completed switch; follow the actual progress display.

Both switching modes support two selection rules:

- **Highest combined quota**: rank by `√(5-hour remaining percentage × weekly remaining percentage)`. This is a local ranking score, not a token estimate.
- **Custom account priority**: read CC Switch accounts and move them up or down to set priority.

Both rules skip the current account, accounts whose 5-hour quota does not exceed the resume threshold, exhausted weekly quota and failed queries. The list shows `100% − used percentage`; selection reserves an additional 0.5 percentage point because CC Switch rounds its displayed percentages.

If quota queries fail after a VPN change, wait for the connection to settle and click **Refresh CC Switch**. It requeries accounts and retries each failed query once, without switching accounts or restarting Codex. Background refresh has been tested while minimized and hidden without taking foreground focus. An unreadable page produces an error instead of opening the window. This cannot repair the VPN or an unresponsive CC Switch process.

### Settings and switches

| Control | Purpose |
| --- | --- |
| Codex plugin toggle | Controls plugin automation; disabling takes effect at the next check |
| Automatic management | Separate master switch inside the assistant |
| Resume assistant-paused tasks when quota recovers | Controls task continuation; switching also requires it |
| Auto switch accounts | Green means on, gray means off; changes save immediately |
| Quick setup | Detects and saves local paths, validates directories and configures current-user sign-in startup |
| English / 中文 | Changes the assistant's interface language |

Click the relevant **Save settings** button after changing thresholds, account selection, polling or version options. The two reset-wait checkboxes save immediately. Quick setup preserves disabled switches rather than enabling account switching for you.

## Quota and recovery rules

Eligible tasks pause when the current account's 5-hour remaining quota is **at or below the pause threshold**, or weekly quota is exhausted. Resumption requires 5-hour quota **strictly above the resume threshold** and weekly quota **above zero**. After switching, these checks use the new account. Missing, stale or failed quota readings block automatic resumption.

The new-device installation guide initializes **5% pause / 10% resume**, then enables monitoring after verification; automatic switching stays off initially. The program's base defaults, when used directly, instead set pause to **1%**. Both installation paths preserve existing settings. Saved settings determine actual behavior.

Adaptive polling defaults are editable:

| Current account's 5-hour remaining quota | Check interval |
| --- | --- |
| ≥ 20% | 3 minutes |
| ≥ 10% and < 20% | 1 minute |
| < 10% | 30 seconds |

At the pause threshold, during quota errors or recovery checks, the low-quota interval applies. Disabling adaptive polling uses the fixed interval. These are periodic checks: quota can cross several boundaries between readings, especially with concurrent tasks.

### When no account has enough quota

With reset waiting enabled, the assistant chooses the earliest known 5-hour reset among accounts with weekly quota available, saves pause records and exits Codex. It keeps checking quota. Natural resets schedule reopening at the verified reset time **plus five minutes**; early recovery can reopen sooner after two consecutive qualifying readings. Reopening does not itself authorize a continuation message.

Turning off **Automatically restart tasks when quota recovers** preserves the wait plan and monitoring but prevents reopening or continuation; turning it on checks again. Turning off the earliest-reset option cancels the wait plan. Exhausted weekly quota, unknown reset times or failed queries may prevent scheduling. Monitoring cannot run while the computer is asleep or shut down.

### Tasks, recovery and queues

Only local tasks paused by this assistant for low quota, with their stopped state confirmed, can continue automatically. Manually stopped tasks, pending user input or approval, and chats taken over by the user are excluded. Recovery preserves the original chat, context and permissions. Uncertain sends require review and are not automatically repeated.

Right-click a chat under **Tasks & recovery** to add a task or manage its queue. The next item is sent in order after the preceding turn reports completion, the chat is idle and quota qualifies. One fresh qualifying quota reading is enough for queue dispatch; quota is checked again after each send. A completed turn does not prove that every part of an arbitrary written objective was achieved.

Recovery of an interrupted task retains its separate two-reading check and other safeguards. Existing items in Codex's native queue, a failed or manually interrupted task, or new user messages can block or pause the assistant queue. Pending and processed entries can be deleted; sending, running or uncertain entries must be resolved first. Deleting a queue entry does not delete chat messages.

## Compatibility and limits

- Version tracking checks installed versions and official release information. Existing adapters may be reused after interface checks. The assistant does not upgrade Codex/CC Switch or write its own patches; future versions may need manual adaptation.
- Chat control uses version-dependent local internal interfaces, not a stable public API. Unknown protocols or UI structures stop affected operations.
- Window discovery has been improved for Windows virtual desktops. The assistant does not move windows or switch desktops. A complete live cross-desktop exit/switch/reopen/resume test remains outstanding, and Windows determines where reopened windows appear.
- Full live quota-exhaustion recovery across multiple chats remains unverified. Cloud tasks, remote devices, locked sessions and other Windows user sessions are outside the current scope. External operations cannot be guaranteed reversible or losslessly resumable.

See [VERIFICATION.md](VERIFICATION.md) for test coverage. Passing unit tests or read-only probes does not establish complete live switching and task recovery.

## Data and development

Runtime data normally lives under `Codex/QuotaGuard` in the Windows Documents folder. It contains local paths, settings, quota summaries and pause records. `task-queues.json` also contains the full task text you enter, stored locally as plain text. Do not distribute this directory or delete records while recovery is pending. The assistant does not independently copy or store OAuth tokens; Codex and CC Switch manage login and account configuration.

The display name is **Codex Auto Switch Assistant**; the internal ID remains `codex-quota-guard`. Its Skill is included in the plugin and needs no separate global installation. A current-user Windows scheduled task runs the monitor independently. Closing Settings does not stop monitoring; disable the plugin or automatic management to stop automation.

From the plugin directory, developers can run:

```powershell
python -X utf8 -m unittest discover -s tests -p 'test_*.py'
node --test tests/bridge.test.cjs
```

Use `CODEX_QUOTA_GUARD_DATA` for isolated UI testing. Do not treat real Codex exits, account switches or task submissions as routine UI tests.
