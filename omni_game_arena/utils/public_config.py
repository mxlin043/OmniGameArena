"""Credential-free configuration metadata for dry runs and saved results."""
from dataclasses import asdict, is_dataclass


_PRIVATE_FIELDS = {
    'apikey', 'token', 'accesstoken', 'refreshtoken', 'password', 'secret',
    'clientsecret', 'authorization', 'credentials', 'headers', 'extraheaders',
    # Endpoint URLs may contain credentials, private hosts, or signed queries.
    'baseurl', 'url',
}


def public_config(value):
    """Return a copy; live objects retain their usable credentials and routes.

    Environment-variable references are safe to retain. Local routing is loaded
    again at resume time instead of persisting resolved credentials in a run.
    """
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        return {
            key: public_config(item)
            for key, item in value.items()
            if ''.join(c for c in str(key).lower() if c.isalnum()) not in _PRIVATE_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [public_config(item) for item in value]
    return value
