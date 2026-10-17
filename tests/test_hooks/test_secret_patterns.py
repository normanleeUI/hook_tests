"""Unit tests for the secret-detection patterns in the global pre-commit.

The patterns live in claude-config/githooks/pre-commit (`_SECRET_PATTERNS`,
the secret leg) and are loaded from there, so there is no copy to drift.
They are bytes patterns (the leg scans the raw staged diff), so every
sample is encoded before matching. Pure regex: no git state, no subprocess.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.test_hooks.hook_runner import load_precommit

PATTERNS = load_precommit()._SECRET_PATTERNS


def _matching(text: str) -> set[str]:
    return {name for name, pat in PATTERNS.items() if pat.search(text.encode())}


class TestSecretPatternsExamples:
    """Explicit positive and negative examples for each pattern."""

    def test_detects_anthropic_key(self) -> None:
        text = "sk-ant-" + "A" * 20
        assert PATTERNS["Anthropic API key"].search(text.encode())

    def test_openai_negative_lookahead_rejects_anthropic(self) -> None:
        text = "sk-ant-" + "A" * 20
        assert not PATTERNS["OpenAI API key"].search(text.encode())

    def test_detects_openai_key(self) -> None:
        text = "sk-" + "A" * 20
        assert PATTERNS["OpenAI API key"].search(text.encode())

    def test_detects_aws_key(self) -> None:
        text = "AKIA" + "A" * 16
        assert PATTERNS["AWS access key ID"].search(text.encode())

    def test_detects_github_classic_pat(self) -> None:
        text = "ghp_" + "A" * 36
        assert PATTERNS["GitHub token"].search(text.encode())

    def test_detects_github_fine_grained_token(self) -> None:
        text = "github_pat_" + "A" * 82
        assert PATTERNS["GitHub fine-grained token"].search(text.encode())

    def test_detects_slack_xoxb_token(self) -> None:
        text = "xoxb-" + "A" * 15
        assert PATTERNS["Slack token"].search(text.encode())

    def test_detects_slack_xoxp_token(self) -> None:
        text = "xoxp-" + "A" * 15
        assert PATTERNS["Slack token"].search(text.encode())

    def test_detects_google_api_key(self) -> None:
        text = "AIza" + "A" * 35
        assert PATTERNS["Google API key"].search(text.encode())

    def test_detects_pem_private_key(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())


class TestSecretPatternsProperties:
    """Hypothesis property tests for pattern robustness."""

    @given(
        suffix=st.from_regex(r"[A-Za-z0-9_-]{20,40}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_anthropic_key_always_matches(self, suffix: str) -> None:
        text = f"sk-ant-{suffix}"
        assert PATTERNS["Anthropic API key"].search(text.encode())

    @given(
        suffix=st.from_regex(r"[A-Za-z0-9]{20,40}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_openai_key_always_matches(self, suffix: str) -> None:
        """OpenAI key: sk- followed by >=20 alphanumeric chars (not starting with ant-)."""
        text = f"sk-{suffix}"
        assert PATTERNS["OpenAI API key"].search(text.encode())

    @given(
        suffix=st.from_regex(r"[0-9A-Z]{16}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_aws_key_always_matches(self, suffix: str) -> None:
        """AWS access key ID: AKIA followed by exactly 16 uppercase alphanumeric chars."""
        text = f"AKIA{suffix}"
        assert PATTERNS["AWS access key ID"].search(text.encode())

    @given(
        suffix=st.from_regex(r"[A-Za-z0-9]{36}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_github_classic_pat_always_matches(self, suffix: str) -> None:
        """GitHub classic PAT: ghp_ followed by exactly 36 alphanumeric chars."""
        text = f"ghp_{suffix}"
        assert PATTERNS["GitHub token"].search(text.encode())

    @given(
        suffix=st.from_regex(r"[A-Za-z0-9_]{82}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_github_fine_grained_always_matches(self, suffix: str) -> None:
        """GitHub fine-grained token: github_pat_ followed by exactly 82 chars."""
        text = f"github_pat_{suffix}"
        assert PATTERNS["GitHub fine-grained token"].search(text.encode())

    @given(
        prefix=st.sampled_from(["xoxb", "xoxp", "xoxa", "xoxr", "xoxs"]),
        suffix=st.from_regex(r"[A-Za-z0-9-]{10,30}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_slack_token_always_matches(self, prefix: str, suffix: str) -> None:
        """Slack token: xox[baprs]- followed by >=10 alphanumeric/hyphen chars."""
        text = f"{prefix}-{suffix}"
        assert PATTERNS["Slack token"].search(text.encode())

    @given(
        suffix=st.from_regex(r"[0-9A-Za-z_-]{35}", fullmatch=True),
    )
    @settings(max_examples=200)
    def test_google_api_key_always_matches(self, suffix: str) -> None:
        """Google API key: AIza followed by exactly 35 chars."""
        text = f"AIza{suffix}"
        assert PATTERNS["Google API key"].search(text.encode())

    @given(
        key_type=st.sampled_from(["RSA ", "EC ", "OPENSSH ", "DSA ", "PGP ", ""]),
    )
    @settings(max_examples=200)
    def test_generic_private_key_always_matches(self, key_type: str) -> None:
        """Generic private key block: PEM header with optional key type prefix."""
        text = f"-----BEGIN {key_type}PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())

    @given(length=st.integers(min_value=1, max_value=19))
    def test_short_anthropic_key_no_match(self, length: int) -> None:
        text = "sk-ant-" + "A" * length
        assert not PATTERNS["Anthropic API key"].search(text.encode())


class TestSecretPatternsBoundaries:
    """Boundary and edge cases from the Step 0d test matrix."""

    def test_anthropic_key_boundary_19_chars_no_match(self) -> None:
        text = "sk-ant-" + "A" * 19
        assert not PATTERNS["Anthropic API key"].search(text.encode())

    def test_anthropic_key_boundary_20_chars_match(self) -> None:
        text = "sk-ant-" + "A" * 20
        assert PATTERNS["Anthropic API key"].search(text.encode())

    def test_pem_certificate_header_no_match(self) -> None:
        text = "-----BEGIN CERTIFICATE-----"
        assert not PATTERNS["Generic private key block"].search(text.encode())

    def test_pem_private_key_header_match(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())

    def test_pem_ec_private_key_match(self) -> None:
        text = "-----BEGIN EC PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())

    def test_pem_openssh_private_key_match(self) -> None:
        text = "-----BEGIN OPENSSH PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())

    def test_pem_bare_private_key_match(self) -> None:
        text = "-----BEGIN PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())

    def test_pem_dsa_private_key_match(self) -> None:
        text = "-----BEGIN DSA PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())

    def test_pem_pgp_private_key_match(self) -> None:
        text = "-----BEGIN PGP PRIVATE KEY-----"
        assert PATTERNS["Generic private key block"].search(text.encode())


class TestSecretPatternLengthProperties:
    """Hypothesis property tests for secret pattern length boundaries."""

    @given(length=st.integers(min_value=15, max_value=25))
    @settings(max_examples=200)
    def test_anthropic_key_length_boundary(self, length: int) -> None:
        """Anthropic key pattern requires >=20 chars after the prefix."""
        text = "sk-ant-" + "A" * length
        match = PATTERNS["Anthropic API key"].search(text.encode())
        if length >= 20:
            assert match, f"Expected match for suffix length {length}"
        else:
            assert not match, f"Expected no match for suffix length {length}"

    @given(length=st.integers(min_value=30, max_value=40))
    @settings(max_examples=200)
    def test_github_token_length_boundary(self, length: int) -> None:
        """GitHub classic PAT pattern requires exactly 36 chars after the prefix.

        The regex is ghp_[A-Za-z0-9]{36} which matches exactly 36 chars.
        For length < 36, no match. For length >= 36, the regex matches
        the first 36 chars (the pattern has no end anchor).
        """
        text = "ghp_" + "A" * length
        match = PATTERNS["GitHub token"].search(text.encode())
        if length >= 36:
            assert match, f"Expected match for suffix length {length}"
        else:
            assert not match, f"Expected no match for suffix length {length}"


class TestSecretPatternsCompleteness:
    """The pre-commit's pattern set is exactly the documented eight."""

    EXPECTED_KEYS = {
        "Anthropic API key",
        "OpenAI API key",
        "AWS access key ID",
        "GitHub token",
        "GitHub fine-grained token",
        "Slack token",
        "Google API key",
        "Generic private key block",
    }

    def test_all_eight_patterns_present(self) -> None:
        assert set(PATTERNS.keys()) == self.EXPECTED_KEYS


# Realistic samples, assembled from parts so this file holds no literal key.
_ALNUM36 = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
REALISTIC = [
    ("sk-ant", "sk-" + "ant-api03-" + "Zq8xW2vB7nM4kL9pR3tY6uI1oP5aS0dF-gH_jK", "Anthropic API key"),
    ("legacy sk", "sk-" + "T3BlbkFJ" + "x9Qw2Er4Ty6Ui8Op0As2Df4Gh6Jk8L", "OpenAI API key"),
    ("sk-proj", "sk-" + "proj-" + "Ab3_dE6-gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6kM9nP2rS5", "OpenAI API key"),
    ("sk-svcacct", "sk-" + "svcacct-" + "Lm4Nb6Vc1Zt8Rd3Xy7Kp2Qw9_Lm4Nb6Vc1Zt8Rd3Xy7K", "OpenAI API key"),
    ("sk-admin", "sk-" + "admin-" + "Qw9Lm4Nb6Vc1Zt8Rd3Xy7Kp2-Qw9Lm4Nb6Vc1Zt8Rd3X", "OpenAI API key"),
    ("AKIA", "AKIA" + "IOSFODNN7EXAMPLE", "AWS access key ID"),
    ("ghp", "ghp_" + _ALNUM36, "GitHub token"),
    ("gho", "gho_" + _ALNUM36, "GitHub token"),
    ("ghu", "ghu_" + _ALNUM36, "GitHub token"),
    ("ghs", "ghs_" + _ALNUM36, "GitHub token"),
    ("ghr", "ghr_" + _ALNUM36, "GitHub token"),
    ("github_pat", "github_" + "pat_" + "11ABCDEFG0" + "x" * 12 + "_" + "Yz" * 29 + "Q", "GitHub fine-grained token"),
    ("xox", "xoxb-" + "123456789012-1234567890123-" + "AbCdEfGhIjKlMnOpQrStUvWx", "Slack token"),
    ("AIza", "AIza" + "SyD-9tSrke72PouQMnMX7KVo0X2a5dEPeGQ", "Google API key"),
    ("private key", "-----BEGIN " + "OPENSSH PRIVATE KEY-----", "Generic private key block"),
]

LOOKALIKES = [
    "task-" + "ManagementDashboardWidget42",
    "risk-" + "AssessmentFrameworkV2Final",
    "disk-" + "UsageMonitoringForServers9",
    "sk-" + "admin-dashboard-for-internal-tools",
    "akia" + "iosfodnn7example",
    "ghp_" + _ALNUM36[:35],  # the 35-character placeholder some docs show
]


class TestRealisticFormats:
    """AC-SEC-07, unit level: each of the 15 formats matches one realistic
    sample (and only its own pattern); realistic look-alikes match nothing."""

    @pytest.mark.parametrize(("label", "sample", "name"), REALISTIC, ids=[c[0] for c in REALISTIC])
    def test_each_format_matches_only_its_pattern(self, label, sample, name) -> None:
        assert _matching(f"key = {sample}\n") == {name}

    @pytest.mark.parametrize("text", LOOKALIKES)
    def test_lookalikes_match_nothing(self, text) -> None:
        assert _matching(f"see {text} here") == set()
