# Raspberry Pi Project

This project manages a Raspberry Pi 5 running Raspberry Pi OS Lite (Trixie, arm64)
remotely over SSH from this laptop. opencode runs locally on the laptop and acts on
the Pi by running commands through SSH.

## Target device

- SSH alias: `pi` (configured in `~/.ssh/config`)
- Address: `192.168.0.168` (Ethernet) / Wi-Fi IP may differ
- User: `iptv`
- Hostname on device: `iptv1`
- Auth: key-based, passwordless (laptop's `~/.ssh/id_ed25519`)
- Sudo: passwordless (`/etc/sudoers.d/010_iptv-nopasswd`)

## How to run commands on the Pi

Always operate on the Pi by prefixing commands with `ssh pi`:

```bash
ssh pi 'hostname'
ssh pi 'sudo systemctl status ssh'
ssh pi 'cat /etc/os-release'
```

Sudo works non-interactively:

```bash
ssh pi 'sudo apt update'
ssh pi 'sudo systemctl restart <service>'
```

## Editing files on the Pi

Two options:

1. One-off edits via SSH (small changes):
   ```bash
   ssh pi 'sudo tee /etc/some/file > /dev/null <<EOF
   ...content...
   EOF'
   ```

2. Optional SSHFS mount for direct Read/Edit/Write with local tools:
   ```bash
   sudo apt install -y sshfs        # laptop, once
   mkdir -p ~/Documents/Projects/IPTV/mnt
   sshfs pi:/home/iptv ~/Documents/Projects/IPTV/mnt
   # unmount with: fusermount -u ~/Documents/Projects/IPTV/mnt
   ```

## Notes

- If the IP changes, update `HostName` in `~/.ssh/config` under `Host pi`.
- Verify connectivity with: `ssh pi 'echo ok'`
