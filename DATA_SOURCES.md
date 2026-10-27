# 免费数据源与 CCU 核对

当前使用四个提供方，无需 VIP、登录或付费 API 密钥：

| 提供方 | 用途 | 限制 |
| --- | --- | --- |
| Rolimon’s `api.rolimons.com/games/v1/gamelist` | 每小时从收录游戏中选取 CCU Top 500 | 不是全站穷举，源端更新时间未知 |
| Roblox 官方 `games.roblox.com/v1/games?universeIds=...` | 对本轮 Top 500 候选逐款读取 `playing` 核对 | 按50个 universe ID分批，存在采集时差；候选来自 Rolimon’s，不是独立官方Top500 |
| Roblox 官方 Top Playing Now | 约100款榜单样本与后备核对 | 与官方详情属于同一提供方，不能算第三个独立平台 |
| [RoWatcher](https://rowatcher.com) `/api/games/batch?ids=...` | 每批25款，CCU、访问、收藏、赞踩、类型和来源时间 | 网站公开接口可能变化；同样以Roblox数据为上游，不代表独立测量 |
| [Romanum](https://romanum.dev/analytics/data) `/api/public/stats?universeIds=...` | 每批最多10款，CCU、访问、收藏、赞踩、类型及 fetchedAt | 官方API转取/缓存，不能视为独立计数；遵循公开目录参数限制 |

新增来源每小时随任务采集；全部覆盖并不保证，缺失保持为空。同轮快照之外不回填旧值，提供方时间与本轮Rolimon’s读取时间超过15分钟时保留数值并标注时间不匹配，不计算相对官方差异。`/api/compare` 中 `external_sources` 为覆盖摘要、每款游戏的 `external` 为各来源记录和时间。切换排行榜来源可查看该来源采集的历史；这是Top500候选内的排序，不是对应网站全站Top500。额外来源连续三批失败时停止本轮该来源请求。

RoWatcher历史接口已对单款游戏实测返回公开历史，测试响应保存于本地 `data/rowatcher-history.json`；尚未批量导入500款历史，不能将外站历史冒充本机采集点。其收入、DAU、留存估算不计入本次观察指标。

其他候选的核实结果（2026-10-27）：[Statroblox REST API](https://statroblox.com/api-docs) 要求Pro密钥，未接入；[RobloxGameStats API](https://robloxgamestats.com/docs/api) 单款实测可用，免费限个人/非商业用途，未默认启用；RoMonitor、RoTrends此前连接失败，仍未验证。没有绕过登录或付费限制。

官方详情同时保存公开的累计访问量、收藏数、服务器人数上限、一级类型与每批读取时间，位于 `entries.extra`，也可通过 `/api/compare` 的 `official_metadata` 字段查看。这些不是独家 VIP 数据，也不包括真实地区/设备玩家分布、收入、留存或全部历史。付费网站的历史图表可以从今后的定时采集中逐步积累替代，但不能凭当前值补造过去的数据。

看板核对只使用同一个采集轮次，以 Place ID 匹配。API `/api/compare` 返回全部候选；没有取得官方值时返回 null。差值=Rolimon’s−官方；差异百分比以官方为分母，官方为0时百分比为空。绝对差值至少100人且差异至少10%时标为“待核对”；官方为0时仅按绝对差值判断。这是筛查规则，不是准确性结论，不取平均数来制造所谓真实CCU。

游戏标识优先复用已有官方榜单、地区样本和本地缓存。首次映射需要额外请求；接口持续失败时停止本轮后续映射并明确报告缺失，下次继续尝试。请求遵循已有间隔及429重试规则。官方详情全失败时仍保留本轮Rolimon’s快照，不以旧时段官方值冒充本轮值。

2026-10-27 检查：官方详情免费接口实测可用。RoMonitor Stats 和 RoTrends 本次连接失败，未确认其免费接口与付费字段，未接入，也不据此断言它们必须付费。指定某个VIP功能后，需逐项核实是否存在等价公开来源。
