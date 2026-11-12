#!/usr/bin/env bash
# HG-03 v4.1 read-only snapshot. No sudo, installs, restarts or deletion.
# Uses linux-local service names only for status lookup; never guesses the
# profile-to-host mapping. Run via the existing launcher after host verification.
set -u
section(){ printf '\n===== %s =====\n' "$1"; }
run(){ "$@" 2>&1 || printf '[unavailable/failed: %s]\n' "$1"; }
os_release(){ if [ -r /etc/os-release ]; then cat /etc/os-release; else echo unavailable; fi; }
boot_id(){ if [ -r /proc/sys/kernel/random/boot_id ]; then cat /proc/sys/kernel/random/boot_id; else echo unavailable; fi; }
pid_max(){ if [ -r /proc/sys/kernel/pid_max ]; then cat /proc/sys/kernel/pid_max; else echo unavailable; fi; }
container_cgroup(){ if [ -r "/proc/$pid/cgroup" ]; then cat "/proc/$pid/cgroup"; else echo unavailable; fi; }
section timestamp; run date -Is; run uname -srmo; run nproc; run uptime
section identity; run hostname; run os_release
section kernel; run boot_id; run pid_max
section memory; run free -h; run swapon --show; run vmstat 1 5
section pressure; for f in cpu memory io; do printf '%s\n' "$f"; if [ -r "/proc/pressure/$f" ]; then cat "/proc/pressure/$f"; else echo unavailable; fi; done
section filesystems; run df -hT; run df -ih
if command -v systemctl >/dev/null 2>&1; then run systemctl show --property=MemoryCurrent --property=MemoryHigh --property=MemoryMax --property=MemorySwapMax --value; else echo 'systemctl unavailable'; fi
section processes; run ps -eo pid,ppid,comm,stat,pcpu,rss,etime --sort=-rss
section resources; for unit in /sys/fs/cgroup/.memory.peak /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.stat /sys/fs/cgroup/cpu.stat; do if [ -r "$unit" ]; then echo "$unit"; cat "$unit"; fi; done
section docker
if command -v docker >/dev/null 2>&1; then
 run docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}'
 run docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}} {{.MemPerc}} {{.NetIO}} {{.BlockIO}}'
 run docker system df -v
 run docker info --format '{{.DockerRootDir}}'
 run docker compose ls
 if ids=$(docker ps -aq 2>/dev/null); then
  for id in $ids; do
   run docker inspect --format '{{.Name}} status={{.State.Status}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} memLimit={{.HostConfig.Memory}} nanoCPU={{.HostConfig.NanoCpus}} logDriver={{.HostConfig.LogConfig.Type}} pid={{.State.Pid}}' "$id"
   pid=$(docker inspect --format '{{.State.Pid}}' "$id" 2>/dev/null || true)
   if [ -n "$pid" ] && [ "$pid" != 0 ] && [ -r "/proc/$pid/cgroup" ]; then
    container_cgroup
    if [ -r "/sys/fs/cgroup/system.slice/docker-$id.scope/memory.events" ]; then
     echo "container_memory_events=$id"; cat "/sys/fs/cgroup/system.slice/docker-$id.scope/memory.events"
    fi
   fi
  done
 fi
else echo 'docker not installed/available'; fi
section units; for unit in napcat astrbot caddy qq-codex-bot-cleanup.timer qq-codex-bot-provider-watchdog.timer qq-codex-bot-login-watchdog.timer; do printf '%s=' "$unit"; systemctl show "$unit" -p LoadState -p ActiveState -p SubState -p ExecMainStatus 2>/dev/null || echo unavailable; done
section ports; run ss -lntu
section timers; run systemctl list-timers --all --no-pager
section oom
if command -v journalctl >/dev/null 2>&1; then
 if log=$(journalctl -k --since '7 days ago' --no-pager -q 2>&1); then
  printf 'Visible matching kernel log lines: '; printf '%s\n' "$log" | grep -Eic 'oom|out of memory|killed process' || true
  printf 'Visible kernel log lines in query: '; printf '%s\n' "$log" | wc -l
  echo 'Coverage depends on journal permissions and retention.'
 else echo 'Kernel journal unavailable to current user.'; fi
else echo 'journalctl unavailable'; fi
section note
printf '%s\n' 'Snapshot only; not proof of no OOM, natural peak, or a safe cleanup target. Collect cloud monitoring and peak workload separately. Review output for sensitive paths/names before sharing.'
