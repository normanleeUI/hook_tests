"""Unit tests for the regex patterns in scan_secrets_on_commit.py.

Tests the 8 secret-detection patterns from the hook in isolation (pure
regex, no git state, no subprocess calls). This is faster and more
precise than full hook integration tests -- if a pattern is wrong, these
tests catch it without needing a staged diff.
"""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

# Duplicate of PATTERNS from ~/.claude/hooks/scan_secrets_on_commit.py
# (lines 21-30). Compiled here for direct regex testing.
PATTERNS: dict[str, re.Pattern[str]] = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "OpenAI API key": re.compile(r"sk-(?!ant-)[A-Za-z0-9]{20,}"),
    "AWS access key ID": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub personal access token (classic)": re.compile(r"ghp_[A-Za-z0-9]{36}"),
    "GitHub fine-grained token": re.compile(r"github_pat_[A-Za-z0-9_]{82}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "Generic private key block": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
    ),
}


class TestSecretPatternsExamples:
    """Explicit positive and negative examples for each pattern."""

    def test_detects_anthropic_key(self) -> None:
        text = "sk-ant-" + "A" * 20
        assert PATTERNS["Anthropic API key"].search(text)

    def test_openai_negative_lookahead_rejects_anthropic(self) -> None:
        text = "sk-ant-" + "A" * 20
        assert not PATTERNS["OpenAI API key"].search(text)

    def test_detects_openai_key(self) -> None:
        text = "sk-" + "A" * 20
        assert PATTERNS["OpenAI API key"].search(text)

    def test_detects_aws_key(self) -> None:
        text = "AKIA" + "A" * 16
        assert PATTERNS["AWS access key ID"].search(text)

    def test_detects_github_classic_pat(self) -> None:
        text = "ghp_" + "A" * 36
        assert PATTERNS["GitHub personal access token (classic)"].search(text)

    def test_detects_github_fine_grained_token(self) -> None:
        text = "github_pat_" + "A" * 82
        assert PATTERNS["GitHub fine-grained token"].search(text)

    def test_detects_slack_xoxb_token(self) -> None:
        text = "xoxb-" + "A" * 15
        assert PATTERNS["Slack token"].search(text)

    def test_detects_slack_xoxp_token(self) -> None:
        text = "xoxp-" + "A" * 15
        assert PATTERNS["Slack token"].search(text)

    def test_detects_google_api_key(self) -> None:
        text = "AIza" + "A" * 35
        assert PATTERNS["Google API key"].search(text)

    def test_detects_pem_private_key(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text)


class TestSecretPatternsProperties:
    """Hypothesis property tests for pattern robustness."""

    @given(
        suffix=st.from_regex(r"[A-Za-z0-9_-]{20,40}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_anthropic_key_always_matches(self, suffix: str) -> None:
        text = f"sk-ant-{suffix}"
        assert PATTERNS["Anthropic API key"].search(text)

    @given(length=st.integers(min_value=1, max_value=19))
    def test_short_anthropic_key_no_match(self, length: int) -> None:
        text = "sk-ant-" + "A" * length
        assert not PATTERNS["Anthropic API key"].search(text)


class TestSecretPatternsBoundaries:
    """Boundary and edge cases from the Step 0d test matrix."""

    def test_anthropic_key_boundary_19_chars_no_match(self) -> None:
        text = "sk-ant-" + "A" * 19
        assert not PATTERNS["Anthropic API key"].search(text)

    def test_anthropic_key_boundary_20_chars_match(self) -> None:
        text = "sk-ant-" + "A" * 20
        assert PATTERNS["Anthropic API key"].search(text)

    def test_pem_certificate_header_no_match(self) -> None:
        text = "-----BEGIN CERTIFICATE-----"
        assert not PATTERNS["Generic private key block"].search(text)

    def test_pem_private_key_header_match(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text)

    def test_pem_ec_private_key_match(self) -> None:
        text = "-----BEGIN EC PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text)

    def test_pem_openssh_private_key_match(self) -> None:
        text = "-----BEGIN OPENSSH PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text)

    def test_pem_bare_private_key_match(self) -> None:
        text = "-----BEGIN PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text)

    def test_pem_dsa_private_key_match(self) -> None:
        text = "-----BEGIN DSA PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text)

    def test_pem_pgp_private_key_match(self) -> None:
        text = "-----BEGIN PGP PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text)


class TestSecretPatternsCompleteness:
    """Verify the test-local PATTERNS dict is complete and matches the hook source."""

    EXPECTED_KEYS = {
        "Anthropic API key",
        "OpenAI API key",
        "AWS access key ID",
        "GitHub personal access token (classic)",
        "GitHub fine-grained token",
        "Slack token",
        "Google API key",
        "Generic private key block",
    }

    def test_all_eight_patterns_present(self) -> None:
        assert set(PATTERNS.keys()) == self.EXPECTED_KEYS

    def test_patterns_match_hook_source(self) -> None:
        """Canary: detect if hook patterns diverge from the test-local copy."""
        from pathlib import Path

        hook_path = Path.home() / ".claude" / "hooks" / "scan_secrets_on_commit.py"
        source = hook_path.read_text()
        for name, compiled in PATTERNS.items():
            assert compiled.pattern in source, (
                f"Pattern {name!r} ({compiled.pattern!r}) not found in hook source"
            )
