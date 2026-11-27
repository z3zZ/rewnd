"""The "scheduled" exposure mode: profiles with time ranges, a night fallback, sunrise/sunset, easing.

Values are stops, the same unit exposure.ExposureControl uses on every platform: the shutter is open
for 2**value seconds, so -6 is 1/64 s and every +1 doubles the light. A profile therefore means the
same thing on Windows and Linux, and easing between two values in stops looks even to the eye.

Everything here is pure logic with no camera or Qt, so it can be tested on its own. The capture
engine owns a Schedule and feeds its value to the camera; see CaptureEngine._update_schedule.
"""

import copy
import importlib.resources
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

MIN_STOPS, MAX_STOPS = -14.0, 1.0
NIGHT_KEY = "night"
DEFAULT_DAY = -7.0
DEFAULT_NIGHT = -4.0


# ---------- values and times ----------

def clamp_stops(v):
    return max(MIN_STOPS, min(MAX_STOPS, float(v)))


def shutter_text(stops):
    """-6 -> '1/64 s', 0 -> '1 s'."""
    secs = 2.0 ** float(stops)
    if secs >= 0.95:
        return f"{secs:.1f} s".replace(".0 s", " s")
    return f"1/{round(1 / secs)} s"


def fmt_stops(v):
    return f"{float(v):+.1f}".replace("-", "−")


def parse_time(s):
    """'8:00' / '08:00' -> minutes since midnight, or None."""
    m = re.fullmatch(r"\s*(\d{1,2})[:.h](\d{2})\s*", str(s or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def fmt_time(minutes):
    minutes = int(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clean(cfg):
    """A complete, well-typed copy of the exposure settings, whatever was saved."""
    from rewnd.config import DEFAULTS
    base = copy.deepcopy(DEFAULTS["exposure_schedule"])
    cfg = cfg or {}
    out = copy.deepcopy(base)
    out["mode"] = cfg.get("mode") if cfg.get("mode") in ("fixed", "sun") else "fixed"
    try:
        out["ease_minutes"] = max(0.0, min(240.0, float(cfg.get("ease_minutes", base["ease_minutes"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["night_value"] = clamp_stops(cfg.get("night_value", base["night_value"]))
    except (TypeError, ValueError):
        pass
    profiles = []
    for i, p in enumerate(cfg.get("profiles", base["profiles"]) or []):
        if not isinstance(p, dict):
            continue
        try:
            value = clamp_stops(p.get("value", DEFAULT_DAY))
        except (TypeError, ValueError):
            value = DEFAULT_DAY
        start = parse_time(p.get("start"))
        end = parse_time(p.get("end"))
        profiles.append({
            "name": str(p.get("name") or "").strip() or f"profile {i + 1}",
            "start": fmt_time(start if start is not None else 8 * 60),
            "end": fmt_time(end if end is not None else 16 * 60),
            "value": value,
        })
    out["profiles"] = profiles
    try:
        out["sun_profile"] = max(0, int(cfg.get("sun_profile", 0)))
    except (TypeError, ValueError):
        out["sun_profile"] = 0
    loc = dict(base["location"])
    loc.update({k: v for k, v in (cfg.get("location") or {}).items() if k in loc})
    for k in ("lat", "lon"):
        try:
            loc[k] = None if loc[k] is None else float(loc[k])
        except (TypeError, ValueError):
            loc[k] = None
    if loc["lat"] is not None and not (-90 <= loc["lat"] <= 90):
        loc["lat"] = None
    if loc["lon"] is not None and not (-180 <= loc["lon"] <= 180):
        loc["lon"] = None
    out["location"] = loc
    return out


# ---------- the sun (NOAA solar calculator) ----------

def _julian_day(dt_utc):
    return dt_utc.timestamp() / 86400.0 + 2440587.5


def _sun_geometry(jd):
    """Declination (degrees) and equation of time (minutes) for a Julian day."""
    jc = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * jc) + math.sin(3 * mr) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * jc)
    app_long = l0 + c - 0.00569 - 0.00478 * math.sin(omega)
    obliq = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    obliq += 0.00256 * math.cos(omega)
    decl = math.degrees(math.asin(math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))
    y = math.tan(math.radians(obliq / 2)) ** 2
    l0r = math.radians(l0)
    eq_time = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
                               + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                               - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    return decl, eq_time


def sun_times(lat, lon, day):
    """Sunrise and sunset for a UTC calendar day as Unix timestamps.

    Returns (sunrise, sunset, polar): polar is None normally, or "day"/"night" when the sun doesn't
    cross the horizon that day (then sunrise and sunset are None).
    """
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

    def event(sign, guess_min):
        minutes = guess_min
        for _ in range(2):  # refine once at the event time itself
            decl, eq = _sun_geometry(_julian_day(midnight + timedelta(minutes=minutes)))
            lat_r, decl_r = math.radians(lat), math.radians(decl)
            cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl_r))
                      - math.tan(lat_r) * math.tan(decl_r))
            if cos_ha > 1:
                return None, "night"
            if cos_ha < -1:
                return None, "day"
            ha = math.degrees(math.acos(cos_ha))
            minutes = 720 - 4 * lon - eq + sign * 4 * ha
        return midnight.timestamp() + minutes * 60, None

    rise, p1 = event(-1, 720 - 4 * lon - 360)
    sset, p2 = event(+1, 720 - 4 * lon + 360)
    polar = p1 or p2
    if polar:
        return None, None, polar
    return rise, sset, None


def sun_events(lat, lon, ts, days_before=1, days_after=2):
    """Sorted [(timestamp, 'rise'|'set')] around ts, plus the polar state of ts's own day."""
    d0 = datetime.fromtimestamp(ts, timezone.utc).date()
    events, polar_today = [], None
    for k in range(-days_before, days_after + 1):
        rise, sset, polar = sun_times(lat, lon, d0 + timedelta(days=k))
        if k == 0:
            polar_today = polar
        if rise is not None:
            events.append((rise, "rise"))
        if sset is not None:
            events.append((sset, "set"))
    events.sort()
    return events, polar_today


# ---------- which profile applies ----------

@dataclass
class Slot:
    key: str      # "p0", "p1", … or "night": identity, so a renamed profile isn't a switch
    name: str
    value: float


@dataclass
class Plan:
    slot: Slot
    next_at: float = None     # when the active profile next changes (timestamp), if known
    next_slot: Slot = None
    sunrise: float = None     # today's, local date, in sun mode
    sunset: float = None
    note: str = ""


def _night(cfg):
    return Slot(NIGHT_KEY, "night", float(cfg["night_value"]))


def _profile(cfg, i):
    p = cfg["profiles"][i]
    return Slot(f"p{i}", p["name"], float(p["value"]))


def fixed_slot_at(cfg, ts):
    """First profile in the list whose range contains ts (local time), else night."""
    dt = datetime.fromtimestamp(ts)
    m = dt.hour * 60 + dt.minute + dt.second / 60.0
    for i, p in enumerate(cfg["profiles"]):
        s, e = parse_time(p["start"]), parse_time(p["end"])
        if s is None or e is None or s == e:
            continue
        inside = (s <= m < e) if s < e else (m >= s or m < e)  # s > e wraps past midnight
        if inside:
            return _profile(cfg, i)
    return _night(cfg)


def _fixed_plan(cfg, ts):
    current = fixed_slot_at(cfg, ts)
    today = datetime.fromtimestamp(ts).date()
    edges = set()
    for p in cfg["profiles"]:
        for key in ("start", "end"):
            mins = parse_time(p[key])
            if mins is None:
                continue
            for k in range(3):
                d = today + timedelta(days=k)
                t = datetime(d.year, d.month, d.day, mins // 60, mins % 60).timestamp()
                if t > ts:
                    edges.add(t)
    for t in sorted(edges):
        s = fixed_slot_at(cfg, t)
        if s.key != current.key:
            return Plan(current, t, s)
    return Plan(current)


def _sun_plan(cfg, ts):
    loc = cfg["location"]
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        plan = _fixed_plan(cfg, ts)
        plan.note = "no location set, so fixed times are used"
        return plan
    i = cfg["sun_profile"]
    day = _profile(cfg, i) if i < len(cfg["profiles"]) else _night(cfg)
    night = _night(cfg)
    events, polar = sun_events(lat, lon, ts)
    before = [e for e in events if e[0] <= ts]
    after = [e for e in events if e[0] > ts]
    if before:
        is_day = before[-1][1] == "rise"
    elif after:
        is_day = after[0][1] == "set"
    else:
        is_day = polar == "day"
    plan = Plan(day if is_day else night)
    if after and day.key != night.key:
        plan.next_at = after[0][0]
        plan.next_slot = day if after[0][1] == "rise" else night
    local_today = datetime.fromtimestamp(ts).date()
    for t, kind in events:
        if datetime.fromtimestamp(t).date() == local_today:
            if kind == "rise" and plan.sunrise is None:
                plan.sunrise = t
            elif kind == "set" and plan.sunset is None:
                plan.sunset = t
    if polar and not events:
        plan.note = "the sun doesn't rise today" if polar == "night" else "the sun doesn't set today"
    return plan


def plan_at(cfg, ts):
    return _sun_plan(cfg, ts) if cfg["mode"] == "sun" else _fixed_plan(cfg, ts)


# ---------- easing ----------

class Easer:
    """Moves a value towards its target over a set time, in stops, with an S-curve.

    A new target starts a new ramp from wherever the value is right now, so a switch that lands
    mid-ramp carries on smoothly instead of jumping.
    """

    def __init__(self):
        self.value = None
        self.target = None
        self._from = None
        self._t0 = 0.0
        self._dur = 0.0

    @property
    def easing(self):
        return self._dur > 0

    def ends_at(self):
        return self._t0 + self._dur if self._dur > 0 else None

    def update(self, target, now, duration, ease=True):
        if self.value is None or not ease or duration <= 0:
            self.value = self.target = target
            self._dur = 0.0
            return self.value
        if target != self.target:
            self._from, self.target = self.value, target
            self._t0, self._dur = now, float(duration)
        if self._dur > 0:
            p = (now - self._t0) / self._dur
            if p >= 1:
                self.value, self._dur = self.target, 0.0
            else:
                p = max(0.0, p)
                self.value = self._from + (self.target - self._from) * (p * p * (3 - 2 * p))
        else:
            self.value = self.target
        return self.value


class Schedule:
    """Plan + easing. The capture engine calls update() about once a second."""

    def __init__(self, cfg=None):
        self.cfg = clean(cfg)
        self.easer = Easer()
        self._slot = None
        self._from_name = None

    def configure(self, cfg):
        self.cfg = clean(cfg)

    def update(self, now, recording):
        """Exposure to use right now (stops), and everything the UI shows about it.

        Easing only matters for the video, so it runs while recording. When not recording,
        changes apply straight away so the preview shows the new setting immediately.
        """
        plan = plan_at(self.cfg, now)
        if self._slot is None or plan.slot.key != self._slot.key:
            self._from_name = self._slot.name if self._slot else None
        self._slot = plan.slot
        value = self.easer.update(plan.slot.value, now, self.cfg["ease_minutes"] * 60, ease=recording)
        return {
            "active": True,
            "mode": self.cfg["mode"],
            "name": plan.slot.name,
            "key": plan.slot.key,
            "target": plan.slot.value,
            "value": value,
            "easing": self.easer.easing,
            "ease_from": self._from_name if self.easer.easing else None,
            "ease_end": self.easer.ends_at(),
            "next_at": plan.next_at,
            "next_name": plan.next_slot.name if plan.next_slot else None,
            "next_value": plan.next_slot.value if plan.next_slot else None,
            "sunrise": plan.sunrise,
            "sunset": plan.sunset,
            "note": plan.note,
        }


# ---------- places ----------

# Big cities that aren't the namesake of a time zone. Time zone cities come from tzdata.
EXTRA_CITIES = {
    "Washington": (38.90, -77.04, "US"), "San Francisco": (37.77, -122.42, "US"),
    "Seattle": (47.61, -122.33, "US"), "Boston": (42.36, -71.06, "US"), "Miami": (25.76, -80.19, "US"),
    "Atlanta": (33.75, -84.39, "US"), "Dallas": (32.78, -96.80, "US"), "Houston": (29.76, -95.37, "US"),
    "San Diego": (32.72, -117.16, "US"), "Austin": (30.27, -97.74, "US"),
    "Montreal": (45.50, -73.57, "CA"), "Ottawa": (45.42, -75.70, "CA"), "Calgary": (51.05, -114.07, "CA"),
    "Manchester": (53.48, -2.24, "GB"), "Birmingham": (52.49, -1.89, "GB"), "Leeds": (53.80, -1.55, "GB"),
    "Edinburgh": (55.95, -3.19, "GB"), "Glasgow": (55.86, -4.25, "GB"), "Bristol": (51.45, -2.59, "GB"),
    "Cardiff": (51.48, -3.18, "GB"), "Lyon": (45.76, 4.84, "FR"), "Marseille": (43.30, 5.37, "FR"),
    "Munich": (48.14, 11.58, "DE"), "Hamburg": (53.55, 9.99, "DE"), "Frankfurt": (50.11, 8.68, "DE"),
    "Cologne": (50.94, 6.96, "DE"), "Milan": (45.46, 9.19, "IT"), "Barcelona": (41.39, 2.17, "ES"),
    "Osaka": (34.69, 135.50, "JP"), "Beijing": (39.90, 116.41, "CN"), "Delhi": (28.61, 77.21, "IN"),
    "New Delhi": (28.61, 77.21, "IN"), "Mumbai": (19.08, 72.88, "IN"), "Bangalore": (12.97, 77.59, "IN"),
    "Rio de Janeiro": (-22.91, -43.17, "BR"), "Cape Town": (-33.92, 18.42, "ZA"),
    "Tel Aviv": (32.09, 34.78, "IL"), "Rotterdam": (51.92, 4.48, "NL"),
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _tab_coord(s):
    """'+513030-0000731' -> (51.508, -0.125)."""
    m = re.fullmatch(r"([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?", s)
    if not m:
        return None
    lat = int(m.group(2)) + int(m.group(3)) / 60 + int(m.group(4) or 0) / 3600
    lon = int(m.group(6)) + int(m.group(7)) / 60 + int(m.group(8) or 0) / 3600
    return (-lat if m.group(1) == "-" else lat), (-lon if m.group(5) == "-" else lon)


@lru_cache(maxsize=1)
def cities():
    """{normalised name: (lat, lon, 'City, Country')} from tzdata, offline."""
    out = {}
    countries = {}
    try:
        root = importlib.resources.files("tzdata").joinpath("zoneinfo")
        for line in root.joinpath("iso3166.tab").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                code, name = line.split("\t", 1)
                countries[code] = name.strip()
        for line in root.joinpath("zone.tab").read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            ll = _tab_coord(parts[1])
            if ll is None:
                continue
            city = parts[2].rsplit("/", 1)[-1].replace("_", " ")
            out.setdefault(_norm(city), (ll[0], ll[1], f"{city}, {countries.get(parts[0], parts[0])}"))
    except Exception:
        pass
    for city, (lat, lon, cc) in EXTRA_CITIES.items():
        out.setdefault(_norm(city), (lat, lon, f"{city}, {countries.get(cc, cc)}"))
    return out


def nearest_city(lat, lon):
    """Name of the closest city in the offline list, for a friendly label."""
    best, best_d = None, None
    for c_lat, c_lon, label in cities().values():
        dlon = (c_lon - lon + 180) % 360 - 180
        d = (c_lat - lat) ** 2 + (dlon * math.cos(math.radians(lat))) ** 2
        if best_d is None or d < best_d:
            best, best_d = label, d
    return best


def parse_place(text):
    """A typed place -> (lat, lon, label), or None.

    Takes coordinates ('51.5, -0.12', '51.5N 0.12W') or a city name from an offline list.
    """
    text = (text or "").strip()
    if not text:
        return None
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*([NSns])?\s*[,; ]\s*([+-]?\d+(?:\.\d+)?)\s*°?\s*([EWew])?\s*",
                     text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(3))
        if (m.group(2) or "").lower() == "s":
            lat = -abs(lat)
        if (m.group(4) or "").lower() == "w":
            lon = -abs(lon)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon, f"{lat:.3f}, {lon:.3f}"
        return None
    table = cities()
    key = _norm(text.split(",")[0])
    if key in table:
        return table[key]
    starts = [v for k, v in table.items() if k.startswith(key)] if len(key) >= 3 else []
    if len(starts) == 1:
        return starts[0]
    return None
