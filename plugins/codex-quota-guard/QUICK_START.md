# 简明使用说明 / Quick start

## 中文

1. **安装一次。** 在 Windows 上准备好已登录的 Codex 和 CC Switch，解压新设备安装包，并告诉 Codex：`文件目录在“XXX”，请根据“INSTALL_WITH_CODEX.md”安装并调试 Codex 自动切换小助手。`
2. **配置路径。** 安装后打开 CC Switch 的 Codex 供应商页面，选择“不使用项目”；双击发布包最外层的“setting.cmd”（或“打开设置.cmd”），点击“一键配置”。
3. **开启运行。** 启用 Codex 插件栏中的本插件，在助手设置中开启“启用自动管理”和“额度恢复后自动继续被守护程序暂停的任务”并保存，再将右上角“自动切换账号”点成绿色；确认额度可读、后台无错误即可。

`XXX` 是解压后包含 `INSTALL_WITH_CODEX.md` 的文件夹路径；已有安装从第 2 步开始。阈值可在设置中修改，日常不必在每个聊天调用 Skill。关闭设置窗口后后台继续运行；关闭插件或自动管理可停止自动操作。

运行条件、功能边界和恢复限制见 [中文 README](README.zh-CN.md)。自动换号需要至少两个已登录的托管 ChatGPT 账号；本工具不保证全部任务都能无损恢复。

Tips:
建议本程序，Codex，ccswitch都在同一个桌面；

## English

1. **Install once.** On Windows, prepare signed-in Codex and CC Switch accounts, extract the setup bundle, and tell Codex: `The files are in “XXX”. Follow “INSTALL_WITH_CODEX.md” to install, configure and verify Codex Auto Switch Assistant.`
2. **Configure paths.** After installation, open CC Switch's Codex provider page and select “不使用项目” (no project). Open “setting.cmd” (or “Open Settings.cmd”) in the extracted release folder and run “Quick setup”.
3. **Enable operation.** Enable the plugin in Codex, enable automatic management and resumption of assistant-paused tasks in Settings, save, then turn “Auto switch accounts” green. Confirm that quota is readable and the monitor reports no error.

`XXX` is the extracted folder containing `INSTALL_WITH_CODEX.md`. Existing installations can start at step 2. Thresholds are editable; no per-chat Skill invocation is needed. Closing Settings leaves monitoring active; disable the plugin or automatic management to stop automation.

See the [English README](README.md) for requirements and limits. Account switching needs at least two signed-in managed ChatGPT accounts. Lossless recovery of every task is not guaranteed.

Tips:
We recommend keeping this assistant, Codex, and CC Switch on the same Windows virtual desktop.
