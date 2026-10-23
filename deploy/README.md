# 编程赛道独立部署准备

这份目录是待执行的部署材料，不是部署成功证明。用户已允许申请、开源和 Smoke；Full 仍被禁止。现有文本进程、源码、环境文件和数据目录不得修改。

目标主机是已有 Ubuntu 24.04 免费实例。先用 `sudo python3 deploy/inspect_host.py` 读取运行状态。需要 Python 3.12、SQLite 3.43+ 和 FTS5/contentless_delete。预检不读取环境密钥；它记录文本健康状态、现有配置摘要和剩余资源。内存或磁盘不足时先调整资源方案，不启动新服务影响文本进程。700 MiB 可用内存只是 Smoke 的初始余量门槛，不是 Full 容量保证。

## 目录、依赖与进程

固定源码放在 `/opt/heguang-coding/app`，独立虚拟环境 `/opt/heguang-coding/venv`，独立模型 `/opt/heguang-coding/model`，数据 `/var/lib/heguang-coding`。源码和模型由 root 持有且服务可读；数据目录由现有 `admin` 用户持有、权限 0700。依赖安装在新的虚拟环境中，不能复用或升级文本服务的依赖。

```sh
sudo install -d -m 755 /opt/heguang-coding
sudo install -d -o admin -g admin -m 700 /var/lib/heguang-coding
sudo python3 -m venv /opt/heguang-coding/venv
sudo /opt/heguang-coding/venv/bin/python -m pip install -r /opt/heguang-coding/app/requirements-rerank.lock
sudo /opt/heguang-coding/venv/bin/python /opt/heguang-coding/app/tools/acquire_reranker.py --model-dir /opt/heguang-coding/model
sudo python3 /opt/heguang-coding/app/deploy/create_environment.py
sudo install -m 644 /opt/heguang-coding/app/deploy/heguang-coding.service /etc/systemd/system/heguang-coding.service
sudo systemd-analyze verify /etc/systemd/system/heguang-coding.service
sudo systemctl daemon-reload
sudo systemctl enable --now heguang-coding.service
curl --fail http://127.0.0.1:8752/health
```

创建环境文件使用独占写入；若文件存在则停止，不覆盖密钥。服务仅监听回环地址 8752，单进程、单处理名额，模型不联网。650 MiB 是服务内存上限，超限会失败而不是证明请求已成功；必须检查健康状态、内存事件和系统余量。

## 接入现有 HTTPS

先备份 `/etc/caddy/Caddyfile` 并记录摘要。在原域名站点中、最终兜底 `handle` 之前插入 `caddy-coding.fragment`。保留文本的 `/health`、`/add`、`/search` 处理段原样；使用现有域名和证书，不新增公网端口。先运行 `caddy validate --config <候选配置路径>`，再比较原文本处理段和候选配置，确认后通过 Caddy reload 生效。

待验证接口是 `https://heguang-8-208-30-48.sslip.io/coding/health`、`/coding/add`、`/coding/search`。它们目前只是部署目标。外部默认 CA 验证必须通过；不得忽略证书错误。health 应为 200，缺少鉴权的 Add/Search 应为 401；合成的带鉴权预检需核对写入回显、立即搜索、重复与冲突请求、用户隔离、重启后持久化。再检查原文本接口与 systemd 单元摘要没有变化。

专用调用密钥只保存在服务器 0600 文件，提交赛事的该系统鉴权字段时使用。不要把环境文件、密钥、数据库、请求日志或赛事评测 Key 上传 GitHub。

## 保留期、回退与容量

应用启动及运行中会清理 29 天前的数据；在云端接入前还需安装本目录独立 Coding 清理 service/timer，并验证停掉 API 时 timer 仍能清理。`purge_coding.py` 只使用固定 Coding 目录，不读取接口密钥。不能复用文本清理脚本或让两套服务共用数据目录。正式数据不加入备份或学习包。

```sh
sudo install -m 644 /opt/heguang-coding/app/deploy/heguang-coding-retention.service /etc/systemd/system/heguang-coding-retention.service
sudo install -m 644 /opt/heguang-coding/app/deploy/heguang-coding-retention.timer /etc/systemd/system/heguang-coding-retention.timer
sudo systemd-analyze verify /etc/systemd/system/heguang-coding-retention.service /etc/systemd/system/heguang-coding-retention.timer
sudo systemctl daemon-reload
sudo systemctl enable --now heguang-coding-retention.timer
sudo systemctl start heguang-coding-retention.service
sudo systemctl show heguang-coding-retention.service --property=Result --property=ExecMainStatus
```

回退时仅停止新 `heguang-coding` 服务并恢复本次备份的 Caddy 配置，通过验证后 reload；保留错误和配置摘要供复查。不要停止文本进程。当前未验证 1.8B token 全量负载，30GB 磁盘和 1GiB 内存不能被默认认定足够。申请和 Smoke 不等于允许 Full。
