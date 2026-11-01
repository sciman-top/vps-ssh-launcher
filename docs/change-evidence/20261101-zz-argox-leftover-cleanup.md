# 2026-11-01 zz ArgoX 遗留清理

## 背景

代理车道审查时发现 zz 上仍有 2026-04 安装的 ArgoX 残留：

- `argo.service` 是 `cloudflared tunnel --url http://localhost:8080` 的临时
  trycloudflare 隧道，但 `8080` 已无监听，日志每天记录
  `connection refused`/`502`，公网地址不可用。
- `/etc/argox/custom` 与 `/etc/argox/cert/cert.pem` 权限为 `644`，前者含
  `privateKey` 字段。
- `30000-30010/tcp`、`30000-30010/udp`、`8080/tcp` 仍有 accept 规则，但没有
  监听进程；INPUT 策略本身是 `ACCEPT`，这些规则不改变可达性。

## 本次动作（仅 zz）

```bash
systemctl disable --now argo
chmod 600 /etc/argox/custom /etc/argox/cert/cert.pem
```

未删除任何文件，未改防火墙规则，未触碰 xray/sing-box/nginx。

## 复验

- `systemctl is-enabled argo` -> `disabled`
- `systemctl is-active argo` -> `inactive`
- `stat -c '%a %n' /etc/argox/custom /etc/argox/cert/cert.pem` -> `600`
- `ss -Hltnup | grep -E ':3000[0-9]|:8080'` -> 无输出

## 回滚

```bash
chmod 644 /etc/argox/custom /etc/argox/cert/cert.pem
systemctl enable --now argo
```

## 后续

如果需要稳定的 Cloudflare 隧道兜底，应使用命名隧道（需要 Cloudflare 账号级
cert/token），而不是 trycloudflare 临时地址；当前 2087 回源车道与合并订阅不受
本次清理影响。
