"""Configuration with embedded credentials (DO NOT USE IN PRODUCTION).

These are fake keys that match detection patterns for testing purposes.
"""

# Anthropic API key pattern: sk-ant- followed by 20+ alphanumeric/dash/underscore
ANTHROPIC_KEY = "sk-ant-api03-TESTKEY1234567890abcdefghijklmnop"

# AWS Access Key ID pattern: AKIA followed by exactly 16 uppercase alphanumeric
AWS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"

# GitHub classic PAT pattern: ghp_ followed by exactly 36 alphanumeric
GITHUB_TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123"

# Slack bot token pattern: xoxb- followed by 10+ alphanumeric/dash
SLACK_TOKEN = "xoxb-123456789012-abcdefghijklmn"
