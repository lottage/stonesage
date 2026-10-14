"""StoneSage, the agent platform (docs/LAYERS.md layer 1): the server, the agent loop (courage/), souls, tools, apps.

Built on the foundation (layer 0); apps such as Home (layer 2) plug in through apps.py."""

from lodestone import API_VERSION as _LODESTONE_API
from lodestone import compat as _compat

__version__ = "0.1.0rc6"
API_VERSION = "0.1"      # what an app is built against (apps.py checks it when the app loads; docs/LAYERS.md 7c)
LODESTONE_API = "0.1"    # the Lodestone API this version is written for; checked right here, at import

_compat.require("StoneSage", __version__, "Lodestone", _LODESTONE_API, LODESTONE_API)
