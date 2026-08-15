"""twingate-device-trust-bridge.

``__version__`` reflects the version stamped into the container image by CI
(via the ``APP_VERSION`` build-arg → env var). Local/uninstalled runs report
``"dev"``.
"""

import os

__version__ = os.environ.get("APP_VERSION", "dev")
