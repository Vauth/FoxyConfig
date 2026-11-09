"""
FoxyConfig Verification Script
Validates config.json schema and tests live endpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLACEHOLDER_REGEX = re.compile(r"(\$\{?[a-zA-Z0-9_]+\}?)")
STATUS_START_MARKER = "<!-- foxy-config-status-start -->"
STATUS_END_MARKER = "<!-- foxy-config-status-end -->"

DEFAULT_ENV = {
    "android_version": "16",
    "android_sdk": "36",
}

TARGET_ENDPOINTS = [
    {
        "component": "controlPlane",
        "url": "https://api.accounts.firefox.com/v1/recovery_email/status",
        "method": "GET",
    },
    {
        "component": "controlPlane",
        "url": "https://accounts.firefox.com/",
        "method": "GET",
    },
    {
        "component": "guardian",
        "url": "https://vpn.mozilla.org/",
        "method": "GET",
    },
    {
        "component": "guardian",
        "url": "https://vpn.mozilla.org/api/v1/fpn/status",
        "method": "GET",
    },
    {
        "component": "solver",
        "url": "https://api.accounts.firefox.com/",
        "method": "GET",
    },
    {
        "component": "browser",
        "url": "https://accounts.firefox.com/?context=fx_desktop_v3&service=sync&action=email",
        "method": "GET",
    },
]


@dataclass
class EndpointTestResult:
    component: str
    url: str
    status_code: Optional[int]
    protocol: str
    latency_ms: float
    passed: bool
    error_message: Optional[str] = None
    headers_sent: Dict[str, str] = field(default_factory=dict)


@dataclass
class SchemaValidationResult:
    is_valid: bool
    version: int = 1
    components_found: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def get_git_info() -> Dict[str, str]:
    info = {"commit": "unknown", "branch": "unknown", "message": ""}
    try:
        info["commit"] = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        info["branch"] = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        info["message"] = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        pass
    return info


def get_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def resolve_placeholders(raw: str, env: Dict[str, str] = DEFAULT_ENV) -> str:
    result = raw
    android_ver = env.get("android_version", "14")
    android_sdk = env.get("android_sdk", "34")

    replacements = [
        ("${android_version}", android_ver),
        ("{android_version}", android_ver),
        ("${os_version}", android_ver),
        ("{os_version}", android_ver),
        ("${android_sdk}", android_sdk),
        ("{android_sdk}", android_sdk),
    ]

    for pattern, val in replacements:
        idx = 0
        pattern_lower = pattern.lower()
        while True:
            idx = result.lower().find(pattern_lower, idx)
            if idx == -1:
                break
            result = result[:idx] + val + result[idx + len(pattern) :]
            idx += len(val)

    return result


def validate_schema(config_data: Any) -> SchemaValidationResult:
    result = SchemaValidationResult(is_valid=True)

    if not isinstance(config_data, dict):
        result.is_valid = False
        result.errors.append("Root JSON must be an object")
        return result

    version = config_data.get("version")
    if not isinstance(version, int) or version < 1:
        result.is_valid = False
        result.errors.append("Field 'version' must be a positive integer")
    else:
        result.version = version

    expected_components = ["controlPlane", "guardian", "solver", "browser"]
    for comp in expected_components:
        if comp not in config_data:
            result.is_valid = False
            result.errors.append(f"Missing required component: '{comp}'")
            continue

        comp_obj = config_data[comp]
        if not isinstance(comp_obj, dict):
            result.is_valid = False
            result.errors.append(f"Component '{comp}' must be an object")
            continue

        result.components_found.append(comp)

        headers = comp_obj.get("headers")
        if not isinstance(headers, dict) or not headers:
            result.is_valid = False
            result.errors.append(f"Component '{comp}' must contain a non-empty 'headers' object")
        else:
            for k, v in headers.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    result.is_valid = False
                    result.errors.append(f"Header in '{comp}' must be string key/value: {k}={v}")
                else:
                    unresolved = [
                        m
                        for m in PLACEHOLDER_REGEX.findall(v)
                        if m.lower()
                        not in {
                            "{android_version}",
                            "${android_version}",
                            "{os_version}",
                            "${os_version}",
                            "{android_sdk}",
                            "${android_sdk}",
                        }
                    ]
                    if unresolved:
                        result.warnings.append(
                            f"Unknown placeholder(s) in {comp}.headers['{k}']: {', '.join(unresolved)}"
                        )

        settings = comp_obj.get("settings")
        if settings is not None:
            if not isinstance(settings, dict):
                result.is_valid = False
                result.errors.append(f"Component '{comp}.settings' must be an object")
            elif "protocols" in settings:
                protocols = settings["protocols"]
                if not isinstance(protocols, list):
                    result.is_valid = False
                    result.errors.append(f"Component '{comp}.settings.protocols' must be a list")
                else:
                    valid_protocols = {"HTTP_2", "HTTP_1_1", "H2", "HTTP1.1"}
                    for p in protocols:
                        if not isinstance(p, str) or p.upper() not in valid_protocols:
                            result.errors.append(f"Invalid protocol in '{comp}': {p}")

    return result


def probe_with_httpx(
    url: str,
    method: str,
    headers: Dict[str, str],
    protocols: List[str],
    timeout_sec: float = 12.0,
) -> Tuple[Optional[int], str, float, Optional[str]]:
    import httpx

    enable_http2 = any(p.upper() in ("HTTP_2", "H2") for p in protocols)
    start_time = time.perf_counter()

    try:
        with httpx.Client(http2=enable_http2, follow_redirects=False, timeout=timeout_sec) as client:
            resp = client.request(method, url, headers=headers)
            latency = (time.perf_counter() - start_time) * 1000
            protocol_used = resp.http_version
            return resp.status_code, protocol_used, latency, None
    except Exception as exc:
        latency = (time.perf_counter() - start_time) * 1000
        return None, "UNKNOWN", latency, str(exc)


def probe_with_urllib(
    url: str,
    method: str,
    headers: Dict[str, str],
    timeout_sec: float = 12.0,
) -> Tuple[Optional[int], str, float, Optional[str]]:
    import urllib.error
    import urllib.request

    start_time = time.perf_counter()
    req = urllib.request.Request(url, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            latency = (time.perf_counter() - start_time) * 1000
            return resp.status, "HTTP/1.1", latency, None
    except urllib.error.HTTPError as exc:
        latency = (time.perf_counter() - start_time) * 1000
        return exc.code, "HTTP/1.1", latency, None
    except Exception as exc:
        latency = (time.perf_counter() - start_time) * 1000
        return None, "HTTP/1.1", latency, str(exc)


def run_tests(config_path: Path) -> Tuple[SchemaValidationResult, List[EndpointTestResult]]:
    with open(config_path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    config_data = json.loads(raw_content)
    schema_res = validate_schema(config_data)

    if not schema_res.is_valid:
        return schema_res, []

    has_httpx = False
    try:
        import httpx  # noqa: F401

        has_httpx = True
    except ImportError:
        has_httpx = False

    results: List[EndpointTestResult] = []

    for target in TARGET_ENDPOINTS:
        comp_name = target["component"]
        comp_cfg = config_data.get(comp_name, {})
        raw_headers = comp_cfg.get("headers", {})
        settings = comp_cfg.get("settings", {})
        protocols = settings.get("protocols", ["HTTP_2", "HTTP_1_1"])

        headers = {k: resolve_placeholders(v) for k, v in raw_headers.items()}
        url = target["url"]
        method = target["method"]

        if has_httpx:
            status_code, proto, latency, err = probe_with_httpx(url, method, headers, protocols)
        else:
            status_code, proto, latency, err = probe_with_urllib(url, method, headers)

        if status_code is None:
            passed = False
            error_msg = err or "Connection timed out or network error"
        elif status_code in (403, 406):
            passed = False
            error_msg = f"Fastly bot-protection blocked request: HTTP {status_code}"
        else:
            passed = True
            error_msg = None

        results.append(
            EndpointTestResult(
                component=comp_name,
                url=url,
                status_code=status_code,
                protocol=proto,
                latency_ms=latency,
                passed=passed,
                error_message=error_msg,
                headers_sent=headers,
            )
        )

    return schema_res, results


def format_report(
    schema_res: SchemaValidationResult,
    test_results: List[EndpointTestResult],
    config_meta: Dict[str, Any],
    git_info: Dict[str, str],
    is_for_readme: bool = False,
) -> str:
    lines = []

    # Standalone CI summary (e.g. $GITHUB_STEP_SUMMARY) gets header and metadata
    if not is_for_readme:
        lines.append("## Live Endpoint Verification Status")
        lines.append("")
        total_tests = len(test_results)
        passed_tests = sum(1 for r in test_results if r.passed)
        all_passed = schema_res.is_valid and total_tests > 0 and passed_tests == total_tests
        commit_badge = f"`{git_info['commit']}`" if git_info.get("commit") != "unknown" else "`HEAD`"
        sha_short = config_meta.get("sha256", "")[:12]
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        if all_passed:
            lines.append(f"> **Status:** ✅ **ALL PASS** (0 Bot Blocks / {total_tests} Probes Passed)")
        else:
            lines.append(f"> **Status:** ⚠️ **ISSUES DETECTED** ({passed_tests}/{total_tests} Probes Passed)")

        lines.append(
            f"> **Last Checked:** `{now_utc}` | **Commit:** {commit_badge} | **Config Version:** `v{schema_res.version}` | **SHA-256:** `{sha_short}...`"
        )
        lines.append("")

    if not schema_res.is_valid:
        lines.append("### ❌ Schema Validation Errors")
        for err in schema_res.errors:
            lines.append(f"- ⚠️ {err}")
        lines.append("")

    components = ["controlPlane", "guardian", "solver", "browser"]
    for comp in components:
        comp_results = [r for r in test_results if r.component == comp]
        if not comp_results:
            continue

        comp_passed = all(r.passed for r in comp_results)
        comp_badge = "✅" if comp_passed else "❌"
        lines.append(f"### {comp_badge} `{comp}`")
        lines.append("")
        lines.append("| Target URL | HTTP Status | Fastly Status | Protocol |")
        lines.append("| :--- | :---: | :---: | :---: |")

        for r in comp_results:
            if r.status_code is None:
                http_code = "ERR"
                waf_status = "⚠️ Timeout"
            elif r.status_code in (403, 406):
                http_code = f"**{r.status_code}**"
                waf_status = f"🚫 **BLOCKED ({r.status_code})**"
            else:
                http_code = f"`HTTP {r.status_code}`"
                waf_status = "🛡️ Passed"

            lines.append(
                f"| `{r.url}` | {http_code} | {waf_status} | `{r.protocol}` |"
            )

        lines.append("")

    if schema_res.warnings:
        lines.append("### ⚠️ Warnings")
        for w in schema_res.warnings:
            lines.append(f"- {w}")
        lines.append("")

    failed_tests = [r for r in test_results if not r.passed]
    if failed_tests:
        lines.append("<details>")
        lines.append("<summary><b>🔍 Failure Details & Diagnostic Logs</b></summary>")
        lines.append("")
        for f in failed_tests:
            lines.append(f"#### ❌ `{f.component}` -> `{f.url}`")
            lines.append(f"- **HTTP Status**: `{f.status_code}`")
            lines.append(f"- **Error**: {f.error_message}")
            lines.append("- **Headers Sent**:")
            lines.append("  ```json")
            lines.append("  " + json.dumps(f.headers_sent, indent=2).replace("\n", "\n  "))
            lines.append("  ```")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines).strip()


def update_readme_content(readme_path: Path, report_block: str) -> None:
    wrapped_block = f"{STATUS_START_MARKER}\n{report_block}\n{STATUS_END_MARKER}"

    if readme_path.is_file():
        content = readme_path.read_text(encoding="utf-8")
        if STATUS_START_MARKER in content and STATUS_END_MARKER in content:
            pattern = re.compile(
                f"{re.escape(STATUS_START_MARKER)}.*?{re.escape(STATUS_END_MARKER)}",
                re.DOTALL,
            )
            updated = pattern.sub(wrapped_block, content)
        else:
            updated = content.rstrip() + "\n\n<br>\n\n## Live Endpoint Status\n\n" + wrapped_block + "\n"
    else:
        updated = f"# FoxyVPN Config\n\n{wrapped_block}\n"

    readme_path.write_text(updated, encoding="utf-8")
    print(f"[*] Updated status section in {readme_path.resolve()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify FoxyConfig config.json against Fastly Bot Shield")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--output-report", default="report.md", help="Path to write standalone markdown report")
    parser.add_argument("--readme", default=None, help="Path to README.md to update in-place")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"Error: Config file not found at {config_path.resolve()}", file=sys.stderr)
        return 1

    config_sha = get_file_hash(config_path)
    git_info = get_git_info()
    config_meta = {
        "path": str(config_path.resolve()),
        "size_bytes": config_path.stat().st_size,
        "sha256": config_sha,
    }

    print("=" * 80)
    print("FoxyConfig Verification CI")
    print(f"Config File: {config_path.resolve()} ({config_meta['size_bytes']} bytes)")
    print(f"SHA-256:     {config_sha}")
    print(f"Git Commit:  {git_info['commit']} (branch: {git_info['branch']})")
    print("=" * 80)

    schema_res, test_results = run_tests(config_path)

    if not schema_res.is_valid:
        print("[!] Schema validation failed:")
        for err in schema_res.errors:
            print(f"    - {err}")

    for w in schema_res.warnings:
        print(f"[*] Warning: {w}")

    print(f"[*] Executed {len(test_results)} live endpoint probes:")
    for r in test_results:
        status_sym = "[OK]" if r.passed else "[FAIL]"
        code_str = str(r.status_code) if r.status_code is not None else "ERR"
        print(f"    {status_sym} {r.component:<12} -> {r.url}: HTTP {code_str} ({r.latency_ms:.0f}ms)")
        if not r.passed and r.error_message:
            print(f"         Error: {r.error_message}")

    if args.output_report:
        report_md = format_report(schema_res, test_results, config_meta, git_info, is_for_readme=False)
        output_path = Path(args.output_report)
        output_path.write_text(report_md, encoding="utf-8")
        print(f"[*] Wrote standalone report to {output_path.resolve()}")

    if args.readme:
        readme_report = format_report(schema_res, test_results, config_meta, git_info, is_for_readme=True)
        update_readme_content(Path(args.readme), readme_report)

    all_passed = schema_res.is_valid and len(test_results) > 0 and all(r.passed for r in test_results)
    if all_passed:
        print("[+] All verification checks passed.")
    else:
        print("[-] Verification completed with issues.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
