"""Secrets live in the macOS Keychain (login keychain), never in config files or logs."""
import re
import subprocess

SERVICE = "com.namanrai.jarvis"


def get(name: str) -> str | None:
    r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                       capture_output=True, text=True)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def put(name: str, value: str) -> None:
    if not re.fullmatch(r"[\w:.\-]+", value):  # tokens and chat IDs only; also keeps the command below safe
        raise ValueError("unexpected characters in secret")
    # fed through stdin so the secret never appears in the process list
    subprocess.run(["security", "-i"], input=f'add-generic-password -U -s {SERVICE} -a {name} -w "{value}"\n',
                   text=True, check=True, capture_output=True)


def delete(name: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name], capture_output=True)
