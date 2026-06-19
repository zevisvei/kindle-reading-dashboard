#!/usr/bin/env python3
"""Run a command on the Kindle over SSH (busybox sshd, no sftp) and print output.

Connection details are read from environment variables so no credentials are
committed. Defaults match a jailbroken Kindle with USB networking (usbnet):
    KINDLE_HOST    (default 192.168.15.244)   # usbnet device IP
    KINDLE_USER    (default root)
    KINDLE_PW      (default kindle)            # the well-known default jailbreak password
    KINDLE_SSH_KEY (default unset)             # private-key path for key-only login
"""
import os, sys, paramiko

HOST = os.environ.get("KINDLE_HOST", "192.168.15.244")
USER = os.environ.get("KINDLE_USER", "root")
PW = os.environ.get("KINDLE_PW", "kindle")
# Private-key path. If set (or relying on ssh-agent / ~/.ssh keys), the device
# can be configured for key-only login with password auth disabled.
KEY = os.environ.get("KINDLE_SSH_KEY")


def connect_kwargs():
    """Auth kwargs for paramiko.connect.

    Supports both password and public-key login. Key-only Kindles (pubkey in
    authorized_keys, password auth disabled) work via KINDLE_SSH_KEY, ssh-agent,
    or a default ~/.ssh key — none of which the old password-only call allowed.
    """
    kw = {"username": USER, "timeout": 15,
          "look_for_keys": True, "allow_agent": True}
    if KEY:
        kw["key_filename"] = os.path.expanduser(KEY)
    if PW:
        kw["password"] = PW  # tried after keys; harmless if password auth off
    return kw


def _client():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, **connect_kwargs())
    return c


def run(cmd):
    c = _client()
    stdin, stdout, stderr = c.exec_command(cmd, timeout=60)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    c.close()
    return out, err

def put(local, remote):
    # busybox sshd has no sftp subsystem -> stream bytes through `cat > remote`
    with open(local, "rb") as f:
        data = f.read()
    c = _client()
    stdin, stdout, stderr = c.exec_command(f"cat > '{remote}'")
    stdin.channel.sendall(data)
    stdin.channel.shutdown_write()
    stdout.channel.recv_exit_status()
    err = stderr.read().decode("utf-8", "replace")
    c.close()
    print(f"put {local} -> {remote} ({len(data)} bytes){' ERR:'+err if err.strip() else ''}")

def get(remote, local):
    c = _client()
    stdin, stdout, stderr = c.exec_command(f"cat '{remote}'")
    data = stdout.channel.makefile("rb").read()
    c.close()
    with open(local, "wb") as f:
        f.write(data)
    print(f"get {remote} -> {local} ({len(data)} bytes)")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "put":
        put(sys.argv[2], sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == "get":
        get(sys.argv[2], sys.argv[3])
    else:
        cmd = sys.argv[1] if len(sys.argv) > 1 else "uname -a"
        out, err = run(cmd)
        sys.stdout.write(out)
        if err.strip():
            sys.stderr.write("\n[stderr]\n" + err)
