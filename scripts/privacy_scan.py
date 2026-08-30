"""Local secret and privacy gate that never prints matched values."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 5 * 1024 * 1024

PERSONAL_ARTIFACT_NAMES = frozenset(
    {
        "resume.pdf",
        "resume_text.txt",
        "resume_ocr.txt",
        "extract_resume.py",
        "extract_pdfplumber.py",
        "render_pdf_pages.py",
        "check_pdf.py",
        "check_ocr.py",
        "ocr_power.ps1",
        "ocr_power_save.ps1",
    }
)
PERSONAL_ARTIFACT_PREFIXES = ("resume_pages/",)
SKIP_PREFIXES = (
    "cst_agent_rag_datatmppytest-eval/",
    "cst_agent_rag_datatmppytest-offline/",
)

PLACEHOLDER_PATTERN = re.compile(
    r"(?i)(?:example|placeholder|replace_with|your[_ -]?(?:api[_ -]?key|token|password|email)|"
    r"dummy|fake|test[_-]?(?:key|token)|changeme|xxx+|todo|<[^>]+>|\$\{[^}]+\})"
)


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    allow_placeholders: bool = True


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int | None = None
    commit: str = ""

    def safe_summary(self) -> str:
        fields = [f"rule={self.rule}", f"path={self.path}"]
        if self.line is not None:
            fields.append(f"line={self.line}")
        if self.commit:
            fields.append(f"commit={self.commit}")
        return ";".join(fields)


RULES = (
    Rule(
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
        allow_placeholders=False,
    ),
    Rule("provider_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b")),
    Rule("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    Rule("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    Rule("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    Rule("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    Rule(
        "jwt_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    Rule(
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password)"
            r"\b\s*[:=]\s*[\"']([^\"']{12,})[\"']"
        ),
    ),
    Rule("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    Rule("credential_url", re.compile(r"(?i)https?://[^\s/:@]+:[^\s/@]+@")),
    Rule(
        "windows_user_path",
        re.compile(r"(?i)\bC:\\Users\\[^\\\s\"']+"),
        allow_placeholders=False,
    ),
    Rule(
        "private_workspace_path",
        re.compile(r"(?i)\bD:\\dnc\\(?:Desktop|Documents|Downloads|iCloudDrive)\\"),
        allow_placeholders=False,
    ),
    Rule(
        "email_address",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    ),
    Rule(
        "mainland_phone",
        re.compile(
            r"(?i)(?:phone|mobile|tel(?:ephone)?|contact|电话|手机|联系方式)"
            r"\s*[:=：-]?\s*[\"']?(?<!\d)1[3-9]\d{9}(?!\d)"
        ),
        allow_placeholders=False,
    ),
)


def _git(*args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
    )


def candidate_paths(scope: str) -> tuple[str, ...]:
    if scope == "staged":
        return tuple(line for line in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").stdout.splitlines() if line)

    tracked = _git("ls-files").stdout.splitlines()
    untracked = [
        line[3:]
        for line in _git("status", "--porcelain=v1", "-uall").stdout.splitlines()
        if line.startswith("?? ")
    ]
    return tuple(dict.fromkeys([*tracked, *untracked]))


def scan_paths(paths: Iterable[str], *, staged: bool = False) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for raw_path in paths:
        path = raw_path.replace("\\", "/").strip('"')
        if _skip_path(path):
            continue
        if _is_personal_artifact(path):
            findings.append(Finding("personal_artifact", path))
            continue
        data = _staged_bytes(path) if staged else _worktree_bytes(path)
        if data is None or len(data) > MAX_TEXT_BYTES or b"\0" in data[:4096]:
            continue
        findings.extend(_scan_text(data.decode("utf-8", "ignore"), path=path))
    return tuple(dict.fromkeys(findings))


def scan_history() -> tuple[Finding, ...]:
    """Scan added/removed patch lines once, including values deleted later."""

    command = [
        "git",
        "log",
        "--all",
        "--format=COMMIT:%H",
        "-p",
        "--no-ext-diff",
        "--no-color",
        "--full-history",
    ]
    findings = list(_scan_git_identity_metadata())
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    assert process.stdout is not None
    commit = ""
    path = ""
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        if line.startswith("COMMIT:"):
            commit = line[7:19]
            path = ""
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("--- a/") and not path:
            path = line[6:]
        elif path and line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            findings.extend(_scan_text(line[1:], path=path, commit=commit, line_offset=None))
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"git history scan failed with exit code {return_code}")
    return tuple(dict.fromkeys(findings))


def _scan_git_identity_metadata() -> tuple[Finding, ...]:
    rows = _git("log", "--all", "--format=%H%x09%ae%x09%ce").stdout.splitlines()
    findings: list[Finding] = []
    for row in rows:
        commit, author_email, committer_email = row.split("\t", 2)
        for email in {author_email.strip().casefold(), committer_email.strip().casefold()}:
            if email and not email.endswith("@users.noreply.github.com"):
                findings.append(Finding("git_identity_email", "<git-metadata>", commit=commit[:12]))
    return tuple(dict.fromkeys(findings))


def _scan_text(
    text: str,
    *,
    path: str,
    commit: str = "",
    line_offset: int | None = 1,
) -> list[Finding]:
    findings: list[Finding] = []
    for index, line in enumerate(text.splitlines() or [text]):
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                if rule.allow_placeholders and PLACEHOLDER_PATTERN.search(line):
                    continue
                if rule.name == "mainland_phone" and _inside_hex_digest(line, match.start(), match.end()):
                    continue
                findings.append(
                    Finding(
                        rule.name,
                        path,
                        None if line_offset is None else line_offset + index,
                        commit,
                    )
                )
                break
    return findings


def _inside_hex_digest(line: str, start: int, end: int) -> bool:
    left = start
    right = end
    while left > 0 and line[left - 1] in "0123456789abcdefABCDEF":
        left -= 1
    while right < len(line) and line[right] in "0123456789abcdefABCDEF":
        right += 1
    return right - left >= 32


def _skip_path(path: str) -> bool:
    return path.startswith(SKIP_PREFIXES)


def _is_personal_artifact(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.name in PERSONAL_ARTIFACT_NAMES or path.startswith(PERSONAL_ARTIFACT_PREFIXES)


def _worktree_bytes(path: str) -> bytes | None:
    target = ROOT / PurePosixPath(path)
    try:
        return target.read_bytes() if target.is_file() else None
    except OSError:
        return None


def _staged_bytes(path: str) -> bytes | None:
    result = _git("show", f":{path}", text=False)
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan Git candidates without printing any matched secret or personal value."
    )
    parser.add_argument(
        "--scope",
        choices=("worktree", "staged", "history"),
        default="worktree",
    )
    args = parser.parse_args()

    if args.scope == "history":
        findings = scan_history()
    else:
        findings = scan_paths(
            candidate_paths(args.scope),
            staged=args.scope == "staged",
        )
    if findings:
        print(f"Privacy scan failed: {len(findings)} redacted finding(s).")
        for finding in findings:
            print(f"- {finding.safe_summary()}")
        return 1
    print(f"Privacy scan passed for {args.scope} scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
