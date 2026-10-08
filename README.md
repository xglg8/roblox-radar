# Roblox Radar

现已支持北美、南美、欧洲、东南亚、日韩的固定国家样本增长热度趋势，详见 [地区统计口径](REGIONS.md)。地区指数来自国家 Top Trending 榜单，不是地区实际 CCU。

当前模式：**只采集 Roblox**，Steam 已在配置中关闭。每天北京时间 10:00 执行 `python radar.py daily`，采集后自动推送到已配置的飞书群；未配置 Webhook 时仅保存在本机。接入步骤见 [FEISHU.md](FEISHU.md)。不部署公网服务，原有 Steam 历史保留。

每日 Roblox CCU Top 500、日／周排名变化、Roblox 官方／第三方同游戏数据核对，以及飞书群日报。Python 3.11+，仅使用标准库，无需 pip 安装。代码保留 Steam 对比适配器，但当前配置关闭 Steam 采集，飞书日报仅包含 Roblox。

仓库只包含源码和配置示例，不包含飞书 Webhook、签名密钥、本机数据库、日志或采集结果。克隆到新电脑后需要重新配置机器人并安装计划任务；上传 GitHub 本身不会启动云端每日采集。

## 快速使用

在本目录打开终端：

```powershell
python radar.py collect       # 立即采集 Roblox，保存本地快照
python radar.py daily         # 采集并发送到已配置的飞书群
python radar.py serve         # 本地看板 http://127.0.0.1:8765
python radar.py export        # 导出 data/rankings.csv，UTF-8 BOM
python radar.py status        # 查看最近任务及错误
python -m unittest -v         # 核心测试
```

也可双击 `start-dashboard.cmd`。若看板已经运行，直接打开 http://127.0.0.1:8765 。看板刷新只读取数据库；新采集使用 `collect` 或计划任务。

## 每日采集

Windows 任务安装：

```powershell
.\install-task.ps1 -At '10:00'
Get-ScheduledTaskInfo -TaskName RobloxRadar-Daily
```

安装脚本默认每天 **本机时间 10:00**，当前电脑时区为中国标准时间（UTC+8）。任务名 `RobloxRadar-Daily`；要求电脑开机、用户已登录和网络可用。启用错过后补跑、失败每 15 分钟重试（最多 3 次），同一任务不重叠，单次上限 45 分钟。不保存用户密码。安装脚本不会覆盖已有同名任务。日志写入 `data/radar.log`。

修改已安装任务时间：

```powershell
Set-ScheduledTask -TaskName RobloxRadar-Daily -Trigger (New-ScheduledTaskTrigger -Daily -At '10:00')
```

停止自动采集：`Disable-ScheduledTask -TaskName RobloxRadar-Daily`；恢复：`Enable-ScheduledTask -TaskName RobloxRadar-Daily`。

Linux 服务器可直接使用 Python 和 cron，例如 `0 2 * * * /usr/bin/python3 /absolute/path/RobloxRadar/radar.py daily`（服务器时区为 UTC 时对应北京时间 10:00）。持续运行不依赖看板进程。Windows 电脑离线期间不能采集过去的 CCU。

## 数据源与覆盖范围

| 数据集 | 获取方式 | 范围与限制 |
| --- | --- | --- |
| Roblox 主榜 | `api.rolimons.com/games/v1/gamelist` | 从 Rolimon’s 收录游戏按当前返回 CCU 排序取前 500；首轮约 7,500 个候选。不能保证包含 Roblox 全部游戏；源端更新时间未知。 |
| Roblox 官方核对 | `apis.roblox.com/explore-api/v1/get-sort-content?sortId=top-playing-now&sessionId=UUID` | 实测约 100 款，没有可用的分页游标，不能独自提供 Top 500。按 root Place ID 与主榜匹配。 |
| Steam 对比 | `ISteamChartsService/GetMostPlayedGames/v1/` + `ISteamUserStats/GetNumberOfCurrentPlayers/v1/` | 官方 Most Played 返回约 100 个候选，再逐个取当前在线人数排序。不是 Steam 全站 CCU Top 500。 |
| Steam 名称 | `store.steampowered.com/api/appdetails` | 名称缓存在本地；名称查询失败时保留 App ID，不影响 CCU。 |

CCU 是数据源返回的当前在线人数，不是 DAU、日均在线或每日峰值。Steam 的 `peak_in_game` 仅保存在原始附加字段，绝不替代 CCU。多款游戏的读取存在时间窗口，不能视为同一秒的快照。各来源的本机采集起止时间独立记录，不能代表上游数据的新鲜度。

看板提供同日 Top 10 和 Top 100 样本 CCU 对比，**不得将样本 CCU 总和解释为平台全站总量或市场份额**。跨平台同名游戏不自动认定为同一作品。Roblox 官方与 Rolimon’s 的差值可能由缓存和时差造成。只读取公开接口，不依赖登录 Cookie。

## 排名与历史规则

- 日历日默认 UTC+8，可在 `config.json` 调整固定时区偏移；上线后不建议修改，否则历史日期口径会改变。
- 日变化 = 昨日排名 − 当前排名；周变化 = 7 天前排名 − 当前排名。正数为上升。
- 日／周指固定时刻快照之间的比较，不是日均榜或周均榜。
- 必须存在精确目标日期、同平台、同数据源的快照；缺失显示“暂无基准”，不使用更早的日期冒充昨日。
- 昨日或上周不在已保存样本中的游戏显示“前期未入样本”，不伪造历史排名，也不声称游戏刚上线。
- 同 CCU 按数值 ID 升序打破平局，因此即使 CCU 相同也有唯一顺序。
- 同日多次采集全部保留，页面和基准使用当日最新成功快照。失败批次不替换已有数据。
- 主榜每次保存前 500；低于 500 的返回或非法／重复记录导致该来源失败。其他来源仍继续采集。
- 各来源原子提交；任务状态为 `success`、`partial`、`failed`，后两种退出码为 1，触发计划任务重试。进程被强制结束时可留下 `running` 记录，需结合日志判断。
- Steam 单款 CCU 查询失败会排除该款、记录缺失 ID，并把任务标记为 `partial`。覆盖不足候选数的 80% 时整批不发布；样本不完整时排名变化需结合缺失信息解释。
- 首次运行没有历史；持续采集一天／七天后才能分别计算日／周变化。历史曲线仅包括该游戏进入样本的采集点。

## 文件与 API

`radar.py`：HTTP 重试／限速、采集适配器、SQLite 存储、排名服务、CSV、只读本地 HTTP 服务。

`dashboard.html`：中文看板、平台切换、历史日期、名称／ID 搜索、CCU 趋势、同来源排名变化、跨平台对比和来源核对。

`config.json`：Top N、数据库路径、固定时区偏移、Steam 开关、请求超时／重试／间隔。

`data/radar.sqlite3`：快照、排名、任务状态、Steam 名称缓存。SQLite WAL 模式支持采集时读看板。要备份运行中的数据库，请使用 SQLite backup API，不要只复制主文件而遗漏 WAL。

HTTP API 默认仅监听 `127.0.0.1`，不用于直接暴露公网：

```text
GET /api/rankings?platform=roblox&source=rolimons&day=2026-09-29
GET /api/rankings?platform=steam&source=official
GET /api/compare?day=2026-09-29
GET /api/history?platform=roblox&source=rolimons&game_id=2753915549
GET /api/export.csv?platform=roblox&source=rolimons
GET /api/status
```

省略 `day` 时榜单使用该来源最新快照；对比页使用 Roblox 主榜最新日期，其他平台当天缺失时不会混入其他日期。历史 API 返回所有匹配游戏的采集点。

导出指定日期：

```powershell
python radar.py export --day 2026-09-29 --out data/roblox-2026-09-29.csv
python radar.py export --platform steam --source official --out data/steam.csv
```

请求限速是进程内全局限速，Steam 最多 3 个并发；网络错误、429 和常见 5xx 有有限次数退避重试。进程文件锁防止手动运行与计划任务重叠。第三方接口结构变化时保留此前快照并记录失败，不填充假数据。日志和历史库不会自动清理，长期运行请定期备份与归档。
