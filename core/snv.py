"""Parsing e busca sobre a malha SNV (DNIT) nacional."""

import xml.etree.ElementTree as ET
import zipfile

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from core.geometry import NS, join_trechos, parse_coords, to_utm_line, to_utm_point, utm_epsg_for_lonlat


def _iter_eixo_principal_placemarks(filepath):
    with zipfile.ZipFile(filepath, "r") as z:
        kml_name = next(n for n in z.namelist() if n.endswith(".kml"))
        with z.open(kml_name) as f:
            root = ET.parse(f).getroot()

    for pm in root.findall(".//kml:Placemark", NS):
        attrs = {sd.get("name"): (sd.text or "").strip()
                 for sd in pm.findall(".//kml:SimpleData", NS)}
        if attrs.get("nm_tipo_tr") != "Eixo Principal":
            continue
        yield pm, attrs


def list_brs_in_kmz(filepath) -> list:
    """Lista as BRs distintas presentes no KMZ, sem montar geometria (leve)."""
    brs = set()
    for _pm, attrs in _iter_eixo_principal_placemarks(filepath):
        try:
            brs.add(str(int(attrs.get("vl_br", "0"))))
        except ValueError:
            continue
    return sorted(brs, key=lambda b: (len(b), b))


def parse_kmz_snv(filepath, brs_filtro=None):
    """
    Carrega trechos 'Eixo Principal' do KMZ nacional SNV.
    brs_filtro: set de strings de BR para restringir o carregamento (mais rápido).
    """
    segments = []
    for pm, attrs in _iter_eixo_principal_placemarks(filepath):
        try:
            br = str(int(attrs.get("vl_br", "0")))  # "010" -> "10"
        except ValueError:
            continue

        if brs_filtro and br not in brs_filtro:
            continue

        uf     = attrs.get("sg_uf", "").strip()
        km_ini = float(attrs.get("vl_km_inic") or 0)
        km_fim = float(attrs.get("vl_km_fina") or 0)
        codigo = attrs.get("vl_codigo", "").strip()

        coords_el = pm.find(".//kml:coordinates", NS)
        if coords_el is None:
            continue
        pts = parse_coords(coords_el)
        if len(pts) < 2:
            continue

        segments.append({
            "br":            br,
            "uf":            uf,
            "km_ini":        km_ini,
            "km_fim":        km_fim,
            "codigo":        codigo,
            "coords_lonlat": pts,
        })

    return segments


def build_snv_eixo(segments):
    """
    Une os trechos SNV por (BR, UF) em uma polilinha contínua.
    Retorna {"{br}/{uf}": {br, uf, coords_lonlat}}.
    """
    by_key = {}
    for s in segments:
        key = f"{s['br']}/{s['uf']}"
        by_key.setdefault(key, {"br": s["br"], "uf": s["uf"], "trechos": []})
        by_key[key]["trechos"].append(s["coords_lonlat"])

    result = {}
    for key, info in by_key.items():
        merged = join_trechos(info["trechos"])
        if merged:
            result[key] = {
                "br":            info["br"],
                "uf":            info["uf"],
                "coords_lonlat": merged,
            }
    return result


def load_snv(kmz_path, brs_filtro=None):
    """Retorna (segments, tree, snv_eixo). tree é None se não houver segmentos."""
    segs = parse_kmz_snv(kmz_path, brs_filtro)
    # Índice espacial em lon/lat (graus) — usado só para achar o segmento
    # CANDIDATO mais próximo (busca aproximada). A distância/posição exatas
    # são recalculadas depois, reprojetando apenas o segmento vencedor na
    # zona UTM correta para aquele ponto (ver nearest_snv_segment).
    tree = STRtree([LineString(s["coords_lonlat"]) for s in segs]) if segs else None
    eixo = build_snv_eixo(segs)
    return segs, tree, eixo


def nearest_snv_segment(lon, lat, tree, segments, *, epsg=None, seg_utm_cache=None):
    """
    Acha o segmento SNV mais próximo do ponto via STRtree (busca aproximada
    em lon/lat) e recalcula a posição exata reprojetando só o segmento
    vencedor para a zona UTM apropriada ao ponto (resolvida automaticamente
    a partir da longitude, a menos que `epsg` seja informado explicitamente).

    `seg_utm_cache`: dict opcional {(idx, epsg): LineString} para reaproveitar
    a reprojeção entre pontos consecutivos que caem no mesmo segmento/zona.

    Retorna {"seg", "seg_line_utm", "epsg", "proj_d", "seg_len", "dist_m"}
    ou None se não houver malha carregada.
    """
    if tree is None or not segments:
        return None

    idx = tree.nearest(Point(lon, lat))
    seg = segments[idx]
    if epsg is None:
        epsg = utm_epsg_for_lonlat(lon, lat)

    cache_key = (idx, epsg)
    if seg_utm_cache is not None and cache_key in seg_utm_cache:
        seg_line_utm = seg_utm_cache[cache_key]
    else:
        seg_line_utm = to_utm_line(seg["coords_lonlat"], epsg)
        if seg_utm_cache is not None:
            seg_utm_cache[cache_key] = seg_line_utm

    click_utm = Point(*to_utm_point(lon, lat, epsg))
    proj_d  = seg_line_utm.project(click_utm)
    seg_len = seg_line_utm.length
    dist_m  = seg_line_utm.distance(click_utm)
    return {
        "seg": seg, "seg_line_utm": seg_line_utm, "epsg": epsg,
        "proj_d": proj_d, "seg_len": seg_len, "dist_m": dist_m,
    }


def resolve_br_uf_for_point(lon, lat, tree, segments):
    """Wrapper fino sobre nearest_snv_segment: devolve só (br, uf), ou (None, None)."""
    nearest = nearest_snv_segment(lon, lat, tree, segments)
    if nearest is None:
        return None, None
    return nearest["seg"]["br"], nearest["seg"]["uf"]
