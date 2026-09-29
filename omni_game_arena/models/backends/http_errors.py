"""Shared bounded-retry status policy for commercial model transports.

Treat the entire 5xx class as server failures, including provider-specific
overload codes such as Anthropic's 529. Retry counts and backoff remain with
each caller. See https://platform.claude.com/docs/en/api/errors .
"""

RETRYABLE_HTTP_STATUS = frozenset({408, 409, 425, 429} | set(range(500, 600)))

# After backend retries, IDC may replay one failed match. A 429 can mean an
# exhausted account budget; do not spend another match trying to resolve it.
REPLAYABLE_HTTP_STATUS = RETRYABLE_HTTP_STATUS - {429}
