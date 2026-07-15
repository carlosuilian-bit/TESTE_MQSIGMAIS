"""Parsing e busca sobre a malha SNV (DNIT) nacional."""

import xml.etree.ElementTree as ET
import zipfile

from shapely.strtree import STRtree

from core.geometry import NS, join_trechos, parse_coords, to_utm_line


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
            "line_utm":      to_utm_line(pts),
        })

    return segments


def build_snv_eixo(segments):
    """
    Une os trechos SNV por (BR, UF) em uma polilinha contínua.
    Retorna {"{br}/{uf}": {br, uf, line_utm}}.
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
                "br":       info["br"],
                "uf":       info["uf"],
                "line_utm": to_utm_line(merged),
            }
    return result


def load_snv(kmz_path, brs_filtro=None):
    """Retorna (segments, tree, snv_eixo). tree é None se não houver segmentos."""
    segs = parse_kmz_snv(kmz_path, brs_filtro)
    tree = STRtree([s["line_utm"] for s in segs]) if segs else None
    eixo = build_snv_eixo(segs)
    return segs, tree, eixo


def nearest_snv_segment(pt_utm, tree, segments):
    """
    Acha o segmento SNV mais próximo do ponto (em UTM) via STRtree.
    Retorna {"seg", "proj_d", "seg_len", "dist_m"} ou None se não houver malha.
    """
    if tree is None or not segments:
        return None
    idx     = tree.nearest(pt_utm)
    seg     = segments[idx]
    proj_d  = seg["line_utm"].project(pt_utm)
    seg_len = seg["line_utm"].length
    dist_m  = seg["line_utm"].distance(pt_utm)
    return {"seg": seg, "proj_d": proj_d, "seg_len": seg_len, "dist_m": dist_m}


def resolve_br_uf_for_point(pt_utm, tree, segments):
    """Wrapper fino sobre nearest_snv_segment: devolve só (br, uf), ou (None, None)."""
    nearest = nearest_snv_segment(pt_utm, tree, segments)
    if nearest is None:
        return None, None
    return nearest["seg"]["br"], nearest["seg"]["uf"]
