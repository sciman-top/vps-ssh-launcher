A small candidate patch is attached for maintainer evaluation. It is
intentionally **not** presented as merge-ready: it demonstrates the missing
scheduler/catalog integration, but the `scheduler.pick` candidate ID and the
stored UID may differ in the unified plugin (`workbuddy-intl-…json` versus raw
UID). The canonical join needs your contract confirmation.

The candidate passed `gofmt` check, `go vet ./...`, `go test -count=1 ./...`,
and a Linux amd64 C-shared build on BWG. No upstream WorkBuddy request was made.

If the host contract allows resolving candidate ID to the stored UID, I can
send a corrected PR using one canonical key in both `scheduler.pick` and
`model.for_auth`.
