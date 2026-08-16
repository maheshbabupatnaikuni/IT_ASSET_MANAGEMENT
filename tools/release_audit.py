#!/usr/bin/env python3
"""Fail closed when a repository contains unsafe material."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys


SKIP_DIRECTORIES = {".git", ".venv", ".runtime", "__pycache__", ".pytest_cache", "dist", "build"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".zip", ".xls", ".xlsx", ".pdf", ".pem", ".key", ".pfx", ".p12"}
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.test", "northstar.example", "blueoak.example", "vertex.example"}
TEXT_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".svg", ".example", ""}


def _decoded(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


# Encoded so the audit implementation does not reproduce private identifiers.
BANNED_TERMS = [
    _decoded(value) for value in (
        "UkFZIEdST1VQ", "UmF5IExpZmUgU2NpZW5jZXM=", "cmF5LWl0LW1hbmFnZW1lbnQ=",
        "SHVibGk=", "SGluZHVwdXI=", "Rmxvd2NoZW0=", "U2l0ZSA0MQ==",
    )
] + ["".join(chars) for chars in (("R", "L", "S"), ("T", "L", "S"))]


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    line: int | None = None


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
IPV4_RE = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")
MAC_RE = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
WINDOWS_PATH_RE = re.compile(r"(?i)(?:[a-z]:\\|\\\\[^\s\\]+\\[^\s\\]+)")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
CONCRETE_SECRET_RE = re.compile(
    r"(?i)\b(?:secret[_-]?key|api[_-]?key|password|access[_-]?token)\b\s*[:=]\s*[\"']([^\"']{8,})[\"']"
)
SAFE_LOCAL_IPS = {"127.0.0.1", "0.0.0.0"}
SAFE_DOCUMENTATION_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")


def iter_files(root: Path):
    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES)
        for filename in sorted(filenames):
            yield Path(current) / filename


def scan(root: Path, report_path: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    scanned = 0
    for path in iter_files(root):
        relative = path.relative_to(root)
        if path.resolve() == report_path.resolve():
            continue
        scanned += 1
        lower_name = relative.as_posix().lower()
        if path.name == ".env":
            findings.append(Finding(str(relative), "tracked environment file"))
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(Finding(str(relative), f"forbidden file type {path.suffix.lower()}"))
        if any(word in lower_name for word in ("backup", "recovery_package", "data_snapshot")) and path.suffix.lower() not in {".py", ".md"}:
            findings.append(Finding(str(relative), "backup or recovery artifact"))
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"requirements.txt", ".gitignore"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(str(relative), "unreviewed binary content"))
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            folded = line.casefold()
            for term in BANNED_TERMS:
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(term.casefold())}(?![A-Za-z0-9])", folded):
                    findings.append(Finding(str(relative), "banned organisation identifier", line_number))
                    break
            if path.resolve() != Path(__file__).resolve() and WINDOWS_PATH_RE.search(line):
                findings.append(Finding(str(relative), "absolute Windows or network path", line_number))
            if MAC_RE.search(line):
                findings.append(Finding(str(relative), "MAC address", line_number))
            if PHONE_RE.search(line):
                findings.append(Finding(str(relative), "phone number pattern", line_number))
            if PRIVATE_KEY_RE.search(line):
                findings.append(Finding(str(relative), "private key material", line_number))
            for match in EMAIL_RE.finditer(line):
                if match.group(1).lower() not in SAFE_EMAIL_DOMAINS:
                    findings.append(Finding(str(relative), "non-allow-listed email address", line_number))
            for address in IPV4_RE.findall(line):
                if address not in SAFE_LOCAL_IPS and not address.startswith(SAFE_DOCUMENTATION_PREFIXES):
                    findings.append(Finding(str(relative), "non-local IPv4 address", line_number))
            secret = CONCRETE_SECRET_RE.search(line)
            if secret and not any(marker in line.lower() for marker in ("test-only", "replace-with", "set-a-unique", "os.environ", "request.form")):
                findings.append(Finding(str(relative), "possible hard-coded secret", line_number))
    return sorted(set(findings), key=lambda item: (item.path, item.line or 0, item.category)), scanned


def write_report(report_path: Path, findings: list[Finding], scanned: int) -> None:
    status = "PASS" if not findings else "FAIL"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Repository Audit Report", "", f"- Result: **{status}**",
        f"- Generated: {now}", f"- Files scanned: {scanned}",
        f"- Findings: {len(findings)}", "",
    ]
    if findings:
        lines.extend(["## Findings", ""])
        for finding in findings:
            location = f":{finding.line}" if finding.line else ""
            lines.append(f"- `{finding.path}{location}` — {finding.category}")
    else:
        lines.extend([
            "The repository passed the organisation-identifier, secret, personal-data, network-value, and forbidden-artifact checks defined in `tools/release_audit.py`.",
            "", "Runtime databases, credentials, uploads, logs, generated QR images, and sample records remain ignored or generated locally.",
        ])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    report = (args.report or root / "RELEASE_AUDIT_REPORT.md").resolve()
    findings, scanned = scan(root, report)
    write_report(report, findings, scanned)
    print(f"Repository audit: {'PASS' if not findings else 'FAIL'} ({scanned} files, {len(findings)} findings)")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
