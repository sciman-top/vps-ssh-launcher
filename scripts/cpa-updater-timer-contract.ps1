#requires -Version 7
#requires -Version 7
function Get-CpaUpdaterTimerContract {
  # Used by the remote projector and the read-only schedule checker. systemd
  # begins a service on the first calendar event and the randomized delay is
  # added afterwards; it is not part of the following maintenance slot.
  [pscustomobject]@{
    OnCalendar = "*-*-* 04:00:00"
    RandomizedDelaySec = "300"
    RandomizedDelayUSec = "300000000"
    RandomizedDelaySeconds = "300"
    RandomizedDelayShort = "5min"
  }
}
