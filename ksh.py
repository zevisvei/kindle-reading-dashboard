#!/usr/bin/env python3
"""Run a command on the Kindle over SSH (busybox sshd, no sftp) and print output.

Connection details are read from environment variables so no credentials are
committed. Defaults match a jailbroken Kindle with USB networking (usbnet):
    KINDLE_HOST (default 192.168.15.244)   # usbnet device IP
    KINDLE_USER (default root)
    KINDLE_PW   (default kindle)            # the well-known default jailbreak password
"""
import os, sys, paramiko

HOST = os.environ.get("KINDLE_HOST", "192.168.15.244")
USER = os.environ.get("KINDLE_USER", "root")
PW = os.environ.get("KINDLE_PW", "kindle")

def run(cmd):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = c.exec_command(cmd, timeout=60)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    c.close()
    return out, err

def put(local, remote):
    # busybox sshd has no sftp subsystem -> stream bytes through `cat > remote`
    with open(local, "rb") as f:
        data = f.read()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15, look_for_keys=False, allow_agent=False)
    stdin, stdout, stderr = c.exec_command(f"cat > '{remote}'")
    stdin.channel.sendall(data)
    stdin.channel.shutdown_write()
    stdout.channel.recv_exit_status()
    err = stderr.read().decode("utf-8", "replace")
    c.close()
    print(f"put {local} -> {remote} ({len(data)} bytes){' ERR:'+err if err.strip() else ''}")

def get(remote, local):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15, look_for_keys=False, allow_agent=False)
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
