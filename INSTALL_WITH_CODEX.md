# 给 Codex 的安装与调试指令

用户要求按本文件安装时，请完成安装、配置和验证，最后报告结果。保留既有权限与审批要求；恢复中断工作时，先检查已有状态，不重复结果不明的操作。

## 1. 范围与文件检查

- 产品名称：Codex Auto Switch Assistant；内部插件名：`codex-quota-guard`；本包市场名：`codex-auto-switch-assistant`。
- 先读根目录 `README.md`、`README.en.md`、`QUICK_START.md` 和插件内 `skills/codex-quota-guard/SKILL.md`。只安装本插件及其内置 Skill，不另装全局 Skill 或开发插件。
- 仅支持 Windows。确认已完整解压到固定目录，不在 ZIP 内运行；保留隐藏目录，安装后不要删除本地市场源目录。
- 用 Python 3.11+ 执行 `tools/readiness.py --verify-only`。失败时先修复文件完整性，不能跳过校验；SHA256 不是发布者数字签名。

## 2. 发现本机环境

使用当前设备的实际路径，不照搬其他机器配置。需要：Python 3.11+（tkinter、tomllib）、Node.js 18+、支持本地插件市场的 Codex CLI、官方商店版 Codex Desktop、CC Switch。

1. 优先寻找 Codex 已有运行环境，再查 PATH；实际执行 Python 导入检查、Node `--version`、CLI `--version` 和 `plugin add --help`，不能只确认文件存在。
2. 识别 Windows `OpenAI.Codex` 应用包。便携版 CC Switch 建议先运行，再从进程识别路径；缺失路径向用户询问。
3. 确认 Codex 的真实配置目录、当前进程 `CODEX_HOME` 和 CC Switch 的 `codexConfigDir` 一致。不要通过复制登录文件或修改账号数据库来消除冲突。
4. 运行 `tools/readiness.py` 进行只读环境检查。尚未安装时配置校验未通过是预期情况；安装后仍须验证。

本包不需要 pip/npm 依赖。缺少应用时使用可信的官方安装方式，在用户授权范围内补齐；登录和账号导入由用户完成。不要输出令牌、完整配置文件、账号数据库或聊天记录，不修改全局 PowerShell 执行策略。

## 3. 检查已有安装与计划

用 CLI `plugin list --json` 查找本插件，仅提取来源、版本、启用状态和安装路径。

- 已有安装优先复用原来源，不重复注册另一个同名插件。保留阈值、开关、队列、账号顺序和恢复记录，默认值更新不覆盖已有自定义设置。
- 检查 `status.json` 和 `reset-wait.json` 是否有进行中的换号或恢复计划。计划未结束或外部操作结果不明确时，先核验现场；不要用重新安装重放操作。
- 本包市场名已存在时核对目录；不同目录不能覆盖无关市场。
- 首次安装只在运行数据目录完全空白时初始化下列设置，记录它们确实是本次新建的。不要凭现有开关为 false 判断是首次安装。

```python
# 独立 Python 进程；PLUGIN_SOURCE 为本包插件目录的绝对路径。
import sys
from pathlib import Path
sys.path.insert(0, str(Path(PLUGIN_SOURCE)/'scripts'))
from runtime import data_dir
from core import atomic_json, validate_settings
folder = data_dir()
if folder.exists() and any(folder.iterdir()):
    raise RuntimeError('已有运行数据，不能覆盖首次安装设置')
atomic_json(folder/'settings.json', validate_settings({
    'enabled': False, 'auto_switch': False, 'auto_resume': True,
    'pause_5h': 5, 'resume_5h': 10, 'checkpoint': 5,
}))
```

## 4. 安装插件

下面 `$Bundle`、`$Python`、`$Codex`、`$Node` 都必须是本机核实的绝对路径。只为相关进程设置正确的 `CODEX_HOME`。没有已有插件来源时执行：

```powershell
& $Codex plugin marketplace add $Bundle --json
if ($LASTEXITCODE -ne 0) { throw 'Marketplace registration failed' }
& $Codex plugin add 'codex-quota-guard@codex-auto-switch-assistant' --json
if ($LASTEXITCODE -ne 0) { throw 'Plugin installation failed' }
```

已有安装按原市场的本地更新流程操作。使用安装命令返回的真实 `installedPath`，不要猜缓存版本目录。再次列出插件，确认只有一个来源；正常首次安装应启用插件。内置 Skill 随插件安装，新聊天加载；后台监测不需要每个聊天调用 Skill。Hook 信任由 Codex 规则处理，不替用户绕过。

## 5. 配置与后台

从已安装缓存目录运行配置器：

```python
import sys
from pathlib import Path
root = Path(INSTALLED_ROOT).resolve()  # 来自 CLI 安装结果
sys.path.insert(0, str(root/'scripts'))
from machine_config import detect, validate, save
config = detect()
config = validate(config)  # 检测失败时先补齐真实路径
if Path(config['plugin_root']).resolve() != root:
    raise RuntimeError('仍指向旧插件路径，先核对更新状态')
save(config)
```

更新已有安装时，如果 `installation.json` 保留了旧缓存路径，先确认没有进行中计划、CLI 返回的新路径含正确插件标识，并备份旧文件；仅将其中 `plugin_root` 更新为本次已确认的缓存目录，然后重新检测和校验。不得覆盖其他用户字段或复制旧设备路径。

也可以打开根目录 `setting.cmd` → 一键配置。配置保存应包含真实插件路径、正确市场键、Codex Home、CC Switch 数据目录与各运行时路径；保存后检查当前用户启动快捷方式。配置器保留已关闭的插件、管理和换号开关。

检查独立的 `CodexAutoSwitchAssistant-Service-<当前用户 SID>` 计划任务、后台 `--independent` 进程和心跳。后台应独立于 Codex 进程树运行；调度失败不能假装启动成功。不要将本机生成的设置或日志写回安装包。

## 6. 验证

```powershell
& $Python -X utf8 -m unittest discover -s (Join-Path $Bundle 'plugins/codex-quota-guard/tests') -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
& $Node --test (Join-Path $Bundle 'plugins/codex-quota-guard/tests/bridge.test.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Node tests failed' }
& $Python -X utf8 (Join-Path $Bundle 'tools/readiness.py') --installed-root $InstalledRoot
if ($LASTEXITCODE -ne 0) { throw 'Configuration check failed' }
```

- 使用已安装 `scripts/guard.py probe` 只读查询当前额度；仅报告百分比、时间和错误概况。
- 打开根目录设置入口，检查中英文界面、一键配置、账号列表、主界面和设置。新配置恢复阈值应为 10%，恢复条件是严格大于 10%，周剩余必须大于 0；已有设置保留用户值。动态监测分界仍为 20%/10%，不要误改。
- 首次安装 enabled=false 时后台 disabled 是预期。只有依赖、配置、只读额度检查通过，且确认设置为本次新建后，才将 enabled 改为 true；auto_resume 保持 true，auto_switch 保持 false，除非用户明确要求安装后自动换号。已有安装保留原开关。
- 开启监测后检查心跳、额度、错误、插件状态和桌面协议连接。等待连接不能写成协议检查通过。
- 核验启动快捷方式和目标；未实际重新登录 Windows 时，注明登录启动未实测。
- 常规验收不真实退出 Codex、切号或发送“继续”，也不临时提高暂停阈值来制造中断。真实换号验收需要用户明确要求并保存恢复现场。
- 未知协议或界面结构应停止相关操作，修复适配器再测；不强杀、盲按键或修改应用数据库。

## 7. 交付与失败处理

复制 `INSTALL_REPORT.template.md` 到用户工作目录，填写本机路径、版本、开关、阈值、测试结果和未验证项。报告根目录 `setting.cmd` 为日常入口；说明本包不会迁移旧设备聊天，也不保证全部任务无损恢复。

失败时保留诊断记录，仅撤回本次创建且能确认归属的启动项或安装变更。保留原配置、账号和聊天，不删除 Codex Home 或 CC Switch 数据目录。
