# Single source of truth for the per-profile Google/Gemini/Anthropic IPv4
# pin lists (bare domain names, no "domain:" prefix). Both proxy cores must
# stay in lockstep: scripts/google_ipv4_routing.ps1 asserts the xray lane's
# routing/outbound contract against these entries, and
# scripts/vasma_kernel_update_cron.ps1 projects the sing-box lane's
# 99_vps_ssh_launcher_google_ipv4.json fragment from them. bwg mirrors its
# xray lane's 14-domain scope (anthropic.com/claude.ai since 2026-10-19,
# claude.com/platform.claude.com aligned 2026-10-31); zz stays at the
# 10-domain design (v4-only host, the extra domains would be no-ops).
$GoogleIpv4DomainsByProfile = @{
  "bwg" = @(
    "gemini.google.com", "google.com", "googleapis.com", "googleapis.cn",
    "gstatic.com", "googleusercontent.com", "googlevideo.com", "ggpht.com",
    "youtube.com", "ytimg.com", "anthropic.com", "claude.ai",
    "claude.com", "platform.claude.com"
  )
  "zz" = @(
    "gemini.google.com", "google.com", "googleapis.com", "googleapis.cn",
    "gstatic.com", "googleusercontent.com", "googlevideo.com", "ggpht.com",
    "youtube.com", "ytimg.com"
  )
}
