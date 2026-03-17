#!/usr/bin/env python3
"""Pre-commit hook to detect PII fields in logger calls.

This hook scans Python files for patterns that indicate logging of sensitive
Personal Identifiable Information (PII) fields like CPF, passwords, secrets.

Usage:
    python scripts/detect_pii_logging.py <file1.py> <file2.py> ...

Exit Codes:
    0: No violations found
    1: PII logging violations detected

Examples of VIOLATIONS:
    logger.info(f"CPF: {player.cpf}")
    logger.debug(f"Password: {user.password}")
    logger.error(f"Secret: {settings.jwt_secret}")

Examples of ALLOWED patterns:
    logger.info(f"CPF masked: {mask_cpf(player.cpf)}")
    logger.info("Player registered", extra={"player_id": player.id})
"""

import re
import sys
from pathlib import Path

# PII field names to detect (case-insensitive)
PII_FIELDS = [
    r"\.cpf\b",
    r"\.cpf_encrypted\b",
    r"\.password\b",
    r"\.password_hash\b",
    r"\.pii_encryption_key\b",
    r"\.jwt_secret\b",
    r"\.internal_webhook_secret\b",
    r"\.name_encrypted\b",
    r"\.email\b(?!.*@)",  # email but not domain literal
    r"\.birth_date\b",
    r"\.declared_income\b",
]

# Whitelist functions that safely handle PII
SAFE_FUNCTIONS = [
    r"mask_cpf\(",
    r"mask_email\(",
    r"encrypt_pii\(",
    r"decrypt_cpf\(",  # Used with mask_cpf typically
]

def is_safe_usage(line: str) -> bool:
    """Check if line uses PII within a safe function (e.g., mask_cpf)."""
    return any(re.search(safe_fn, line) for safe_fn in SAFE_FUNCTIONS)

def check_file(filepath: Path) -> list[tuple[int, str]]:
    """Check if file contains unsafe PII logging.

    Returns:
        list[tuple[int, str]]: List of (line_number, line_content) violations
    """
    violations = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        for i, line in enumerate(content.splitlines(), start=1):
            # Skip comments
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            # Check if line contains logger call
            if not re.search(r"logger\.(info|debug|warning|error|critical|exception)", line):
                continue

            # Check if line contains PII field
            has_pii = any(re.search(pii_field, line, re.IGNORECASE) for pii_field in PII_FIELDS)

            if has_pii and not is_safe_usage(line):
                violations.append((i, line.strip()))

    except Exception as e:
        print(f"⚠️  Error reading {filepath}: {e}")

    return violations

def main():
    if len(sys.argv) < 2:
        print("Usage: python detect_pii_logging.py <file1.py> <file2.py> ...")
        sys.exit(0)

    filepaths = [Path(arg) for arg in sys.argv[1:] if arg.endswith(".py")]
    all_violations = {}

    for filepath in filepaths:
        violations = check_file(filepath)
        if violations:
            all_violations[filepath] = violations

    if all_violations:
        print("\n🔴 PII LOGGING VIOLATIONS DETECTED\n")
        print("=" * 70)

        for filepath, violations in all_violations.items():
            print(f"\n📄 {filepath}")
            for line_num, line_content in violations:
                print(f"   Line {line_num}: {line_content}")

        print("\n" + "=" * 70)
        print(f"\n❌ {len(all_violations)} file(s) with {sum(len(v) for v in all_violations.values())} violation(s)\n")
        print("💡 SOLUTION: Use mask_cpf(), mask_email() or exclude PII from logger calls.")
        print("   Example: logger.info('Player registered', extra={'player_id': player.id})\n")

        sys.exit(1)

    print("✅ No PII logging violations detected.")
    sys.exit(0)

if __name__ == "__main__":
    main()
