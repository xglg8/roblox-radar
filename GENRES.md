# 最近流行游戏的类型构成趋势

数据范围是五地区29个国家的 Roblox `Top Trending` 榜单样本。总览对同一 Place ID 去重，每款游戏只归入一个主类型；各地区也分别去重。这不是全 Roblox 类型市场份额，也不是类型 CCU 占比。

## 分类规则

通过官方 `games.roblox.com/v1/games?universeIds=...` 批量读取 `genre`、`genre_l1`、`genre_l2`，随当期榜单保存分类快照，不按今天的标签重新改写历史。

| 展示类型 | 官方依据 |
| --- | --- |
| 模拟器/模拟经营 | 一级 Simulation |
| FPS | 官方旧类型 FPS，或子类型明确 First Person / First-Person |
| 其他射击/视角未明确 | 一级 Shooter，且没有明确 FPS 标签 |
| 角色扮演/RPG | 一级 RPG |
| 动作/格斗 | 一级 Action |
| 恐怖 | 官方 Horror 标签或包含 Horror 的子类型，优先于普通生存 |
| 生存 | 一级 Survival，排除已归类恐怖的游戏 |
| 跑酷/平台跳跃 | 一级 Obby & Platformer |
| 策略/塔防 | 一级 Strategy |
| 社交/生活扮演 | 一级 Roleplay & Avatar Sim 或 Social |
| 体育/竞速 | 一级 Sports & Racing |
| 解谜/探索 | 一级 Puzzle 或 Adventure |
| 休闲/派对 | 一级 Party & Casual 或 Entertainment |
| 其他 / 未分类 | 官方其他类型 / 没有取得官方一级类型 |

不通过游戏名字中的 Simulator、FPS 等词强行推断。官方标签可能宽泛或有延迟，不能视为经过人工验证的玩法标签；“其他射击”不等于明确的第三人称射击。

## 指标

- **游戏数**：当期样本内该类型的去重游戏数。
- **占比**：该类型游戏数 ÷ 全部样本游戏数，未分类也包含在分母中。
- **1天／7天／15天变化**：对比精确昨日／7天前／15天前同国家样本与同分类规则的快照，展示游戏数变化和占比变化（百分点）。半个月固定按15天计算，是两个日期的快照对比，不是期间均值。例如从20%到25%为+5个百分点，不是+5%。API对应字段为 `daily`、`weekly`、`half_monthly`。
- **地区热度积分占比**：API额外提供该类型地区榜积分之和占比；总览每款等权。不是玩家数占比。
- **样例**：每个类型列出最多3款游戏；总览样例按游戏ID排序，地区样例按地区指数排序，不声称是全站最热门。

占比变化可能来自榜单游戏换入换出、样本总量变化或官方标签修改，不直接代表该类型玩家增长。分类快照从新增功能之日开始保存，历史不足时显示暂无基准。

## 使用

每日北京时间10:00的原任务会在地区采集后建立类型快照，并在飞书日报末尾追加类型构成报告。`config.json` 中 `genre_trends_enabled` 可关闭此功能；需要先启用地区采集。

```powershell
python genre_trends.py --day YYYY-MM-DD
python feishu.py --day YYYY-MM-DD --edition genres --send
```

单独报告独立防重，不覆盖已发送日报。初次升级的旧地区快照没有 Universe ID 时，先重新执行 `python regional.py` 采集；不能凭旧名字恢复分类。

看板类型面板可切换五地区和五地区去重总览。完整数据接口：`/api/genres?day=YYYY-MM-DD`，不传日期取最新类型快照。独立命令导出 `data/genres-YYYY-MM-DD.json`。

任一地区没有当日完整样本时不生成总览；官方类型信息全部无法获取时不发布分类快照，部分缺失时显示未分类。每条分类存储官方原始类型字段，方便核验。
