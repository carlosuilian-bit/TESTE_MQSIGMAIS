"""Utilitários de geometria/coordenadas — sem dependência de framework web."""

import math

import pyproj
from shapely.geometry import LineString

# UTM Zona 22S — GO, MG, SP, MS, MT (-42° a -54° lon)
_to_utm   = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32722", always_xy=True)
_from_utm = pyproj.Transformer.from_crs("EPSG:32722", "EPSG:4326", always_xy=True)
NS        = {"kml": "http://www.opengis.net/kml/2.2"}


def to_utm_line(coords_lonlat):
    return LineString([_to_utm.transform(lon, lat) for lon, lat in coords_lonlat])


def to_utm_point(lon, lat):
    x, y = _to_utm.transform(lon, lat)
    return x, y


def from_utm_point(x, y):
    return _from_utm.transform(x, y)


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians((lat2 - lat1) / 2)) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians((lon2 - lon1) / 2)) ** 2)
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def parse_coords(coords_el):
    pts = []
    for tok in coords_el.text.strip().split():
        p = tok.split(",")
        if len(p) >= 2:
            try:
                pts.append((float(p[0]), float(p[1])))
            except ValueError:
                pass
    return pts


def join_trechos(trechos_lonlat):
    """Greedy nearest-endpoint joining de N polilinhas (lon, lat)."""
    if not trechos_lonlat:
        return []
    remaining = [list(t) for t in trechos_lonlat]
    joined = remaining.pop(0)
    while remaining:
        last = joined[-1]
        bi, brev, bd = 0, False, float("inf")
        for i, t in enumerate(remaining):
            df = haversine_m(last[0], last[1], t[0][0], t[0][1])
            dr = haversine_m(last[0], last[1], t[-1][0], t[-1][1])
            d  = min(df, dr)
            if d < bd:
                bd, bi, brev = d, i, dr < df
        t = remaining.pop(bi)
        if brev:
            t = list(reversed(t))
        if haversine_m(joined[-1][0], joined[-1][1], t[0][0], t[0][1]) < 2.0:
            t = t[1:]
        joined.extend(t)
    return joined
