"""Parsing e busca sobre a malha SNV (DNIT) nacional."""

import re
import xml.etree.ElementTree as ET
import zipfile

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from core.geometry import NS, join_trechos, parse_coords, to_utm_line, to_utm_point, utm_epsg_for_lonlat

_TIPOS_SNV_CARREGADOS = {"Eixo Principal", "Variante"}
_RE_CODIGO_SNV        = re.compile(r"^(\d{3})([A-Z])([A-Z]{2})(\d+)$")


def _iter_trechos_snv_placemarks(filepath):
    with zipfile.ZipFile(filepath, "r") as z:
        kml_name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
        with z.open(kml_name) as f:
            root = ET.parse(f).getroot()

    for pm in root.findall(".//kml:Placemark", NS):
        attrs = {sd.get("name"): (sd.text or "").strip()
                 for sd in pm.findall(".//kml:SimpleData", NS)}
        if attrs.get("nm_tipo_tr") not in _TIPOS_SNV_CARREGADOS:
            continue
        yield pm, attrs


def _familia_rota_snv(codigo):
    m = _RE_CODIGO_SNV.match(codigo or "")
    if not m:
        return None
    sufixo = m.group(4)
    if not sufixo or sufixo[0] == "0":
        return None
    return sufixo[0]


def _eixo_key_for_segment(seg):
    base = f"{seg['br']}/{seg['uf']}"
    tipo_sigla = seg.get("tipo_sigla", "")
    if tipo_sigla in ("", "B"):
        return base

    familia = _familia_rota_snv(seg.get("codigo"))
    sufixo = f"{tipo_sigla}{familia}" if familia else tipo_sigla
    return f"{base}/{sufixo}"


def list_brs_in_kmz(filepath) -> list:
    """Lista as BRs distintas presentes no KMZ, sem montar geometria."""
    brs = set()
    for _pm, attrs in _iter_trechos_snv_placemarks(filepath):
        try:
            brs.add(str(int(attrs.get("vl_br", "0"))))
        except ValueError:
            continue
    return sorted(brs, key=lambda b: (len(b), b))


def parse_kmz_snv(filepath, brs_filtro=None):
    """
    Carrega trechos trafegaveis do KMZ nacional SNV.

    Inclui Eixo Principal e Variante. Acessos, contornos, aneis e travessias
    urbanas continuam fora para evitar contaminar a busca por rota principal.
    """
    segments = []
    for pm, attrs in _iter_trechos_snv_placemarks(filepath):
        try:
            br = str(int(attrs.get("vl_br", "0")))  # "010" -> "10"
        except ValueError:
            continue

        if brs_filtro and br not in brs_filtro:
            continue

        uf = attrs.get("sg_uf", "").strip()
        km_ini = float(attrs.get("vl_km_inic") or 0)
        km_fim = float(attrs.get("vl_km_fina") or 0)
        codigo = attrs.get("vl_codigo", "").strip()
        tipo = attrs.get("nm_tipo_tr", "").strip()
        tipo_sigla = attrs.get("sg_tipo_tr", "").strip()

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
            "tipo":          tipo,
            "tipo_sigla":    tipo_sigla,
            "coords_lonlat": pts,
        })

    return segments


def build_snv_eixo(segments):
    """
    Une os trechos SNV em eixos por rota.

    O eixo principal permanece na chave "{br}/{uf}". Variantes ficam em chaves
    separadas, como "40/RJ/V1", para evitar costurar subida e descida da serra
    em uma unica polilinha.
    """
    by_key = {}
    for s in segments:
        key = _eixo_key_for_segment(s)
        s["eixo_key"] = key
        by_key.setdefault(key, {
            "br": s["br"],
            "uf": s["uf"],
            "tipo": s.get("tipo", ""),
            "tipo_sigla": s.get("tipo_sigla", ""),
            "codigos": [],
            "trechos": [],
        })
        by_key[key]["codigos"].append(s["codigo"])
        by_key[key]["trechos"].append(s["coords_lonlat"])

    result = {}
    for key, info in by_key.items():
        merged = join_trechos(info["trechos"])
        if merged:
            result[key] = {
                "br":            info["br"],
                "uf":            info["uf"],
                "tipo":          info["tipo"],
                "tipo_sigla":    info["tipo_sigla"],
                "codigos":       info["codigos"],
                "coords_lonlat": merged,
            }
    return result


def load_snv(kmz_path, brs_filtro=None):
    """Retorna (segments, tree, snv_eixo). tree e None se nao houver segmentos."""
    segs = parse_kmz_snv(kmz_path, brs_filtro)
    eixo = build_snv_eixo(segs)
    # Indice espacial em lon/lat (graus) usado so para achar o segmento
    # candidato mais proximo. A distancia/posicao exatas sao recalculadas em UTM.
    tree = STRtree([LineString(s["coords_lonlat"]) for s in segs]) if segs else None
    return segs, tree, eixo


def nearest_snv_segment(lon, lat, tree, segments, *, epsg=None, seg_utm_cache=None):
    """
    Acha o segmento SNV mais proximo do ponto via STRtree e recalcula a
    posicao exata reprojetando apenas o segmento vencedor na zona UTM correta.
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
    """Wrapper fino sobre nearest_snv_segment: devolve so (br, uf), ou (None, None)."""
    nearest = nearest_snv_segment(lon, lat, tree, segments)
    if nearest is None:
        return None, None
    return nearest["seg"]["br"], nearest["seg"]["uf"]
