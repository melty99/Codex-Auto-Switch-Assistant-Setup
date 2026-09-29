
你在使用Codex时是否遇到了这些问题？5h额度用完了任务直接中断了需要手动继续；Tobi宣布马上重置，赶紧熬夜猛猛蹬；额度耗尽导致事先输入的任务被一股脑输入，没有运行任务也就算了，原先的任务还可能受影响；熬夜等重置跑任务...我使用Codex构建了这个自动切换助手，用于自动监测多个账户的额度。当账户A的5h额度用完，它会自动停止任务并切换至账号B(如果额度充足）并继续任务。当多个账户额度都不足时，它会帮你在5h额度重置后自动重新启动任务。你还可以添加任务队列，它会在额度足够的情况下自动输入下一个任务（通过任务与恢复记录界面右键你选中的任务进入）。

刚需CCswitch

Have you encountered these issues while using Codex? Tasks abruptly stop when your 5-hour quota runs out, requiring manual resumption; Tobi announces an imminent reset, so you stay up late to rush through tasks; when quotas are depleted, previously entered tasks get flushed all at once—worst case, not only do running tasks fail, but previous ones might also be affected; staying awake just to wait for the reset and run tasks again... I've built this automatic switching assistant with Codex to monitor multiple accounts' quotas. When Account A's 5-hour quota is exhausted, it automatically pauses the task and switches to Account B (if there's sufficient quota) to continue. If all accounts lack quota, it will automatically restart the task once the 5-hour quota resets. You can also add a task queue—it will automatically input the next task in line as soon as quota becomes available (accessed by right-clicking on a selected task in the task and recovery log interface).
CCswitch is necessary

<img width="500" height="600" alt="codex-auto-switch-poster-en" src="https://github.com/user-attachments/assets/621a0250-acf7-4c5a-90ee-f17a2c3e3a2b" />
<img width="500" height="600" alt="codex-auto-switch-poster-v5" src="https://github.com/user-attachments/assets/9df19903-29cb-4558-923a-f79924474dc9" />


# Codex Auto Switch Assistant · 新设备安装包

Save your sleep

版本：**v1.0**（插件版本 `1.0.0`）。由 **melty** 使用 **Codex** 开发。

Developed by **melty** using **Codex**.

## 几句话跑起来

在 Windows 上准备好 Codex、CC Switch 及已登录的账号，把整个安装包解压到固定目录，然后将下面一句话发给 Codex：

> 文件目录在“XXX”，请根据该目录中“INSTALL_WITH_CODEX.md”的内容，安装并调试 Codex 自动切换小助手。

安装后，打开 CC Switch 的 Codex 供应商页面并选择“不使用项目”，双击最外层的“setting.cmd”（或“打开设置.cmd”），点击“一键配置”。启用 Codex 插件栏中的本插件，在助手中开启自动管理和自动继续并保存，将右上角“自动切换账号”点成绿色；确认额度可读、后台没有错误即可使用。

`XXX` 替换为包含本文件的解压目录。已有安装保留原设置；不想自动换号时，让按钮保持灰色。无需在每个聊天重复调用 Skill，关闭设置窗口也不会停止后台。

[简明使用说明（中英文）](QUICK_START.md) · [完整中文说明](README.zh-CN.md) · [English README](README.en.md)

## 安装前须知

- 需要 Windows、官方商店版 Codex Desktop、支持本地插件的 Codex CLI、CC Switch、Python 3.11+（含 tkinter）和 Node.js 18+。优先使用已有运行环境，无需额外安装 pip/npm 依赖。
- 用户需自行完成登录和账号导入；自动换号需要至少两个托管 ChatGPT 账号。本包不迁移旧设备的聊天、任务队列或恢复记录。
- 保留隐藏的 `.agents` 和 `.codex-plugin` 文件夹；不要在 ZIP 内直接运行。安装后保留解压目录，它仍是本地插件市场的来源。
- 安装指南为新设备初始化暂停 5%、恢复严格大于 10%，周剩余必须大于 0；验收后启用监测，自动换号默认关闭。已有安装不覆盖用户阈值或开关。
- 当前为本机测试版，已验证基线为 Codex Desktop 26.924.2738.0、中文 CC Switch 3.20.1。未来版本需重新检查，完整真实额度耗尽后的多聊天恢复仍未端到端验收。

## 文件用途

| 文件或目录 | 用途 |
| --- | --- |
| [INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md) | 给 Codex 的安装、配置与验收步骤 |
| `.agents/plugins/marketplace.json` | 本地插件市场入口 |
| `plugins/codex-quota-guard/` | 插件、运行脚本、设置入口及唯一必需的内置 Skill |
| `plugins/codex-quota-guard/tests/` | 安装调试用测试，不参与日常运行 |
| `tools/readiness.py` | 文件校验和只读环境检查 |
| `SHA256SUMS.json` | 文件完整性清单，不是发布者数字签名 |
| [INSTALL_REPORT.template.md](INSTALL_REPORT.template.md) | 新设备安装结果模板 |

不需要额外安装开发用 Skill 或其他插件。本包不包含应用安装程序、登录凭据、账号数据库和个人运行记录。

## English: getting started

Extract the bundle to a permanent folder on Windows and ask Codex: “The files are in XXX. Follow INSTALL_WITH_CODEX.md to install, configure and verify Codex Auto Switch Assistant.” After installation, open CC Switch's Codex provider page with no project selected, open **setting.cmd** in the release folder, and run **Quick setup**. Enable the plugin, automatic management and task resumption, save, then turn **Auto switch accounts** green and confirm readable quota with no monitor error.

Keep the extracted folder after installation. See the [English README](README.en.md) for requirements and recovery limits.
