"""Ask the operating system where this computer is, for sunrise/sunset scheduling.

Windows: the Windows location service, through PowerShell's System.Device (respects the
Privacy & security > Location setting). Linux: GeoClue2 over D-Bus (needs the jeepney package and
a running geoclue service). Nothing is sent anywhere by rewnd itself; if neither works, the user
types a city or coordinates instead.
"""

import subprocess
import sys
import time

from rewnd.platform_utils import IS_WINDOWS, NO_WINDOW


class LocationUnavailable(Exception):
    pass


_PS_SCRIPT = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::Default)
$null = $w.TryStart($true, [TimeSpan]::FromSeconds(8))
$n = 0
while ($w.Status -ne 'Ready' -and $w.Permission -ne 'Denied' -and $n -lt 80) { Start-Sleep -Milliseconds 100; $n++ }
$c = $w.Position.Location
$inv = [Globalization.CultureInfo]::InvariantCulture
if ($w.Permission -eq 'Denied') { 'denied' }
elseif ($c.IsUnknown) { 'unknown' }
else { 'ok ' + $c.Latitude.ToString($inv) + ' ' + $c.Longitude.ToString($inv) }
$w.Stop()
"""


def _windows():
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=25,
                             creationflags=NO_WINDOW).stdout.strip()
    except Exception as e:
        raise LocationUnavailable(f"Couldn't ask Windows for the location ({e}).")
    last = out.splitlines()[-1] if out else ""
    if last.startswith("ok "):
        lat, lon = (float(x) for x in last.split()[1:3])
        return lat, lon
    if last == "denied":
        raise LocationUnavailable("Windows location is turned off for apps. Turn it on in Settings > "
                                  "Privacy & security > Location, or type a city.")
    raise LocationUnavailable("Windows doesn't know where this computer is right now. Type a city instead.")


def _linux(timeout):
    try:
        from jeepney import DBusAddress, MessageType, Properties, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except Exception:
        raise LocationUnavailable("System location needs the jeepney package. Type a city instead.")
    name = "org.freedesktop.GeoClue2"

    def call(conn, msg):
        reply = conn.send_and_get_reply(msg, timeout=5)
        if reply.header.message_type == MessageType.error:
            raise LocationUnavailable(f"GeoClue said no: {reply.body[0] if reply.body else 'error'}. "
                                      "Type a city instead.")
        return reply.body

    try:
        conn = open_dbus_connection(bus="SYSTEM")
    except Exception:
        raise LocationUnavailable("Couldn't reach the system bus for location. Type a city instead.")
    try:
        mgr = DBusAddress("/org/freedesktop/GeoClue2/Manager", bus_name=name,
                          interface="org.freedesktop.GeoClue2.Manager")
        try:
            (client_path,) = call(conn, new_method_call(mgr, "GetClient"))
        except LocationUnavailable:
            raise
        except Exception:
            raise LocationUnavailable("No location service (GeoClue) is running. Type a city instead.")
        client = DBusAddress(client_path, bus_name=name, interface="org.freedesktop.GeoClue2.Client")
        call(conn, Properties(client).set("DesktopId", "s", "rewnd"))
        call(conn, Properties(client).set("RequestedAccuracyLevel", "u", 4))  # city level is plenty
        call(conn, new_method_call(client, "Start"))
        end = time.time() + timeout
        while time.time() < end:
            (sig_val,) = call(conn, Properties(client).get("Location"))
            loc_path = sig_val[1] if isinstance(sig_val, tuple) else sig_val
            if loc_path and loc_path != "/":
                loc = DBusAddress(loc_path, bus_name=name, interface="org.freedesktop.GeoClue2.Location")
                lat = call(conn, Properties(loc).get("Latitude"))[0][1]
                lon = call(conn, Properties(loc).get("Longitude"))[0][1]
                try:
                    call(conn, new_method_call(client, "Stop"))
                except Exception:
                    pass
                return float(lat), float(lon)
            time.sleep(0.25)
        raise LocationUnavailable("The location service didn't answer in time. Type a city instead.")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def system_location(timeout=12.0):
    """(lat, lon) from the OS. Blocks for up to ~timeout seconds; run it off the UI thread."""
    import os
    if os.environ.get("REWND_FAKE_LOCATION"):
        lat, lon = os.environ["REWND_FAKE_LOCATION"].split(",")
        return float(lat), float(lon)
    if IS_WINDOWS:
        return _windows()
    if sys.platform.startswith("linux"):
        return _linux(timeout)
    raise LocationUnavailable("System location isn't supported here. Type a city instead.")
