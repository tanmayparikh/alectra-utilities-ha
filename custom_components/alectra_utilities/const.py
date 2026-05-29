DOMAIN = "alectra_utilities"

# User-provided in config flow
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCOUNT_NUMBER = "account_number"

# Discovered from API and persisted in entry.data (not user-facing)
CONF_CUSTOMER_NUMBER = "customer_number"
CONF_METER_NUMBER = "meter_number"

DEFAULT_SCAN_INTERVAL = 3600  # 1 hour in seconds

ATTR_TIER_TOU = "tier_tou"
ATTR_RATE_PLAN = "rate_plan"
ATTR_METER_NUMBER = "meter_number"
