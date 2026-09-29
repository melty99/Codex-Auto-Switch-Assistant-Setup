# Capability-checked adapter for the Chinese Codex provider page.
# Input contains provider names and IDs only; never credentials.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $request = $env:QUOTA_GUARD_CC_REQUEST | ConvertFrom-Json
    $processes = @(Get-Process cc-switch -ErrorAction SilentlyContinue)
    # Re-launching a singleton activates its existing window, even with Hidden.
    # Never launch/show CC Switch as a side effect of a background query.
    if ($processes.Count -ne 1) { throw '需有且仅有一个 CC Switch 实例，并打开 Codex 供应商页面。' }
    $process = $processes[0]
    $version = (Get-Item -LiteralPath $process.Path).VersionInfo.ProductVersion
    if ($version -notmatch '^\d+\.\d+\.\d+') { throw '无法识别 CC Switch 版本。' }
    if (-not $request.autoAdapt -and $version -notmatch '^3\.20\.1(?:\D|$)') { throw '自动适配已关闭；当前版本不在已验证基线内。' }
    $roots = @(Get-ProcessWindowRoots $process.Id 'Tauri Window' -IncludeHidden)
    if ($roots.Count -eq 0) {
        throw 'CC Switch 窗口不可读，请手动打开 Codex 供应商页面；后台不会唤起窗口。'
    }
    if ($roots.Count -ne 1) { throw '无法唯一识别 CC Switch 主窗口；请关闭多余窗口后重试。' }
    $root = Get-WebViewRoot $roots[0]
    $all = [System.Windows.Automation.Condition]::TrueCondition
    $scope = [System.Windows.Automation.TreeScope]::Descendants
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    function Named($element, $name) {
        $condition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, $name)
        return ,@($element.FindAll($scope, $condition))
    }
    function Button($element, $name) {
        $found = @(Named $element $name | ForEach-Object { $_ } | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button })
        if ($found.Count -ne 1) { throw ('无法唯一识别按钮：' + $name) }
        return $found[0]
    }
    function Card($name) {
        $titles = @(Named $root $name | ForEach-Object { $_ } | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text })
        if ($titles.Count -ne 1) { throw ('无法唯一识别账号卡片：' + $name + '；请打开 Codex 供应商页面。') }
        $node = $titles[0]
        for ($i=0; $i -lt 8; $i++) {
            $node = $walker.GetParent($node)
            if (-not $node) { break }
            if ($node.Current.ClassName -match '^relative overflow-hidden rounded-xl border ') { return $node }
        }
        throw '账号卡片结构与已验证版本不一致，已停止。'
    }
    function Snapshot($name, [switch]$AllowUnavailableQuota) {
        $card = Card $name
        $nodes = @($card.FindAll($scope,$all))
        $texts = @($nodes | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text } | ForEach-Object { $_.Current.Name })
        $buttons = @($nodes | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button } | ForEach-Object { $_.Current.Name })
        $active = $buttons -contains '使用中'
        $canSwitch = $buttons -contains '启用'
        if ($active -eq $canSwitch) { throw '账号激活状态结构不明确，待适配。' }
        $null = Button $card $(if ($active) { '使用中' } else { '启用' })
        $quotaReady=$true
        foreach ($label in @('5小时','7天')) {
            if (@($texts | Where-Object { $_.TrimEnd(':','：').Trim() -eq $label }).Count -ne 1) {
                $quotaReady=$false
            }
        }
        $queryError=@($texts | Where-Object { $_ -match '查询错误|查询失败|获取失败|Failed to (?:fetch|query)|Query error' }).Count -gt 0
        if (-not $AllowUnavailableQuota -and (-not $quotaReady -or $queryError)) { throw '额度窗口标签已变化或查询失败，请刷新 CC Switch。' }
        $refresh = Button $card '刷新'
        $null = $refresh.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        return @{texts=$texts; active=$active; canSwitch=$canSwitch; refreshing=(-not $refresh.Current.IsEnabled); quotaReady=$quotaReady; queryError=$queryError; version=$version; adapter='semantic-ui-v1'}
    }
    function InvokeButton($button) {
        if (-not $button.Current.IsEnabled) { throw '按钮暂不可用；未发送点击。' }
        $pattern = $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pattern.Invoke()
    }
    function RefreshQuota($name) {
        # An error card can have no quota labels. Validate its identity and Refresh
        # control, then requery; require both quota windows only after success.
        $before = Snapshot $name -AllowUnavailableQuota
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ($before.refreshing -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 200
            $before = Snapshot $name -AllowUnavailableQuota
        }
        if ($before.refreshing) { throw 'CC Switch 查询仍在进行，请稍后刷新。' }
        InvokeQuietRefresh $name
        Start-Sleep -Milliseconds 250
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        do {
            $result = Snapshot $name -AllowUnavailableQuota
            if (-not $result.refreshing) {
                if ($result.queryError) { throw 'CC Switch 仍显示查询错误，请等待 VPN 连接稳定后重试。' }
                if ($result.quotaReady -and ($result.texts -contains '刚刚')) { return (Snapshot $name) }
            }
            Start-Sleep -Milliseconds 200
        } while ([DateTime]::UtcNow -lt $deadline)
        throw '额度刷新超时；没有切换账号。'
    }
    function InvokeQuietRefresh($name) {
        $button = Button (Card $name) '刷新'
        if (-not $button.Current.IsEnabled) { throw '刷新按钮暂不可用；未发送点击。' }
        Add-Type -TypeDefinition ([IO.File]::ReadAllText($env:QUOTA_GUARD_REFRESH_SOURCE,[Text.Encoding]::UTF8)) -ReferencedAssemblies Accessibility
        try {
            [QuotaGuardQuietRefresh]::Run([IntPtr]$root.Current.NativeWindowHandle, $name)
        } catch {
            throw 'CC Switch 后台刷新控件不可用；请手动检查页面。为避免抢焦点，不回退到前台点击。'
        }
    }
    # A profile switch could also change MCP/skills; only operate the default page.
    $null = Button $root 'Codex'
    if (@(Named $root '不使用项目' | ForEach-Object { $_ }).Count -ne 1) { throw '请先在 CC Switch 选择“不使用项目”，避免同时切换项目配置。' }
    switch ($request.action) {
        'probe' {
            $result = Snapshot $request.name
            if (-not $result.active) { throw '当前界面账号与 CC Switch 配置不一致。' }
            foreach ($name in $request.names) { $null = Snapshot $name }
        }
        'inspect' {
            $result = Snapshot $request.name -AllowUnavailableQuota
        }
        'refresh' {
            $result = RefreshQuota $request.name
        }
        'switch' {
            if (-not (Snapshot $request.currentName).active) { throw '当前账号已被其他操作改变；请重新点击手动切换。' }
            $result = Snapshot $request.name
            if ($result.active -or -not $result.canSwitch -or $result.refreshing) { throw '目标账号状态发生变化；没有发送切换。' }
            if (($result.texts -join "`n") -cne ($request.expectedTexts -join "`n")) { throw '目标账号额度显示发生变化；请重新点击手动切换。' }
            InvokeButton (Button (Card $request.name) '启用')
            $deadline = [DateTime]::UtcNow.AddSeconds(20)
            do {
                Start-Sleep -Milliseconds 100
                $result = Snapshot $request.name
                if ($result.active) { break }
            } while ([DateTime]::UtcNow -lt $deadline)
            if (-not $result.active) { throw '已请求切换，但未确认结果。请检查 CC Switch；不会自动重复点击。' }
        }
        default { throw '未知操作。' }
    }
    @{ok=$true; result=$result} | ConvertTo-Json -Depth 8 -Compress
} catch {
    @{ok=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
