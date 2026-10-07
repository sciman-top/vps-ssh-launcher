"""Module entrypoint so ``python -m vps_ssh_launcher`` mirrors ssh_tool.py."""

from vps_ssh_launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
