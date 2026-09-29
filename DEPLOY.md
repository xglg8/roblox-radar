# 云服务器部署

状态：部署文件已准备；尚未连接服务器、上传或生成公网网址。当前机器没有 Docker，因此容器构建尚未实测。

需要一台可通过 SSH 管理、已安装 Docker Engine 和 Docker Compose 的 Linux 服务器。公网入站允许 TCP 80/443；不开放 8765。已有服务占用 80/443 时应复用服务器现有反向代理，避免端口冲突。

将本目录（不含 `.git`、`__pycache__`、`data`）上传到服务器专用目录，例如 `/opt/roblox-radar`。在该目录执行：

```sh
cp .env.example .env
# 编辑 .env：有域名时填写已解析到服务器的域名，例如 radar.example.com。
# 无域名时填写 http://服务器公网IP；这是 HTTP，不是 HTTPS。
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 collector
```

域名模式由 Caddy 自动申请并续期 HTTPS 证书。地址由实际服务器或域名决定，不会自动分配一个不存在的域名。网页及 API 无需登录，获得网址的人均可阅读榜单、历史和采集状态。

`web` 提供只读看板；`collector` 首次启动立即采集，以后按配置时区每天 10:00 采集，默认 UTC+8。任务失败或部分失败时每 15 分钟重试，最多共 4 次；重启后错过的任务会补跑一次。云端调度状态、数据库和日志存于 `radar-data` 持久卷，更新镜像不丢失。不执行 `docker compose down -v`，该选项会删除历史数据卷。

部署后检查：

```sh
curl -fsS https://实际域名/api/status
curl -fsS https://实际域名/api/rankings
```

首次采集完成前榜单为空，不自动上传旧电脑上的日志或数据库。如要迁移已有历史，应先用 SQLite backup API 导出一致性备份，停止云端采集容器后导入持久卷，并校正文件所有者为 UID/GID 10001。

确认云端每日调度正常后，可以在原 Windows 电脑停用本地任务，避免重复采集：

```powershell
Disable-ScheduledTask -TaskName RobloxRadar-Daily
```

公网服务独立于原电脑运行；目前本地任务仍保持启用，直到服务器部署完成。
