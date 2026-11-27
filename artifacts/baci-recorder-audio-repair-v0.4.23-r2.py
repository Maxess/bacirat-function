#!/usr/bin/python3
"""Apply or revert the audio replay repair on a managed, idle Baci VPS 1.3.2."""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import stat
import struct
import sys
import tempfile
from types import ModuleType

APP = Path('/opt/school-classroom')
HELPERS = {
    'vps_media.py': '6dd018f843995131fc69d6c434d900b4f10be1a84ac547c97d9f1833bd1c6993',
    'baci-vps.py': '6455e2f0187f014d9dd5456a3165e8f05f632ef3dceae23283a7a776b7824f1a',
}
AFTER_SHA = '54cb9ab61118e60412357b0555d72325fb0cd1b7c8a2136c70f17ba16de11a1c'
STOCK_SHA = '6fb4a82aefdee96b1c2f52240baa580625caf116bfab79f5a2330a687101aa7f'
RENDERER_ASSETS = {
    'archive.py': 'af6165d251a3df09e377f767ff9df205d4b51992635ef8f19280c864d02b71e2',
    'movie.py': 'a91aded20c3b7eaee6096a0514b2f6efa69e293f0427ae03395311ae2ce29802',
    'postprocess.py': '6bedc7bd6636d360c47529dc02212ce85128c6eb124854f8d8c27e61ef0a4180',
}
COMPACT_SHA = 'e224bd2088dab6a22b0db71095272a8e563b9d368c221772db8d389af64061dd'
UNIT_SHA = 'c5f0c7ca7dafb92837f6cb1e2721cc177377ee34ce3d192d84a13029185ff2ea'
# Replaced only by the deterministic build. Never read a patch from the cwd.
STOCK = (
    b'KCgpID0+IHsKICAgICd1c2Ugc3RyaWN0JzsKICAgIC8vIFZpcnR1YWwgY2xvY2s6IGNvbXBvc2luZyB0aGUgZnJhbWUgZm9yIHRp'
    b'bWUgVCBkb2VzIG5vdCB3YWl0IGZvciB3YWxsIHRpbWUgVC4KICAgIC8vIEZGbXBlZyByZWNlaXZlcyBsb3NzbGVzcyBQTkcgcGl4'
    b'ZWxzICsgUENNOyBILjI2NC9BQUMgaXMgdGhlIG9ubHkgZmlsbSBlbmNvZGUuCiAgICBjb25zdCBlbCA9IGlkID0+IGRvY3VtZW50'
    b'LmdldEVsZW1lbnRCeUlkKGlkKTsKICAgIGNvbnN0IGNhbnZhcyA9IGVsKCdmaWxtJyksIHBhcGVyID0gZWwoJ2JvYXJkLXBhcGVy'
    b'JyksIGluayA9IGVsKCdib2FyZC1pbmsnKTsKICAgIGNvbnN0IHBhaW50ID0gd2luZG93LkFyY2hpdmVGcmFtZShjYW52YXMpOwog'
    b'ICAgY29uc3QgZnJhbWVTYW1wbGVzID0gNDAwMDsgLy8gNDgwMDAgSHogLyAxMiBmcHMKICAgIGNvbnN0IG9wdXMgPSB7Y29kZWM6'
    b'J29wdXMnLHNhbXBsZVJhdGU6NDgwMDAsbnVtYmVyT2ZDaGFubmVsczoyfTsKICAgIGNvbnN0IHZpZGVvQ29kZWNzID0geyd2aWRl'
    b'by92cDgnOid2cDgnLCd2aWRlby9oMjY0JzonYXZjMS40MkUwMUYnfTsKICAgIGNvbnN0IHRyYWNrcyA9IG5ldyBNYXAoKSwgYXVk'
    b'aW8gPSBuZXcgTWFwKCksIHF1ZXVlID0gW10sIHNvdW5kID0gW107CiAgICBsZXQgc2NvcGUsIGhlYWRpbmcsIHRlYWNoZXIsIGFy'
    b'Y2hpdmUsIGRvYywgbmF0aXZlQ2xvY2ssIGxhc2VyID0gW10sIGhhZExhc2VyID0gZmFsc2UsIGRpcnR5ID0gdHJ1ZSwgZmFpbHVy'
    b'ZSwgZm9udHM7CiAgICBjb25zdCBib2FyZCA9IHdpbmRvdy5DbGFzc3Jvb21Cb2FyZChlbCgnY2FwdHVyZS1ib2FyZCcpLCB7CiAg'
    b'ICAgICAgcmFzdGVyT25seTp0cnVlLGNhbkVkaXQ6KCkgPT4gZmFsc2UsaWRlbnRpdHk6KCkgPT4gbnVsbCxhdXRob3JOYW1lOigp'
    b'ID0+ICcnLG5vdGljZTooKSA9PiB7IGZhaWx1cmUgPSAnYXJjaGl2ZWQgYm9hcmQgdW5hdmFpbGFibGUnOyB9LAogICAgfSk7CiAg'
    b'ICBjb25zdCBjb25maWd1cmUgPSBjb2RlYyA9PiAoe2NvZGVjLG9wdGltaXplRm9yTGF0ZW5jeTp0cnVlLGhhcmR3YXJlQWNjZWxl'
    b'cmF0aW9uOidwcmVmZXItc29mdHdhcmUnfSk7CiAgICBmdW5jdGlvbiBmYWlsKCkgeyBmYWlsdXJlID0gJ2FyY2hpdmVkIG1lZGlh'
    b'IGRlY29kZSBmYWlsZWQnOyB9CiAgICBmdW5jdGlvbiBjbGVhclZpZGVvKGl0ZW0pIHsKICAgICAgICBpZiAoaXRlbS5kZWNvZGVy'
    b'ICYmIGl0ZW0uZGVjb2Rlci5zdGF0ZSAhPT0gJ2Nsb3NlZCcpIGl0ZW0uZGVjb2Rlci5jbG9zZSgpOwogICAgICAgIGl0ZW0uZGVj'
    b'b2RlciA9IG51bGw7CiAgICAgICAgaXRlbS52aWRlbz8uY2xvc2UoKTsgaXRlbS52aWRlbyA9IG51bGw7CiAgICB9CiAgICBmdW5j'
    b'dGlvbiBkZWNvZGVWaWRlbyhpdGVtLCBhdCwgY29kZWMsIGtleSwgZW5jb2RlZCkgewogICAgICAgIGlmIChpdGVtLm11dGVkIHx8'
    b'ICghaXRlbS5kZWNvZGVyICYmICFrZXkpKSByZXR1cm47CiAgICAgICAgaWYgKGl0ZW0uY29kZWMgIT09IGNvZGVjIHx8ICFpdGVt'
    b'LmRlY29kZXIpIHsKICAgICAgICAgICAgY2xlYXJWaWRlbyhpdGVtKTsKICAgICAgICAgICAgaWYgKCFrZXkgfHwgIXZpZGVvQ29k'
    b'ZWNzW2NvZGVjXSkgdGhyb3cgbmV3IEVycm9yKCdpbnZhbGlkIHZpZGVvIGNvbmZpZ3VyYXRpb24nKTsKICAgICAgICAgICAgaXRl'
    b'bS5jb2RlYyA9IGNvZGVjOwogICAgICAgICAgICBpdGVtLmRlY29kZXIgPSBuZXcgVmlkZW9EZWNvZGVyKHsKICAgICAgICAgICAg'
    b'ICAgIG91dHB1dChmcmFtZSkgewogICAgICAgICAgICAgICAgICAgIGl0ZW0udmlkZW8/LmNsb3NlKCk7IGl0ZW0udmlkZW8gPSBm'
    b'cmFtZTsKICAgICAgICAgICAgICAgICAgICBkaXJ0eSA9IHRydWU7CiAgICAgICAgICAgICAgICAgICAgaXRlbS5kZWNvZGVkPy4o'
    b'KTsKICAgICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICBlcnJvcigpIHsgZmFpbCgpOyBpdGVtLmRlY29kZWQ/LigpOyB9'
    b'LAogICAgICAgICAgICB9KTsKICAgICAgICAgICAgaXRlbS5kZWNvZGVyLmNvbmZpZ3VyZShjb25maWd1cmUodmlkZW9Db2RlY3Nb'
    b'Y29kZWNdKSk7CiAgICAgICAgfQogICAgICAgIGNvbnN0IGNodW5rID0gbmV3IEVuY29kZWRWaWRlb0NodW5rKHt0eXBlOmtleSA/'
    b'ICdrZXknIDogJ2RlbHRhJyx0aW1lc3RhbXA6YXQqMTAwMCxkYXRhOlVpbnQ4QXJyYXkuZnJvbUJhc2U2NChlbmNvZGVkKX0pOwog'
    b'ICAgICAgIHJldHVybiBuZXcgUHJvbWlzZSgocmVzb2x2ZSxyZWplY3QpID0+IHsKICAgICAgICAgICAgLy8gQ2xhc3Nyb29tIHB1'
    b'YmxpY2F0aW9ucyB1c2UgcmVhbC10aW1lLCBub24tcmVvcmRlcmVkIHBpY3R1cmVzLiBGYWlsIGNsb3NlZCBpZiBhCiAgICAgICAg'
    b'ICAgIC8vIG1hbGZvcm1lZCBhcmNoaXZlIG5ldmVyIHByb2R1Y2VzIGEgZnJhbWU7IGRvbid0IHNpbGVudGx5IHNlbmQgYSBibGFu'
    b'ayBjYW1lcmEuCiAgICAgICAgICAgIGNvbnN0IHRpbWVyID0gc2V0VGltZW91dCgoKSA9PiByZWplY3QobmV3IEVycm9yKCd2aWRl'
    b'byBkZWNvZGVyIGRpZCBub3QgcHJvZHVjZSBhIGZyYW1lJykpLDE4MDApOwogICAgICAgICAgICBpdGVtLmRlY29kZWQgPSAoKSA9'
    b'PiB7IGNsZWFyVGltZW91dCh0aW1lcik7IHJlc29sdmUoKTsgfTsKICAgICAgICAgICAgdHJ5IHsgaXRlbS5kZWNvZGVyLmRlY29k'
    b'ZShjaHVuayk7IH0KICAgICAgICAgICAgY2F0Y2ggKGVycm9yKSB7IGNsZWFyVGltZW91dCh0aW1lcik7IHJlamVjdChlcnJvcik7'
    b'IH0KICAgICAgICB9KTsKICAgIH0KICAgIGZ1bmN0aW9uIG9uU291bmQoZnJhbWUpIHsKICAgICAgICB0cnkgewogICAgICAgICAg'
    b'ICBjb25zdCBuID0gZnJhbWUubnVtYmVyT2ZGcmFtZXM7CiAgICAgICAgICAgIGlmIChmcmFtZS5zYW1wbGVSYXRlICE9PSA0ODAw'
    b'MCB8fCBuID4gNTc2MCB8fCBzb3VuZC5sZW5ndGggPj0gMTIwMDApIHsgZmFpbCgpOyByZXR1cm47IH0KICAgICAgICAgICAgY29u'
    b'c3Qgc2FtcGxlcyA9IG5ldyBGbG9hdDMyQXJyYXkobiksIHBsYW5lID0gbmV3IEZsb2F0MzJBcnJheShuKTsKICAgICAgICAgICAg'
    b'Zm9yIChsZXQgY2g9MDsgY2g8ZnJhbWUubnVtYmVyT2ZDaGFubmVsczsgY2grKykgewogICAgICAgICAgICAgICAgZnJhbWUuY29w'
    b'eVRvKHBsYW5lLHtwbGFuZUluZGV4OmNoLGZvcm1hdDonZjMyLXBsYW5hcid9KTsKICAgICAgICAgICAgICAgIGZvciAobGV0IGk9'
    b'MDtpPG47aSsrKSBzYW1wbGVzW2ldICs9IHBsYW5lW2ldL2ZyYW1lLm51bWJlck9mQ2hhbm5lbHM7CiAgICAgICAgICAgIH0KICAg'
    b'ICAgICAgICAgc291bmQucHVzaChbTWF0aC5yb3VuZChmcmFtZS50aW1lc3RhbXAqMC4wNDgpLHNhbXBsZXNdKTsKICAgICAgICB9'
    b'IGZpbmFsbHkgeyBmcmFtZS5jbG9zZSgpOyB9CiAgICB9CiAgICBmdW5jdGlvbiBkZWNvZGVBdWRpbyhpZCwgYXQsIHRzLCBlbmNv'
    b'ZGVkKSB7CiAgICAgICAgbGV0IGl0ZW0gPSBhdWRpby5nZXQoaWQpOwogICAgICAgIGlmICghaXRlbSkgewogICAgICAgICAgICBj'
    b'b25zdCBkZWNvZGVyID0gbmV3IEF1ZGlvRGVjb2Rlcih7b3V0cHV0Om9uU291bmQsZXJyb3I6ZmFpbH0pOwogICAgICAgICAgICBk'
    b'ZWNvZGVyLmNvbmZpZ3VyZShvcHVzKTsKICAgICAgICAgICAgYXVkaW8uc2V0KGlkLGl0ZW0gPSB7ZGVjb2Rlcix0cyxwb3M6YXQq'
    b'NDh9KTsKICAgICAgICB9IGVsc2UgewogICAgICAgICAgICBpdGVtLnBvcyArPSAodHMtaXRlbS50cyl8MDsKICAgICAgICAgICAg'
    b'Ly8gUHJlc2VydmUgY29udGludW91cyBPcHVzIHNhbXBsZXMgdGhyb3VnaCBhcnJpdmFsIGppdHRlci4gTmF0aXZlCiAgICAgICAg'
    b'ICAgIC8vIHNvdXJjZXMgY2FycnkgYSBzaGFyZWQgc2VuZGVyLXJlcG9ydCBjbG9jazogc2xvd2x5IGNvcnJlY3QgZHJpZnQKICAg'
    b'ICAgICAgICAgLy8gd2l0aG91dCBhIHNpbmdsZSBqdW1wIGZvciBlYWNoIHNtYWxsIGNvcnJlY3Rpb24uCiAgICAgICAgICAgIGNv'
    b'bnN0IGRyaWZ0ID0gYXQqNDgtaXRlbS5wb3M7CiAgICAgICAgICAgIGlmIChNYXRoLmFicyhkcmlmdCkgPiAobmF0aXZlQ2xvY2sg'
    b'PyA1NzYwIDogMTIwMDApKSBpdGVtLnBvcyA9IGF0KjQ4OwogICAgICAgICAgICBlbHNlIGlmIChuYXRpdmVDbG9jayAmJiBNYXRo'
    b'LmFicyhkcmlmdCkgPiA0ODApIGl0ZW0ucG9zICs9IE1hdGguc2lnbihkcmlmdCkqMzsKICAgICAgICAgICAgaXRlbS50cyA9IHRz'
    b'OwogICAgICAgIH0KICAgICAgICBpdGVtLmRlY29kZXIuZGVjb2RlKG5ldyBFbmNvZGVkQXVkaW9DaHVuayh7dHlwZTona2V5Jyx0'
    b'aW1lc3RhbXA6TWF0aC5yb3VuZChpdGVtLnBvcy8wLjA0OCksZGF0YTpVaW50OEFycmF5LmZyb21CYXNlNjQoZW5jb2RlZCl9KSk7'
    b'CiAgICAgICAgcmV0dXJuIGl0ZW0uZGVjb2RlcjsKICAgIH0KICAgIGFzeW5jIGZ1bmN0aW9uIGxvYWQodXBkYXRlcykgewogICAg'
    b'ICAgIGNvbnN0IHBlbmRpbmcgPSBuZXcgU2V0KCksIGVuZGVkID0gW107CiAgICAgICAgZm9yIChjb25zdCB1cGRhdGUgb2YgdXBk'
    b'YXRlcykgewogICAgICAgICAgICBjb25zdCBbdHlwZSxpZCxhdCxjb2RlYyx0cywsYnl0ZXNdID0gdXBkYXRlOwogICAgICAgICAg'
    b'ICBpZiAodHlwZSA9PT0gJ21lZGlhJyAmJiBjb2RlYyA9PT0gJ2F1ZGlvL29wdXMnKSBwZW5kaW5nLmFkZChkZWNvZGVBdWRpbyhp'
    b'ZCxhdCx0cyxieXRlcykpOwogICAgICAgICAgICBlbHNlIHF1ZXVlLnB1c2godXBkYXRlKTsKICAgICAgICAgICAgaWYgKHR5cGUg'
    b'PT09ICdlbmQnKSBlbmRlZC5wdXNoKGlkKTsKICAgICAgICB9CiAgICAgICAgYXdhaXQgUHJvbWlzZS5hbGwoQXJyYXkuZnJvbShw'
    b'ZW5kaW5nLCBkZWNvZGVyID0+IGRlY29kZXIuZmx1c2goKSkpOwogICAgICAgIGZvciAoY29uc3QgaWQgb2YgZW5kZWQpIHsKICAg'
    b'ICAgICAgICAgY29uc3QgaXRlbSA9IGF1ZGlvLmdldChpZCk7CiAgICAgICAgICAgIGlmIChpdGVtPy5kZWNvZGVyLnN0YXRlID09'
    b'PSAnY29uZmlndXJlZCcpIGl0ZW0uZGVjb2Rlci5jbG9zZSgpOwogICAgICAgICAgICBhdWRpby5kZWxldGUoaWQpOwogICAgICAg'
    b'IH0KICAgICAgICBxdWV1ZS5zb3J0KChhLGIpID0+IGFbMl0tYlsyXSk7CiAgICB9CiAgICBhc3luYyBmdW5jdGlvbiBhcHBseShb'
    b'dHlwZSxpZCxhdCwuLi5hcmdzXSkgewogICAgICAgIGlmICh0eXBlID09PSAnYm9hcmQnKSB7CiAgICAgICAgICAgIGNvbnN0IFtu'
    b'ZXh0LGxheW91dCxyZXF1ZXN0ZWRBdF0gPSBhcmdzOwogICAgICAgICAgICBhcmNoaXZlID0gbGF5b3V0OwogICAgICAgICAgICBp'
    b'ZiAoJ2RvYycgaW4gbmV4dCkgZG9jID0gbmV4dC5kb2M7CiAgICAgICAgICAgIGxhc2VyID0gKG5leHQubGFzZXI/LnBvaW50cyB8'
    b'fCBbXSkubWFwKChbeCx5LGxlZnRdKSA9PiBbeCx5LHJlcXVlc3RlZEF0K2xlZnRdKTsKICAgICAgICAgICAgYXdhaXQgYm9hcmQu'
    b'c3luYyh7Li4ubmV4dCxsYXNlcjpudWxsfSxzY29wZSk7CiAgICAgICAgICAgIGRpcnR5ID0gdHJ1ZTsKICAgICAgICB9IGVsc2Ug'
    b'aWYgKHR5cGUgPT09ICdvcGVuJykgewogICAgICAgICAgICBjb25zdCBbc291cmNlLGlkZW50aXR5LHNpZCxwdWIsbXV0ZWRdID0g'
    b'YXJnczsKICAgICAgICAgICAgaWYgKHNvdXJjZSAhPT0gJ21pY3JvcGhvbmUnKSB0cmFja3Muc2V0KGlkLHtzb3VyY2UsaWRlbnRp'
    b'dHksc2lkLHB1YixtdXRlZH0pOwogICAgICAgIH0gZWxzZSBpZiAodHlwZSA9PT0gJ211dGUnIHx8IHR5cGUgPT09ICdlbmQnKSB7'
    b'CiAgICAgICAgICAgIGNvbnN0IGl0ZW0gPSB0cmFja3MuZ2V0KGlkKTsKICAgICAgICAgICAgaWYgKGl0ZW0pIHsKICAgICAgICAg'
    b'ICAgICAgIGNsZWFyVmlkZW8oaXRlbSk7CiAgICAgICAgICAgICAgICBpdGVtLm11dGVkID0gYXJnc1swXTsKICAgICAgICAgICAg'
    b'ICAgIGlmICh0eXBlID09PSAnZW5kJykgdHJhY2tzLmRlbGV0ZShpZCk7CiAgICAgICAgICAgICAgICBkaXJ0eSA9IHRydWU7CiAg'
    b'ICAgICAgICAgIH0KICAgICAgICB9IGVsc2UgaWYgKHR5cGUgPT09ICdtZWRpYScpIHsKICAgICAgICAgICAgY29uc3QgaXRlbSA9'
    b'IHRyYWNrcy5nZXQoaWQpOwogICAgICAgICAgICBpZiAoaXRlbSkgYXdhaXQgZGVjb2RlVmlkZW8oaXRlbSxhdCxhcmdzWzBdLGFy'
    b'Z3NbMl0sYXJnc1szXSk7CiAgICAgICAgfSBlbHNlIHRocm93IG5ldyBFcnJvcigndW5rbm93biBzb3VyY2UgZXZlbnQnKTsKICAg'
    b'IH0KICAgIGZ1bmN0aW9uIG1peGVkKGluZGV4KSB7CiAgICAgICAgY29uc3Qgc3RhcnQgPSBpbmRleCpmcmFtZVNhbXBsZXMsIHN0'
    b'b3AgPSBzdGFydCtmcmFtZVNhbXBsZXM7CiAgICAgICAgbGV0IGZsb2F0czsKICAgICAgICBmb3IgKGxldCBpPXNvdW5kLmxlbmd0'
    b'aC0xOyBpPj0wOyBpLS0pIHsKICAgICAgICAgICAgY29uc3QgW2F0LHNhbXBsZXNdID0gc291bmRbaV07CiAgICAgICAgICAgIGNv'
    b'bnN0IGVuZCA9IGF0K3NhbXBsZXMubGVuZ3RoOwogICAgICAgICAgICBpZiAoYXQgPCBzdG9wICYmIGVuZCA+IHN0YXJ0KSB7CiAg'
    b'ICAgICAgICAgICAgICBmbG9hdHMgPz89IG5ldyBGbG9hdDMyQXJyYXkoZnJhbWVTYW1wbGVzKTsKICAgICAgICAgICAgICAgIGZv'
    b'ciAobGV0IHBvcz1NYXRoLm1heChhdCxzdGFydCk7IHBvczxNYXRoLm1pbihlbmQsc3RvcCk7IHBvcysrKSBmbG9hdHNbcG9zLXN0'
    b'YXJ0XSArPSBzYW1wbGVzW3Bvcy1hdF07CiAgICAgICAgICAgIH0KICAgICAgICAgICAgaWYgKGVuZCA8PSBzdG9wKSBzb3VuZC5z'
    b'cGxpY2UoaSwxKTsKICAgICAgICB9CiAgICAgICAgaWYgKCFmbG9hdHMpIHJldHVybiBudWxsOwogICAgICAgIGNvbnN0IGJ1ZmZl'
    b'ciA9IG5ldyBVaW50OEFycmF5KGZyYW1lU2FtcGxlcyoyKSwgcGNtID0gbmV3IERhdGFWaWV3KGJ1ZmZlci5idWZmZXIpOwogICAg'
    b'ICAgIGZvciAobGV0IGk9MDtpPGZsb2F0cy5sZW5ndGg7aSsrKSBwY20uc2V0SW50MTYoaSoyLE1hdGgucm91bmQoTWF0aC5tYXgo'
    b'LTEsTWF0aC5taW4oMSxmbG9hdHNbaV0pKSozMjc2NyksdHJ1ZSk7CiAgICAgICAgcmV0dXJuIGJ1ZmZlci50b0Jhc2U2NCgpOwog'
    b'ICAgfQogICAgZnVuY3Rpb24gY29tcG9zaXRlKGF0KSB7CiAgICAgICAgY29uc3QgY2FtZXJhcyA9IFtdLCBvd25lciA9IGFyY2hp'
    b'dmU/LnNjcmVlbk93bmVyOwogICAgICAgIGxldCBzY3JlZW4sIHByZWZlcnJlZCA9IGZhbHNlLCBtYWluOwogICAgICAgIGZvciAo'
    b'Y29uc3QgaXRlbSBvZiB0cmFja3MudmFsdWVzKCkpIHsKICAgICAgICAgICAgaWYgKCFpdGVtLnZpZGVvIHx8IGl0ZW0ubXV0ZWQp'
    b'IGNvbnRpbnVlOwogICAgICAgICAgICBpZiAoaXRlbS5zb3VyY2UgPT09ICdjYW1lcmEnKSB7CiAgICAgICAgICAgICAgICBpZiAo'
    b'aXRlbS5pZGVudGl0eSA9PT0gdGVhY2hlcikgbWFpbiA9IGl0ZW0udmlkZW87CiAgICAgICAgICAgICAgICBjYW1lcmFzLnB1c2go'
    b'e3JvbGU6aXRlbS5pZGVudGl0eS5zcGxpdCgnLScsMSlbMF0sdmlkZW86aXRlbS52aWRlb30pOwogICAgICAgICAgICB9IGVsc2Ug'
    b'aWYgKGl0ZW0uc291cmNlID09PSAnc2NyZWVuX3NoYXJlJyAmJiBvd25lcj8uaWRlbnRpdHkgPT09IGl0ZW0uaWRlbnRpdHkgJiYg'
    b'b3duZXI/LnNpZCA9PT0gaXRlbS5zaWQgJiYgIXByZWZlcnJlZCkgewogICAgICAgICAgICAgICAgc2NyZWVuID0gaXRlbS52aWRl'
    b'bzsKICAgICAgICAgICAgICAgIHByZWZlcnJlZCA9IGFyY2hpdmU/LnNjcmVlbiA9PT0gaXRlbS5wdWI7CiAgICAgICAgICAgIH0K'
    b'ICAgICAgICB9CiAgICAgICAgY29uc3QgcmFuayA9IHt0ZWFjaGVyOjAsYWRtaW46MSxzdHVkZW50OjJ9OwogICAgICAgIGNhbWVy'
    b'YXMuc29ydCgoYSxiKSA9PiByYW5rW2Eucm9sZV0gLSByYW5rW2Iucm9sZV0pOwogICAgICAgIHBhaW50KHtwYXBlcixpbmssZG9j'
    b'LHZpZXc6Ym9hcmQuZnJhbWluZygpLGhhc0luazpib2FyZC5wb3NpdGlvbigpLmluaz4wLGxhc2VyLGNhbWVyYXMsc2NyZWVuLHRl'
    b'YWNoZXI6bWFpbiwKICAgICAgICAgICAgYXJjaGl2ZUJvYXJkOmFyY2hpdmU/LmJvYXJkID09PSB0cnVlLHByZWZlclNjcmVlbjpw'
    b'cmVmZXJyZWQsaGVhZGluZyxhdH0pOwogICAgfQogICAgYXN5bmMgZnVuY3Rpb24gcmVhZHkoKSB7CiAgICAgICAgaWYgKCF3aW5k'
    b'b3cuQXVkaW9EZWNvZGVyIHx8ICF3aW5kb3cuVmlkZW9EZWNvZGVyIHx8IHR5cGVvZiBVaW50OEFycmF5LmZyb21CYXNlNjQgIT09'
    b'ICdmdW5jdGlvbicpIHJldHVybiBmYWxzZTsKICAgICAgICBmb250cyA/Pz0gUHJvbWlzZS5hbGwoWydBcmNoaXZlVmF6aXInLCdD'
    b'bGFzc3Jvb21QREZQZXJzaWFuJ10uZmxhdE1hcChmYW1pbHkgPT4gWzQwMCw3MDBdLm1hcChhc3luYyB3ZWlnaHQgPT4gewogICAg'
    b'ICAgICAgICBjb25zdCBsb2FkZWQgPSBhd2FpdCBkb2N1bWVudC5mb250cy5sb2FkKGAke3dlaWdodH0gMTZweCAke2ZhbWlseX1g'
    b'LCAn2q/ahuKAjNm+2pjaqduMJyk7CiAgICAgICAgICAgIHJldHVybiBsb2FkZWQuc29tZShmYWNlID0+IGZhY2UuZmFtaWx5ID09'
    b'PSBmYW1pbHkgJiYgZmFjZS5zdGF0dXMgPT09ICdsb2FkZWQnKTsKICAgICAgICB9KSkpOwogICAgICAgIHJldHVybiAoYXdhaXQg'
    b'Zm9udHMpLmV2ZXJ5KEJvb2xlYW4pICYmIChhd2FpdCBBdWRpb0RlY29kZXIuaXNDb25maWdTdXBwb3J0ZWQob3B1cykpLnN1cHBv'
    b'cnRlZAogICAgICAgICAgICAmJiAoYXdhaXQgVmlkZW9EZWNvZGVyLmlzQ29uZmlnU3VwcG9ydGVkKGNvbmZpZ3VyZSgndnA4Jykp'
    b'KS5zdXBwb3J0ZWQ7CiAgICB9CiAgICB3aW5kb3cucmVwbGF5ID0gewogICAgICAgIHJlYWR5LAogICAgICAgIGFzeW5jIHN0YXJ0'
    b'KG1ldGEpIHsKICAgICAgICAgICAgaWYgKCFhd2FpdCByZWFkeSgpKSB0aHJvdyBuZXcgRXJyb3IoJ3JlcGxheSBjb2RlY3Mgb3Ig'
    b'Zm9udHMgdW5hdmFpbGFibGUnKTsKICAgICAgICAgICAgc2NvcGUgPSB7bGVzc29uOm1ldGEubGVzc29uLGVwb2NoOm1ldGEuZXBv'
    b'Y2h9OyBoZWFkaW5nID0gbWV0YS50aXRsZTsgdGVhY2hlciA9IG1ldGEudGVhY2hlcjsgYXJjaGl2ZSA9IG1ldGEuYXJjaGl2ZTsK'
    b'ICAgICAgICAgICAgbmF0aXZlQ2xvY2sgPSBtZXRhLmNsb2NrID09PSAncnRjcC12MSc7CiAgICAgICAgICAgIGJvYXJkLnJlc2V0'
    b'KCk7CiAgICAgICAgfSwKICAgICAgICBhc3luYyBmcmFtZShpbmRleCwgdXBkYXRlcykgewogICAgICAgICAgICBjb25zdCBhdCA9'
    b'IGluZGV4KjEwMDAvMTI7CiAgICAgICAgICAgIGlmICh1cGRhdGVzLmxlbmd0aCkgYXdhaXQgbG9hZCh1cGRhdGVzKTsKICAgICAg'
    b'ICAgICAgd2hpbGUgKHF1ZXVlLmxlbmd0aCAmJiBxdWV1ZVswXVsyXSA8PSBhdCkgYXdhaXQgYXBwbHkocXVldWUuc2hpZnQoKSk7'
    b'CiAgICAgICAgICAgIGlmIChmYWlsdXJlKSB0aHJvdyBuZXcgRXJyb3IoZmFpbHVyZSk7CiAgICAgICAgICAgIGNvbnN0IGxpdCA9'
    b'IGxhc2VyLnNvbWUoKFssICwgdW50aWxdKSA9PiB1bnRpbCA+IGF0KTsKICAgICAgICAgICAgbGV0IHBuZyA9IG51bGw7CiAgICAg'
    b'ICAgICAgIGlmIChkaXJ0eSB8fCBsaXQgfHwgaGFkTGFzZXIpIHsKICAgICAgICAgICAgICAgIGNvbXBvc2l0ZShhdCk7CiAgICAg'
    b'ICAgICAgICAgICBwbmcgPSBjYW52YXMudG9EYXRhVVJMKCdpbWFnZS9wbmcnKS5zbGljZSgnZGF0YTppbWFnZS9wbmc7YmFzZTY0'
    b'LCcubGVuZ3RoKTsKICAgICAgICAgICAgICAgIGRpcnR5ID0gZmFsc2U7CiAgICAgICAgICAgIH0KICAgICAgICAgICAgaGFkTGFz'
    b'ZXIgPSBsaXQ7CiAgICAgICAgICAgIHJldHVybiB7cG5nLHBjbTptaXhlZChpbmRleCl9OwogICAgICAgIH0sCiAgICB9Owp9KSgp'
    b'Owo='
)
FIXED = (
    b'KCgpID0+IHsKICAgICd1c2Ugc3RyaWN0JzsKICAgIC8vIFZpcnR1YWwgY2xvY2s6IGNvbXBvc2luZyB0aGUgZnJhbWUgZm9yIHRp'
    b'bWUgVCBkb2VzIG5vdCB3YWl0IGZvciB3YWxsIHRpbWUgVC4KICAgIC8vIEZGbXBlZyByZWNlaXZlcyBsb3NzbGVzcyBQTkcgcGl4'
    b'ZWxzICsgUENNOyBILjI2NC9BQUMgaXMgdGhlIG9ubHkgZmlsbSBlbmNvZGUuCiAgICBjb25zdCBlbCA9IGlkID0+IGRvY3VtZW50'
    b'LmdldEVsZW1lbnRCeUlkKGlkKTsKICAgIGNvbnN0IGNhbnZhcyA9IGVsKCdmaWxtJyksIHBhcGVyID0gZWwoJ2JvYXJkLXBhcGVy'
    b'JyksIGluayA9IGVsKCdib2FyZC1pbmsnKTsKICAgIGNvbnN0IHBhaW50ID0gd2luZG93LkFyY2hpdmVGcmFtZShjYW52YXMpOwog'
    b'ICAgY29uc3QgZnJhbWVTYW1wbGVzID0gNDAwMDsgLy8gNDgwMDAgSHogLyAxMiBmcHMKICAgIGNvbnN0IG9wdXMgPSB7Y29kZWM6'
    b'J29wdXMnLHNhbXBsZVJhdGU6NDgwMDAsbnVtYmVyT2ZDaGFubmVsczoyfTsKICAgIGNvbnN0IHZpZGVvQ29kZWNzID0geyd2aWRl'
    b'by92cDgnOid2cDgnLCd2aWRlby9oMjY0JzonYXZjMS40MkUwMUYnfTsKICAgIGNvbnN0IHRyYWNrcyA9IG5ldyBNYXAoKSwgYXVk'
    b'aW8gPSBuZXcgTWFwKCksIHF1ZXVlID0gW10sIHNvdW5kID0gW107CiAgICBsZXQgc2NvcGUsIGhlYWRpbmcsIHRlYWNoZXIsIGFy'
    b'Y2hpdmUsIGRvYywgbmF0aXZlQ2xvY2ssIGxhc2VyID0gW10sIGhhZExhc2VyID0gZmFsc2UsIGRpcnR5ID0gdHJ1ZSwgZmFpbHVy'
    b'ZSwgZm9udHM7CiAgICBjb25zdCBib2FyZCA9IHdpbmRvdy5DbGFzc3Jvb21Cb2FyZChlbCgnY2FwdHVyZS1ib2FyZCcpLCB7CiAg'
    b'ICAgICAgcmFzdGVyT25seTp0cnVlLGNhbkVkaXQ6KCkgPT4gZmFsc2UsaWRlbnRpdHk6KCkgPT4gbnVsbCxhdXRob3JOYW1lOigp'
    b'ID0+ICcnLG5vdGljZTooKSA9PiB7IGZhaWx1cmUgPSAnYXJjaGl2ZWQgYm9hcmQgdW5hdmFpbGFibGUnOyB9LAogICAgfSk7CiAg'
    b'ICBjb25zdCBjb25maWd1cmUgPSBjb2RlYyA9PiAoe2NvZGVjLG9wdGltaXplRm9yTGF0ZW5jeTp0cnVlLGhhcmR3YXJlQWNjZWxl'
    b'cmF0aW9uOidwcmVmZXItc29mdHdhcmUnfSk7CiAgICBmdW5jdGlvbiBmYWlsKCkgeyBmYWlsdXJlID0gJ2FyY2hpdmVkIG1lZGlh'
    b'IGRlY29kZSBmYWlsZWQnOyB9CiAgICBmdW5jdGlvbiBjbGVhclZpZGVvKGl0ZW0pIHsKICAgICAgICBpZiAoaXRlbS5kZWNvZGVy'
    b'ICYmIGl0ZW0uZGVjb2Rlci5zdGF0ZSAhPT0gJ2Nsb3NlZCcpIGl0ZW0uZGVjb2Rlci5jbG9zZSgpOwogICAgICAgIGl0ZW0uZGVj'
    b'b2RlciA9IG51bGw7CiAgICAgICAgaXRlbS52aWRlbz8uY2xvc2UoKTsgaXRlbS52aWRlbyA9IG51bGw7CiAgICB9CiAgICBmdW5j'
    b'dGlvbiBkZWNvZGVWaWRlbyhpdGVtLCBhdCwgY29kZWMsIGtleSwgZW5jb2RlZCkgewogICAgICAgIGlmIChpdGVtLm11dGVkIHx8'
    b'ICghaXRlbS5kZWNvZGVyICYmICFrZXkpKSByZXR1cm47CiAgICAgICAgaWYgKGl0ZW0uY29kZWMgIT09IGNvZGVjIHx8ICFpdGVt'
    b'LmRlY29kZXIpIHsKICAgICAgICAgICAgY2xlYXJWaWRlbyhpdGVtKTsKICAgICAgICAgICAgaWYgKCFrZXkgfHwgIXZpZGVvQ29k'
    b'ZWNzW2NvZGVjXSkgdGhyb3cgbmV3IEVycm9yKCdpbnZhbGlkIHZpZGVvIGNvbmZpZ3VyYXRpb24nKTsKICAgICAgICAgICAgaXRl'
    b'bS5jb2RlYyA9IGNvZGVjOwogICAgICAgICAgICBpdGVtLmRlY29kZXIgPSBuZXcgVmlkZW9EZWNvZGVyKHsKICAgICAgICAgICAg'
    b'ICAgIG91dHB1dChmcmFtZSkgewogICAgICAgICAgICAgICAgICAgIGl0ZW0udmlkZW8/LmNsb3NlKCk7IGl0ZW0udmlkZW8gPSBm'
    b'cmFtZTsKICAgICAgICAgICAgICAgICAgICBkaXJ0eSA9IHRydWU7CiAgICAgICAgICAgICAgICAgICAgaXRlbS5kZWNvZGVkPy4o'
    b'KTsKICAgICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICBlcnJvcigpIHsgZmFpbCgpOyBpdGVtLmRlY29kZWQ/LigpOyB9'
    b'LAogICAgICAgICAgICB9KTsKICAgICAgICAgICAgaXRlbS5kZWNvZGVyLmNvbmZpZ3VyZShjb25maWd1cmUodmlkZW9Db2RlY3Nb'
    b'Y29kZWNdKSk7CiAgICAgICAgfQogICAgICAgIGNvbnN0IGNodW5rID0gbmV3IEVuY29kZWRWaWRlb0NodW5rKHt0eXBlOmtleSA/'
    b'ICdrZXknIDogJ2RlbHRhJyx0aW1lc3RhbXA6YXQqMTAwMCxkYXRhOlVpbnQ4QXJyYXkuZnJvbUJhc2U2NChlbmNvZGVkKX0pOwog'
    b'ICAgICAgIHJldHVybiBuZXcgUHJvbWlzZSgocmVzb2x2ZSxyZWplY3QpID0+IHsKICAgICAgICAgICAgLy8gQ2xhc3Nyb29tIHB1'
    b'YmxpY2F0aW9ucyB1c2UgcmVhbC10aW1lLCBub24tcmVvcmRlcmVkIHBpY3R1cmVzLiBGYWlsIGNsb3NlZCBpZiBhCiAgICAgICAg'
    b'ICAgIC8vIG1hbGZvcm1lZCBhcmNoaXZlIG5ldmVyIHByb2R1Y2VzIGEgZnJhbWU7IGRvbid0IHNpbGVudGx5IHNlbmQgYSBibGFu'
    b'ayBjYW1lcmEuCiAgICAgICAgICAgIGNvbnN0IHRpbWVyID0gc2V0VGltZW91dCgoKSA9PiByZWplY3QobmV3IEVycm9yKCd2aWRl'
    b'byBkZWNvZGVyIGRpZCBub3QgcHJvZHVjZSBhIGZyYW1lJykpLDE4MDApOwogICAgICAgICAgICBpdGVtLmRlY29kZWQgPSAoKSA9'
    b'PiB7IGNsZWFyVGltZW91dCh0aW1lcik7IHJlc29sdmUoKTsgfTsKICAgICAgICAgICAgdHJ5IHsgaXRlbS5kZWNvZGVyLmRlY29k'
    b'ZShjaHVuayk7IH0KICAgICAgICAgICAgY2F0Y2ggKGVycm9yKSB7IGNsZWFyVGltZW91dCh0aW1lcik7IHJlamVjdChlcnJvcik7'
    b'IH0KICAgICAgICB9KTsKICAgIH0KICAgIGZ1bmN0aW9uIHBsYWNlU291bmQoaXRlbSwgYXQsIHRzLCBzYW1wbGVzKSB7CiAgICAg'
    b'ICAgbGV0IHBvcyA9IGF0KjQ4LCBzbGV3ID0gMDsKICAgICAgICBpZiAoaXRlbS50cyAhPT0gbnVsbCkgewogICAgICAgICAgICBj'
    b'b25zdCBwcmVkaWN0ZWQgPSBpdGVtLnBvcyArICgodHMtaXRlbS50cyl8MCk7CiAgICAgICAgICAgIGNvbnN0IGRyaWZ0ID0gcG9z'
    b'LXByZWRpY3RlZDsKICAgICAgICAgICAgaWYgKE1hdGguYWJzKGRyaWZ0KSA8PSAobmF0aXZlQ2xvY2sgPyA1NzYwIDogMTIwMDAp'
    b'KSB7CiAgICAgICAgICAgICAgICBwb3MgPSBwcmVkaWN0ZWQ7CiAgICAgICAgICAgICAgICBpZiAobmF0aXZlQ2xvY2sgJiYgc2Ft'
    b'cGxlcy5sZW5ndGggPj0gMTIwICYmIE1hdGguYWJzKGRyaWZ0KSA+IDQ4MCkgewogICAgICAgICAgICAgICAgICAgIHNsZXcgPSBN'
    b'YXRoLnNpZ24oZHJpZnQpKk1hdGgubWF4KDEsTWF0aC5yb3VuZChzYW1wbGVzLmxlbmd0aC8zMjApKTsKICAgICAgICAgICAgICAg'
    b'IH0KICAgICAgICAgICAgfQogICAgICAgIH0KICAgICAgICBpdGVtLnRzID0gdHM7CiAgICAgICAgaXRlbS5wb3MgPSBwb3Mrc2xl'
    b'dzsKICAgICAgICBpZiAoc2xldykgewogICAgICAgICAgICAvLyBTbGV3IHRoZSBkdXJhdGlvbiwgbm90IHRoZSBzdGFydDogc2hp'
    b'ZnRpbmcgY29uc2VjdXRpdmUgcGFja2V0cwogICAgICAgICAgICAvLyBsZWF2ZXMgaG9sZXMgb3Igc3VtcyB0aGVtIGF0IGEgYm91'
    b'bmRhcnksIGJvdGggYXVkaWJsZSBhcyBjbGlja3MuCiAgICAgICAgICAgIC8vIEtlZXAgdGhlIGRlY29kZWQgZW5kcG9pbnRzIHNv'
    b'IHRoaXMgc21hbGwgY29ycmVjdGlvbiBqb2lucyBzbW9vdGhseS4KICAgICAgICAgICAgY29uc3QgYWRqdXN0ZWQgPSBuZXcgRmxv'
    b'YXQzMkFycmF5KHNhbXBsZXMubGVuZ3RoK3NsZXcpOwogICAgICAgICAgICBjb25zdCBsYXN0ID0gYWRqdXN0ZWQubGVuZ3RoLTEs'
    b'IHNjYWxlID0gKHNhbXBsZXMubGVuZ3RoLTEpL2xhc3Q7CiAgICAgICAgICAgIGZvciAobGV0IGk9MDtpPGxhc3Q7aSsrKSB7CiAg'
    b'ICAgICAgICAgICAgICBjb25zdCBvZmZzZXQgPSBpKnNjYWxlLCBsZWZ0ID0gTWF0aC5mbG9vcihvZmZzZXQpOwogICAgICAgICAg'
    b'ICAgICAgYWRqdXN0ZWRbaV0gPSBzYW1wbGVzW2xlZnRdICsgKHNhbXBsZXNbbGVmdCsxXS1zYW1wbGVzW2xlZnRdKSoob2Zmc2V0'
    b'LWxlZnQpOwogICAgICAgICAgICB9CiAgICAgICAgICAgIGFkanVzdGVkW2xhc3RdID0gc2FtcGxlc1tzYW1wbGVzLmxlbmd0aC0x'
    b'XTsKICAgICAgICAgICAgc2FtcGxlcyA9IGFkanVzdGVkOwogICAgICAgIH0KICAgICAgICBzb3VuZC5wdXNoKFtwb3Msc2FtcGxl'
    b'c10pOwogICAgfQogICAgZnVuY3Rpb24gb25Tb3VuZChpdGVtLCBmcmFtZSkgewogICAgICAgIHRyeSB7CiAgICAgICAgICAgIGNv'
    b'bnN0IG4gPSBmcmFtZS5udW1iZXJPZkZyYW1lcywgdGltaW5nID0gaXRlbS50aW1pbmdbaXRlbS5kZWNvZGVkKytdOwogICAgICAg'
    b'ICAgICAvLyBSYXcgT3B1cyB5aWVsZHMgb25lIG9yZGVyZWQgUENNIGJsb2NrIHBlciBwYWNrZXQuIENocm9taXVtIG1heQogICAg'
    b'ICAgICAgICAvLyBzeW50aGVzaXplIGNvbnRpZ3VvdXMgZGVjb2RlciB0aW1lc3RhbXBzLCBldmVuIGFjcm9zcyBhbiBSVFAgZ2Fw'
    b'LgogICAgICAgICAgICBpZiAoIXRpbWluZyB8fCBmcmFtZS5zYW1wbGVSYXRlICE9PSA0ODAwMCB8fCAhbiB8fCBuID4gNTc2MCB8'
    b'fCBzb3VuZC5sZW5ndGggPj0gMTIwMDApIHsgZmFpbCgpOyByZXR1cm47IH0KICAgICAgICAgICAgY29uc3Qgc2FtcGxlcyA9IG5l'
    b'dyBGbG9hdDMyQXJyYXkobiksIHBsYW5lID0gbmV3IEZsb2F0MzJBcnJheShuKTsKICAgICAgICAgICAgZm9yIChsZXQgY2g9MDsg'
    b'Y2g8ZnJhbWUubnVtYmVyT2ZDaGFubmVsczsgY2grKykgewogICAgICAgICAgICAgICAgZnJhbWUuY29weVRvKHBsYW5lLHtwbGFu'
    b'ZUluZGV4OmNoLGZvcm1hdDonZjMyLXBsYW5hcid9KTsKICAgICAgICAgICAgICAgIGZvciAobGV0IGk9MDtpPG47aSsrKSBzYW1w'
    b'bGVzW2ldICs9IHBsYW5lW2ldL2ZyYW1lLm51bWJlck9mQ2hhbm5lbHM7CiAgICAgICAgICAgIH0KICAgICAgICAgICAgcGxhY2VT'
    b'b3VuZChpdGVtLC4uLnRpbWluZyxzYW1wbGVzKTsKICAgICAgICB9IGNhdGNoIChfKSB7IGZhaWwoKTsgfQogICAgICAgIGZpbmFs'
    b'bHkgewogICAgICAgICAgICBmcmFtZS5jbG9zZSgpOwogICAgICAgICAgICBpZiAoZmFpbHVyZSB8fCBpdGVtLmRlY29kZWQgPT09'
    b'IGl0ZW0udGltaW5nLmxlbmd0aCkgaXRlbS5zZXR0bGU/LigpOwogICAgICAgIH0KICAgIH0KICAgIGFzeW5jIGZ1bmN0aW9uIHdh'
    b'aXRBdWRpbyhpdGVtKSB7CiAgICAgICAgLy8gRG8gbm90IGZsdXNoIHBlciB2aWRlbyBmcmFtZTogdGhhdCByZXNldHMgT3B1cyBw'
    b'cmVkaWN0aW9uIGFuZAogICAgICAgIC8vIG1ha2VzIHRoZSByZXN1bHRpbmcgc291bmQgZGVwZW5kIG9uIGhvdyB0aGUgcmVhZGVy'
    b'IGJhdGNoZWQgUlRQLgogICAgICAgIGlmICghZmFpbHVyZSAmJiBpdGVtLmRlY29kZWQgPCBpdGVtLnRpbWluZy5sZW5ndGgpIGF3'
    b'YWl0IG5ldyBQcm9taXNlKHJlc29sdmUgPT4gewogICAgICAgICAgICBjb25zdCB0aW1lciA9IHNldFRpbWVvdXQoKCkgPT4geyBm'
    b'YWlsKCk7IGl0ZW0uc2V0dGxlPy4oKTsgfSwgODAwMCk7CiAgICAgICAgICAgIGl0ZW0uc2V0dGxlID0gKCkgPT4geyBjbGVhclRp'
    b'bWVvdXQodGltZXIpOyBpdGVtLnNldHRsZSA9IG51bGw7IHJlc29sdmUoKTsgfTsKICAgICAgICB9KTsKICAgICAgICBpZiAoZmFp'
    b'bHVyZSB8fCBpdGVtLmRlY29kZWQgIT09IGl0ZW0udGltaW5nLmxlbmd0aCkgdGhyb3cgbmV3IEVycm9yKCdhcmNoaXZlZCBhdWRp'
    b'byBkZWNvZGUgaW5jb21wbGV0ZScpOwogICAgICAgIGl0ZW0udGltaW5nLmxlbmd0aCA9IGl0ZW0uZGVjb2RlZCA9IDA7CiAgICB9'
    b'CiAgICBmdW5jdGlvbiBkZWNvZGVBdWRpbyhpZCwgYXQsIHRzLCBlbmNvZGVkKSB7CiAgICAgICAgaWYgKCFOdW1iZXIuaXNTYWZl'
    b'SW50ZWdlcihhdCkgfHwgIU51bWJlci5pc1NhZmVJbnRlZ2VyKHRzKSkgdGhyb3cgbmV3IEVycm9yKCdpbnZhbGlkIGFyY2hpdmVk'
    b'IGF1ZGlvIHRpbWUnKTsKICAgICAgICBsZXQgaXRlbSA9IGF1ZGlvLmdldChpZCk7CiAgICAgICAgaWYgKCFpdGVtKSB7CiAgICAg'
    b'ICAgICAgIGl0ZW0gPSB7dHM6bnVsbCxwb3M6MCx0aW1pbmc6W10sZGVjb2RlZDowLHNldHRsZTpudWxsfTsKICAgICAgICAgICAg'
    b'aXRlbS5kZWNvZGVyID0gbmV3IEF1ZGlvRGVjb2Rlcih7b3V0cHV0OmZyYW1lID0+IG9uU291bmQoaXRlbSxmcmFtZSksZXJyb3I6'
    b'KCkgPT4geyBmYWlsKCk7IGl0ZW0uc2V0dGxlPy4oKTsgfX0pOwogICAgICAgICAgICBpdGVtLmRlY29kZXIuY29uZmlndXJlKG9w'
    b'dXMpOwogICAgICAgICAgICBhdWRpby5zZXQoaWQsaXRlbSk7CiAgICAgICAgfQogICAgICAgIGl0ZW0udGltaW5nLnB1c2goW2F0'
    b'LHRzXSk7CiAgICAgICAgaXRlbS5kZWNvZGVyLmRlY29kZShuZXcgRW5jb2RlZEF1ZGlvQ2h1bmsoe3R5cGU6J2tleScsdGltZXN0'
    b'YW1wOmF0KjEwMDAsZGF0YTpVaW50OEFycmF5LmZyb21CYXNlNjQoZW5jb2RlZCl9KSk7CiAgICAgICAgcmV0dXJuIGl0ZW07CiAg'
    b'ICB9CiAgICBhc3luYyBmdW5jdGlvbiBsb2FkKHVwZGF0ZXMpIHsKICAgICAgICBjb25zdCBwZW5kaW5nID0gbmV3IFNldCgpLCBl'
    b'bmRlZCA9IFtdOwogICAgICAgIGZvciAoY29uc3QgdXBkYXRlIG9mIHVwZGF0ZXMpIHsKICAgICAgICAgICAgY29uc3QgW3R5cGUs'
    b'aWQsYXQsY29kZWMsdHMsLGJ5dGVzXSA9IHVwZGF0ZTsKICAgICAgICAgICAgaWYgKHR5cGUgPT09ICdtZWRpYScgJiYgY29kZWMg'
    b'PT09ICdhdWRpby9vcHVzJykgcGVuZGluZy5hZGQoZGVjb2RlQXVkaW8oaWQsYXQsdHMsYnl0ZXMpKTsKICAgICAgICAgICAgZWxz'
    b'ZSBxdWV1ZS5wdXNoKHVwZGF0ZSk7CiAgICAgICAgICAgIGlmICh0eXBlID09PSAnZW5kJykgZW5kZWQucHVzaChpZCk7CiAgICAg'
    b'ICAgfQogICAgICAgIGF3YWl0IFByb21pc2UuYWxsKEFycmF5LmZyb20ocGVuZGluZywgd2FpdEF1ZGlvKSk7CiAgICAgICAgZm9y'
    b'IChjb25zdCBpZCBvZiBlbmRlZCkgewogICAgICAgICAgICBjb25zdCBpdGVtID0gYXVkaW8uZ2V0KGlkKTsKICAgICAgICAgICAg'
    b'aWYgKGl0ZW0/LmRlY29kZXIuc3RhdGUgPT09ICdjb25maWd1cmVkJykgaXRlbS5kZWNvZGVyLmNsb3NlKCk7CiAgICAgICAgICAg'
    b'IGF1ZGlvLmRlbGV0ZShpZCk7CiAgICAgICAgfQogICAgICAgIHF1ZXVlLnNvcnQoKGEsYikgPT4gYVsyXS1iWzJdKTsKICAgIH0K'
    b'ICAgIGFzeW5jIGZ1bmN0aW9uIGFwcGx5KFt0eXBlLGlkLGF0LC4uLmFyZ3NdKSB7CiAgICAgICAgaWYgKHR5cGUgPT09ICdib2Fy'
    b'ZCcpIHsKICAgICAgICAgICAgY29uc3QgW25leHQsbGF5b3V0LHJlcXVlc3RlZEF0XSA9IGFyZ3M7CiAgICAgICAgICAgIGFyY2hp'
    b'dmUgPSBsYXlvdXQ7CiAgICAgICAgICAgIGlmICgnZG9jJyBpbiBuZXh0KSBkb2MgPSBuZXh0LmRvYzsKICAgICAgICAgICAgbGFz'
    b'ZXIgPSAobmV4dC5sYXNlcj8ucG9pbnRzIHx8IFtdKS5tYXAoKFt4LHksbGVmdF0pID0+IFt4LHkscmVxdWVzdGVkQXQrbGVmdF0p'
    b'OwogICAgICAgICAgICBhd2FpdCBib2FyZC5zeW5jKHsuLi5uZXh0LGxhc2VyOm51bGx9LHNjb3BlKTsKICAgICAgICAgICAgZGly'
    b'dHkgPSB0cnVlOwogICAgICAgIH0gZWxzZSBpZiAodHlwZSA9PT0gJ29wZW4nKSB7CiAgICAgICAgICAgIGNvbnN0IFtzb3VyY2Us'
    b'aWRlbnRpdHksc2lkLHB1YixtdXRlZF0gPSBhcmdzOwogICAgICAgICAgICBpZiAoc291cmNlICE9PSAnbWljcm9waG9uZScpIHRy'
    b'YWNrcy5zZXQoaWQse3NvdXJjZSxpZGVudGl0eSxzaWQscHViLG11dGVkfSk7CiAgICAgICAgfSBlbHNlIGlmICh0eXBlID09PSAn'
    b'bXV0ZScgfHwgdHlwZSA9PT0gJ2VuZCcpIHsKICAgICAgICAgICAgY29uc3QgaXRlbSA9IHRyYWNrcy5nZXQoaWQpOwogICAgICAg'
    b'ICAgICBpZiAoaXRlbSkgewogICAgICAgICAgICAgICAgY2xlYXJWaWRlbyhpdGVtKTsKICAgICAgICAgICAgICAgIGl0ZW0ubXV0'
    b'ZWQgPSBhcmdzWzBdOwogICAgICAgICAgICAgICAgaWYgKHR5cGUgPT09ICdlbmQnKSB0cmFja3MuZGVsZXRlKGlkKTsKICAgICAg'
    b'ICAgICAgICAgIGRpcnR5ID0gdHJ1ZTsKICAgICAgICAgICAgfQogICAgICAgIH0gZWxzZSBpZiAodHlwZSA9PT0gJ21lZGlhJykg'
    b'ewogICAgICAgICAgICBjb25zdCBpdGVtID0gdHJhY2tzLmdldChpZCk7CiAgICAgICAgICAgIGlmIChpdGVtKSBhd2FpdCBkZWNv'
    b'ZGVWaWRlbyhpdGVtLGF0LGFyZ3NbMF0sYXJnc1syXSxhcmdzWzNdKTsKICAgICAgICB9IGVsc2UgdGhyb3cgbmV3IEVycm9yKCd1'
    b'bmtub3duIHNvdXJjZSBldmVudCcpOwogICAgfQogICAgZnVuY3Rpb24gbWl4ZWQoaW5kZXgpIHsKICAgICAgICBjb25zdCBzdGFy'
    b'dCA9IGluZGV4KmZyYW1lU2FtcGxlcywgc3RvcCA9IHN0YXJ0K2ZyYW1lU2FtcGxlczsKICAgICAgICBsZXQgZmxvYXRzOwogICAg'
    b'ICAgIGZvciAobGV0IGk9c291bmQubGVuZ3RoLTE7IGk+PTA7IGktLSkgewogICAgICAgICAgICBjb25zdCBbYXQsc2FtcGxlc10g'
    b'PSBzb3VuZFtpXTsKICAgICAgICAgICAgY29uc3QgZW5kID0gYXQrc2FtcGxlcy5sZW5ndGg7CiAgICAgICAgICAgIGlmIChhdCA8'
    b'IHN0b3AgJiYgZW5kID4gc3RhcnQpIHsKICAgICAgICAgICAgICAgIGZsb2F0cyA/Pz0gbmV3IEZsb2F0MzJBcnJheShmcmFtZVNh'
    b'bXBsZXMpOwogICAgICAgICAgICAgICAgZm9yIChsZXQgcG9zPU1hdGgubWF4KGF0LHN0YXJ0KTsgcG9zPE1hdGgubWluKGVuZCxz'
    b'dG9wKTsgcG9zKyspIGZsb2F0c1twb3Mtc3RhcnRdICs9IHNhbXBsZXNbcG9zLWF0XTsKICAgICAgICAgICAgfQogICAgICAgICAg'
    b'ICBpZiAoZW5kIDw9IHN0b3ApIHNvdW5kLnNwbGljZShpLDEpOwogICAgICAgIH0KICAgICAgICBpZiAoIWZsb2F0cykgcmV0dXJu'
    b'IG51bGw7CiAgICAgICAgY29uc3QgYnVmZmVyID0gbmV3IFVpbnQ4QXJyYXkoZnJhbWVTYW1wbGVzKjIpLCBwY20gPSBuZXcgRGF0'
    b'YVZpZXcoYnVmZmVyLmJ1ZmZlcik7CiAgICAgICAgZm9yIChsZXQgaT0wO2k8ZmxvYXRzLmxlbmd0aDtpKyspIHBjbS5zZXRJbnQx'
    b'NihpKjIsTWF0aC5yb3VuZChNYXRoLm1heCgtMSxNYXRoLm1pbigxLGZsb2F0c1tpXSkpKjMyNzY3KSx0cnVlKTsKICAgICAgICBy'
    b'ZXR1cm4gYnVmZmVyLnRvQmFzZTY0KCk7CiAgICB9CiAgICBmdW5jdGlvbiBjb21wb3NpdGUoYXQpIHsKICAgICAgICBjb25zdCBj'
    b'YW1lcmFzID0gW10sIG93bmVyID0gYXJjaGl2ZT8uc2NyZWVuT3duZXI7CiAgICAgICAgbGV0IHNjcmVlbiwgcHJlZmVycmVkID0g'
    b'ZmFsc2UsIG1haW47CiAgICAgICAgZm9yIChjb25zdCBpdGVtIG9mIHRyYWNrcy52YWx1ZXMoKSkgewogICAgICAgICAgICBpZiAo'
    b'IWl0ZW0udmlkZW8gfHwgaXRlbS5tdXRlZCkgY29udGludWU7CiAgICAgICAgICAgIGlmIChpdGVtLnNvdXJjZSA9PT0gJ2NhbWVy'
    b'YScpIHsKICAgICAgICAgICAgICAgIGlmIChpdGVtLmlkZW50aXR5ID09PSB0ZWFjaGVyKSBtYWluID0gaXRlbS52aWRlbzsKICAg'
    b'ICAgICAgICAgICAgIGNhbWVyYXMucHVzaCh7cm9sZTppdGVtLmlkZW50aXR5LnNwbGl0KCctJywxKVswXSx2aWRlbzppdGVtLnZp'
    b'ZGVvfSk7CiAgICAgICAgICAgIH0gZWxzZSBpZiAoaXRlbS5zb3VyY2UgPT09ICdzY3JlZW5fc2hhcmUnICYmIG93bmVyPy5pZGVu'
    b'dGl0eSA9PT0gaXRlbS5pZGVudGl0eSAmJiBvd25lcj8uc2lkID09PSBpdGVtLnNpZCAmJiAhcHJlZmVycmVkKSB7CiAgICAgICAg'
    b'ICAgICAgICBzY3JlZW4gPSBpdGVtLnZpZGVvOwogICAgICAgICAgICAgICAgcHJlZmVycmVkID0gYXJjaGl2ZT8uc2NyZWVuID09'
    b'PSBpdGVtLnB1YjsKICAgICAgICAgICAgfQogICAgICAgIH0KICAgICAgICBjb25zdCByYW5rID0ge3RlYWNoZXI6MCxhZG1pbjox'
    b'LHN0dWRlbnQ6Mn07CiAgICAgICAgY2FtZXJhcy5zb3J0KChhLGIpID0+IHJhbmtbYS5yb2xlXSAtIHJhbmtbYi5yb2xlXSk7CiAg'
    b'ICAgICAgcGFpbnQoe3BhcGVyLGluayxkb2Msdmlldzpib2FyZC5mcmFtaW5nKCksaGFzSW5rOmJvYXJkLnBvc2l0aW9uKCkuaW5r'
    b'PjAsbGFzZXIsY2FtZXJhcyxzY3JlZW4sdGVhY2hlcjptYWluLAogICAgICAgICAgICBhcmNoaXZlQm9hcmQ6YXJjaGl2ZT8uYm9h'
    b'cmQgPT09IHRydWUscHJlZmVyU2NyZWVuOnByZWZlcnJlZCxoZWFkaW5nLGF0fSk7CiAgICB9CiAgICBhc3luYyBmdW5jdGlvbiBy'
    b'ZWFkeSgpIHsKICAgICAgICBpZiAoIXdpbmRvdy5BdWRpb0RlY29kZXIgfHwgIXdpbmRvdy5WaWRlb0RlY29kZXIgfHwgdHlwZW9m'
    b'IFVpbnQ4QXJyYXkuZnJvbUJhc2U2NCAhPT0gJ2Z1bmN0aW9uJykgcmV0dXJuIGZhbHNlOwogICAgICAgIGZvbnRzID8/PSBQcm9t'
    b'aXNlLmFsbChbJ0FyY2hpdmVWYXppcicsJ0NsYXNzcm9vbVBERlBlcnNpYW4nXS5mbGF0TWFwKGZhbWlseSA9PiBbNDAwLDcwMF0u'
    b'bWFwKGFzeW5jIHdlaWdodCA9PiB7CiAgICAgICAgICAgIGNvbnN0IGxvYWRlZCA9IGF3YWl0IGRvY3VtZW50LmZvbnRzLmxvYWQo'
    b'YCR7d2VpZ2h0fSAxNnB4ICR7ZmFtaWx5fWAsICfar9qG4oCM2b7amNqp24wnKTsKICAgICAgICAgICAgcmV0dXJuIGxvYWRlZC5z'
    b'b21lKGZhY2UgPT4gZmFjZS5mYW1pbHkgPT09IGZhbWlseSAmJiBmYWNlLnN0YXR1cyA9PT0gJ2xvYWRlZCcpOwogICAgICAgIH0p'
    b'KSk7CiAgICAgICAgcmV0dXJuIChhd2FpdCBmb250cykuZXZlcnkoQm9vbGVhbikgJiYgKGF3YWl0IEF1ZGlvRGVjb2Rlci5pc0Nv'
    b'bmZpZ1N1cHBvcnRlZChvcHVzKSkuc3VwcG9ydGVkCiAgICAgICAgICAgICYmIChhd2FpdCBWaWRlb0RlY29kZXIuaXNDb25maWdT'
    b'dXBwb3J0ZWQoY29uZmlndXJlKCd2cDgnKSkpLnN1cHBvcnRlZDsKICAgIH0KICAgIHdpbmRvdy5yZXBsYXkgPSB7CiAgICAgICAg'
    b'cmVhZHksCiAgICAgICAgYXN5bmMgc3RhcnQobWV0YSkgewogICAgICAgICAgICBpZiAoIWF3YWl0IHJlYWR5KCkpIHRocm93IG5l'
    b'dyBFcnJvcigncmVwbGF5IGNvZGVjcyBvciBmb250cyB1bmF2YWlsYWJsZScpOwogICAgICAgICAgICBzY29wZSA9IHtsZXNzb246'
    b'bWV0YS5sZXNzb24sZXBvY2g6bWV0YS5lcG9jaH07IGhlYWRpbmcgPSBtZXRhLnRpdGxlOyB0ZWFjaGVyID0gbWV0YS50ZWFjaGVy'
    b'OyBhcmNoaXZlID0gbWV0YS5hcmNoaXZlOwogICAgICAgICAgICBuYXRpdmVDbG9jayA9IG1ldGEuY2xvY2sgPT09ICdydGNwLXYx'
    b'JzsKICAgICAgICAgICAgYm9hcmQucmVzZXQoKTsKICAgICAgICB9LAogICAgICAgIGFzeW5jIGZyYW1lKGluZGV4LCB1cGRhdGVz'
    b'KSB7CiAgICAgICAgICAgIGNvbnN0IGF0ID0gaW5kZXgqMTAwMC8xMjsKICAgICAgICAgICAgaWYgKHVwZGF0ZXMubGVuZ3RoKSBh'
    b'd2FpdCBsb2FkKHVwZGF0ZXMpOwogICAgICAgICAgICB3aGlsZSAocXVldWUubGVuZ3RoICYmIHF1ZXVlWzBdWzJdIDw9IGF0KSBh'
    b'd2FpdCBhcHBseShxdWV1ZS5zaGlmdCgpKTsKICAgICAgICAgICAgaWYgKGZhaWx1cmUpIHRocm93IG5ldyBFcnJvcihmYWlsdXJl'
    b'KTsKICAgICAgICAgICAgY29uc3QgbGl0ID0gbGFzZXIuc29tZSgoWywgLCB1bnRpbF0pID0+IHVudGlsID4gYXQpOwogICAgICAg'
    b'ICAgICBsZXQgcG5nID0gbnVsbDsKICAgICAgICAgICAgaWYgKGRpcnR5IHx8IGxpdCB8fCBoYWRMYXNlcikgewogICAgICAgICAg'
    b'ICAgICAgY29tcG9zaXRlKGF0KTsKICAgICAgICAgICAgICAgIHBuZyA9IGNhbnZhcy50b0RhdGFVUkwoJ2ltYWdlL3BuZycpLnNs'
    b'aWNlKCdkYXRhOmltYWdlL3BuZztiYXNlNjQsJy5sZW5ndGgpOwogICAgICAgICAgICAgICAgZGlydHkgPSBmYWxzZTsKICAgICAg'
    b'ICAgICAgfQogICAgICAgICAgICBoYWRMYXNlciA9IGxpdDsKICAgICAgICAgICAgcmV0dXJuIHtwbmcscGNtOm1peGVkKGluZGV4'
    b'KX07CiAgICAgICAgfSwKICAgIH07Cn0pKCk7Cg=='
)
HEX = re.compile(r'[a-f0-9]{32}')


class Refused(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Refused(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def directory(path, uid=0, *, private=False):
    st = path.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == uid and path.resolve() == path
         and not st.st_mode & (0o077 if private else 0o022), f'Unexpected directory: {path.name}.')


def file(path, *, uid=0, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as fp:
        st = os.fstat(fp.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_uid == uid and st.st_nlink == 1
             and not st.st_mode & (0o077 if private else 0o022) and 0 < st.st_size <= 128 * 1024,
             f'Unexpected installed file: {path.name}.')
        data = fp.read(128 * 1024 + 1)
        need(len(data) == st.st_size, f'File changed while reading: {path.name}.')
    return data, st


def verified_module(name, filename):
    path = APP / filename
    code, _ = file(path)
    need(digest(code) == HELPERS[filename], f'Expected the published VPS 1.3.2 bytes: {filename}.')
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(code, str(path), 'exec'), module.__dict__)
    return module


def helpers():
    directory(APP.parent)
    directory(APP)
    previous, saved_path = sys.modules.get('vps_media'), sys.path[:]
    sys.modules['vps_media'] = verified_module('vps_media', 'vps_media.py')
    try:
        return verified_module('_baci_vps_audio_repair', 'baci-vps.py')
    finally:
        sys.path[:] = saved_path
        if previous is None:
            sys.modules.pop('vps_media', None)
        else:
            sys.modules['vps_media'] = previous


def reviewed_payloads():
    stock, fixed = (base64.b64decode(payload, validate=True) for payload in (STOCK, FIXED))
    need(digest(stock) == STOCK_SHA and digest(fixed) == AFTER_SHA, 'Repair payload checksum mismatch.')
    return stock, fixed


@contextmanager
def installation_lock(path):
    directory(path)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Another Recorder maintenance process is running.') from None
        yield
    finally:
        os.close(fd)


def replace(path, original, wanted):
    # The program directory is root-owned; each reader sees one complete version.
    with tempfile.TemporaryDirectory(prefix='.audio-repair-', dir=path.parent) as stage:
        pending = Path(stage) / path.name
        with pending.open('xb') as fp:
            os.fchmod(fp.fileno(), 0o644)
            fp.write(wanted)
            fp.flush()
            os.fsync(fp.fileno())
        need(file(path)[0] == original, 'Installed replay changed concurrently. Nothing was overwritten.')
        os.replace(pending, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Repair:
    def __init__(self, vps, stock, fixed):
        self.vps, self.stock, self.fixed = vps, stock, fixed
        self.replay = vps.REC_PROGRAM / 'replay.js'
        for path in (vps.CFG, vps.REC_PROGRAM.parent, vps.REC_PROGRAM):
            directory(path)
        self.config = json.loads(file(vps.STATE, private=True)[0])
        need(self.config.get('version') == '1.3.2' and self.config.get('profile') in ('compact', 'full'),
             'This repair is only for managed VPS 1.3.2 with recording (compact or full).')
        self.compact = self.config['profile'] == 'compact'
        self.keys = json.loads(file(vps.PAIR, private=True)[0])
        need(self.keys.get('site') == self.config['school'], 'Installed school pairing does not match the current site.')
        self.uid = pwd.getpwnam('school-recorder').pw_uid
        need(self.uid > 0, 'Expected unprivileged Recorder account.')
        self.target = self.process()
        self.keepers = self.invocations()
        self.local_worker = self.worker()

    def worker(self):
        spool = self.vps.SPOOL
        directory(spool, self.uid, private=True)
        need(all(entry.name in ('worker.json', '.lock', '.cache') for entry in spool.iterdir()),
             'Local Recorder spool still has jobs. Wait for it to settle; do not delete any recordings.')
        identity = json.loads(file(spool / 'worker.json', uid=self.uid, private=True)[0]).get('id')
        need(isinstance(identity, str) and HEX.fullmatch(identity), 'Expected the current Recorder worker identity.')
        return identity

    def idle(self):
        vps = self.vps
        need(self.worker() == self.local_worker, 'Local Recorder identity changed.')
        try:
            rooms = vps.media.local_rooms(vps.media.pilot_deployment())
            need(rooms == [], 'LiveKit still has open rooms. Close all classes and practice rooms first.')
            status = vps.recorder_status(self.config, self.keys, self.local_worker)
        except (ValueError, OSError):
            raise Refused('Could not confirm the idle school and local LiveKit. No job will be interrupted.') from None
        need(all(status.get(name) is True for name in ('idle', 'worker_ok', 'native_ready')),
             'School still has a room or a pending job, or the Recorder is not ready.')

    def process(self):
        vps = self.vps
        props = vps.run('systemctl', 'show', '--no-pager',
                        '--property=FragmentPath,DropInPaths,NeedDaemonReload,SubState,User,MainPID,InvocationID',
                        vps.RENDERER, output=True, timeout=8).decode()
        state = dict(row.split('=', 1) for row in props.splitlines() if '=' in row)
        unit = vps.SYSTEMD / vps.RENDERER
        paths = [unit.with_name(unit.name + '.d') / '70-renderer-r1.conf']
        if self.compact:
            paths.append(paths[0].with_name('80-compact.conf'))
        pid, inv = state.get('MainPID', ''), state.get('InvocationID', '')
        need(state.get('FragmentPath') == str(unit) and set(state.get('DropInPaths', '').split()) == set(map(str, paths))
             and state.get('NeedDaemonReload') == 'no' and state.get('SubState') == 'running'
             and state.get('User') == 'school-recorder' and re.fullmatch(r'[1-9][0-9]{0,9}', pid)
             and int(pid) > 1 and HEX.fullmatch(inv) and int(inv, 16),
             'Expected a running managed r1 renderer without custom systemd overrides.')
        return int(pid), inv

    def invocations(self):
        vps = self.vps
        result = tuple(vps.info('-p', 'InvocationID', unit) for unit in (*vps.LIVE_UNITS, vps.RECORDER))
        need(all(HEX.fullmatch(value) and int(value, 16) for value in result), 'Expected running LiveKit and Recorder services.')
        return result

    def proc_file(self, name):
        # Intentional use of /proc/PID/root from the validated live unit. Refuse
        # an overlaid replay file instead of changing a path the renderer cannot see.
        return file(Path('/proc') / str(self.target[0]) / 'root' / self.replay.with_name(name).relative_to('/'))

    def visible(self, expected):
        need(self.process() == self.target and self.invocations() == self.keepers,
             'A service changed independently during the repair.')
        raw, host = file(self.replay)
        guest, view = self.proc_file('replay.js')
        need(raw == guest == expected and (host.st_dev, host.st_ino) == (view.st_dev, view.st_ino),
             'The running renderer does not see these replay bytes. Nothing will be mounted or restarted.')
        vps = self.vps
        unit = vps.SYSTEMD / vps.RENDERER
        need(digest(file(unit)[0]) == UNIT_SHA, 'Expected the published renderer unit.')
        r1 = self.replay.parent.with_name('0.4.23-renderer-r1')
        drop = unit.with_name(unit.name + '.d')
        binds = '[Service]\n' + ''.join(f'BindReadOnlyPaths={r1 / name}:{self.replay.with_name(name)}\n'
                                       for name in ('movie.py', 'postprocess.py'))
        need(file(drop / '70-renderer-r1.conf')[0] == binds.encode(), 'Expected the published r1 bind mounts.')
        assets = RENDERER_ASSETS | ({'compact-renderer.py': COMPACT_SHA} if self.compact else {})
        need(all(digest(self.proc_file(name)[0]) == pin for name, pin in assets.items()),
             'Expected the published r1 renderer (reloads replay.js for each new film).')

    def probe(self, test=False):
        path = self.vps.RENDER_SOCKET
        directory(path.parent, self.uid, private=True)
        st = path.lstat()
        need(stat.S_ISSOCK(st.st_mode) and st.st_uid == self.uid and not st.st_mode & 0o077,
             'Expected the renderer private socket.')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(22 if test else 3)
                conn.connect(str(path))
                pid, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                need(pid == self.target[0] and uid == self.uid, 'Renderer socket does not belong to the running unit.')
                action = 'self-test-mp4' if test else 'ready'
                conn.sendall((json.dumps({'probe': action}) + '\n').encode('ascii'))
                with conn.makefile('rb') as fp:
                    result = json.loads(fp.readline(512))
        except (ValueError, OSError):
            raise Refused('Could not complete the private renderer check.') from None
        answer = 'baci-render/1 tested mp4' if test else 'baci-render/1'
        need(result == {'result': answer}, 'Renderer failed the sandboxed MP4 check or is busy.')

    def change(self, before, after):
        self.idle()
        self.visible(before)
        try:
            replace(self.replay, before, after)
            self.visible(after)
            self.probe(test=True)
            self.idle()
            self.visible(after)
        except (Exception, KeyboardInterrupt) as error:
            try:
                actual, _ = file(self.replay)
                if actual == after:
                    replace(self.replay, after, before)
                else:
                    need(actual == before, 'Installed file changed independently.')
                restored, _ = file(self.replay)
                need(restored == before, 'Prior replay bytes could not be verified.')
            except (Exception, KeyboardInterrupt):
                raise Refused('Repair unconfirmed; automatic rollback could not be verified. No recording files were changed.') from None
            problem = str(error) if isinstance(error, Refused) else type(error).__name__
            raise Refused(f'{problem} Previous replay file restored or unchanged. No recording files were changed.') from None


def run(vps, mode, payloads):
    try:
        vps.system_preflight()
        with installation_lock(vps.REC):
            repair = Repair(vps, *payloads)
            before, _ = file(repair.replay)
            need(before in payloads, 'Installed replay.js is neither the reviewed original nor r2. Nothing was replaced.')
            repair.idle()
            repair.visible(before)
            repair.probe()
            current = 'r2' if before == repair.fixed else 'stock'
            print(f'audio_preflight=ok installed={current} rooms_and_queue=idle recorder=ready', flush=True)
            if mode == 'check-only':
                print('check_only=yes no_changes=yes')
                return
            after = repair.fixed if mode == 'apply' else repair.stock
            if before == after:
                print(f'audio_repair=ok installed={current} already_selected=yes no_changes=yes')
                return
            repair.change(before, after)
            print(f"audio_repair=ok installed={'r2' if mode == 'apply' else 'stock'} mp4_check=ok services=unchanged")
    except vps.Refused as error:
        raise Refused(str(error)) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ('check-only', 'apply', 'rollback'):
        group.add_argument('--' + mode, dest='mode', action='store_const', const=mode)
    args = parser.parse_args()
    if os.geteuid() != 0 or not sys.flags.isolated:
        parser.error('Run the SHA-256 verified script with sudo /usr/bin/python3 -IB.')
    os.umask(0o077)
    sys.dont_write_bytecode = True

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        payloads = reviewed_payloads()
        run(helpers(), args.mode, payloads)
    except Refused as error:
        print(f'Audio repair refused: {error}', file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt) as error:
        print(f'Audio repair unconfirmed ({type(error).__name__}); no private data is shown.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
