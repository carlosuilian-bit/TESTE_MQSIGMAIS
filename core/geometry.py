"""Utilitários de geometria/coordenadas — sem dependência de framework web."""

import math
from functools import lru_cache

import pyproj
from shapely.geometry import LineString

NS = {"kml": "http://www.opengis.net/kml/2.2"}

DEFAULT_EPSG = 32722  # UTM Zona 22S — usado só como fallback (ex: chamadas avulsas/testes)


def utm_epsg_for_lonlat(lon, lat):
    """
    Resolve dinamicamente o EPSG UTM apropriado para uma coordenada.

    O Brasil se estende por 8 fusos UTM (18S a 25S, com uma pontinha de
    Roraima/Amapá no hemisfério norte). Uma única zona fixa distorce
    distâncias fora da sua faixa de ~6° de longitude — por isso cada ponto
    (e cada geometria local a ele) deve ser projetado na sua própria zona,
    não numa zona global fixa para o Brasil inteiro.
    """
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=None)
def _transformer(epsg_origem, epsg_destino):
    return pyproj.Transformer.from_crs(f"EPSG:{epsg_origem}", f"EPSG:{epsg_destino}", always_xy=True)


def to_utm_line(coords_lonlat, epsg=DEFAULT_EPSG):
    t = _transformer(4326, epsg)
    return LineString([t.transform(lon, lat) for lon, lat in coords_lonlat])


def to_utm_point(lon, lat, epsg=DEFAULT_EPSG):
    return _transformer(4326, epsg).transform(lon, lat)


def from_utm_point(x, y, epsg=DEFAULT_EPSG):
    return _transformer(epsg, 4326).transform(x, y)


def from_utm_line(line_utm, epsg=DEFAULT_EPSG):
    """Converte uma LineString UTM em lista de [lon, lat] (para plotagem em mapa)."""
    t = _transformer(epsg, 4326)
    return [list(t.transform(x, y)) for x, y in line_utm.coords]


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
