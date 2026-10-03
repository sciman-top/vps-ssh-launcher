$out = "D:\CODE\vps-ssh-launcher\outputs\sidecar-procs.txt"
Get-Process -Id 10016,12456 -ErrorAction SilentlyContinue |
  Select-Object Id,ProcessName,StartTime |
  Format-Table -AutoSize | Out-File -Encoding utf8 $out
"---done---" | Out-File -Append -Encoding utf8 $out
