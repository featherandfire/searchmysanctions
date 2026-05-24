"""
Environment-based configuration.

All secrets and external-service URLs are read from the environment. The
old config.py file (gitignored, hardcoded keys) is no longer used.

For local dev, set these via `export` or a docker-compose env_file. In prod
on Fly.io, set them with `flyctl secrets set`.
"""
import os

CENSUS_API_KEY    = os.environ.get("CENSUS_API_KEY", "")
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")

# Sentry — optional. If unset, the SDK is not initialised and error tracking
# is disabled. Set via `flyctl secrets set SENTRY_DSN=...` in prod.
SENTRY_DSN         = os.environ.get("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", "production" if os.environ.get("FLY_APP_NAME") else "local")
SENTRY_RELEASE     = os.environ.get("SENTRY_RELEASE", "")  # set via CI to the git SHA

_DEV_SECRET_KEY = "dev-secret-do-not-use-in-prod"
SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_SECRET_KEY)

# Refuse to boot on Fly with the dev default — would leave Flask session
# cookies signed with a publicly-known key. FLY_APP_NAME is set automatically
# by the Fly runtime, so absence of it means "not on Fly" (local / tests / CI).
if SECRET_KEY == _DEV_SECRET_KEY and os.environ.get("FLY_APP_NAME"):
    raise RuntimeError(
        "SECRET_KEY is unset in a Fly.io environment — refusing to boot with "
        "the dev default. Set via `flyctl secrets set SECRET_KEY=...`."
    )
