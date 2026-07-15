"""Parsers de KML/KMZ enviados pelo usuário: marcos quilométricos e eixo."""

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from core.errors import CalcError
from core.geometry import NS, haversine_m, join_trechos, parse_coords
from core.snv import resolve_br_uf_for_point

_RE_NOME_MARCO = re.compile(r"BR-(\d+)\s+KM-(\d+)")
_RE_DESC_KM    = re.compile(r"km-(\d+)", re.IGNORECASE)


def kml_root_from_bytes(data: bytes):
    """Extrai o ET root de bytes de KML ou KMZ."""
    if data[:2] == b"PK":  # ZIP magic -> KMZ
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kml_name = next(n for n in z.namelist() if n.endswith(".kml"))
            data = z.read(kml_name)
    return ET.parse(io.BytesIO(data)).getroot()


def parse_marcos_from_root(root):
    """
    Formato (a): Placemarks Point com nome "BR-NNN KM-MMM".
    Retorna lista de {br, km_num, lon, lat}.
    """
    markers = []
    for pm in root.findall(".//kml:Placemark", NS):
        name_el = pm.find("kml:name", NS)
        if name_el is None:
            continue
        m = _RE_NOME_MARCO.match((name_el.text or "").strip())
        if not m:
            continue
        br, km_num = m.group(1), int(m.group(2))

        coords_el = pm.find(".//kml:coordinates", NS)
        if coords_el is None:
            continue
        p = coords_el.text.strip().split(",")
        if len(p) < 2:
            continue
        try:
            lon, lat = float(p[0]), float(p[1])
        except ValueError:
            continue

        markers.append({"br": br, "km_num": km_num, "lon": lon, "lat": lat})
    return markers


def parse_marcos_mq153_from_root(root, snv_tree, snv_segments):
    """
    Formato (b) — ex: Inputs/MQ_153.kml. Placemarks Point cujo nome não traz
    informação útil (ex: "kml_1"), mas com ExtendedData/SimpleData[@name='DESC']
    no padrão "km-NNN". A BR não vem no Placemark: é resolvida projetando o
    marco sobre a malha SNV e pegando a BR do segmento mais próximo.
    Retorna lista de {br, km_num, lon, lat}.
    """
    markers = []
    for pm in root.findall(".//kml:Placemark", NS):
        desc_el = pm.find(".//kml:SimpleData[@name='DESC']", NS)
        if desc_el is None or not desc_el.text:
            continue
        m = _RE_DESC_KM.search(desc_el.text.strip())
        if not m:
            continue
        km_num = int(m.group(1))

        coords_el = pm.find(".//kml:coordinates", NS)
        if coords_el is None:
            continue
        p = coords_el.text.strip().split(",")
        if len(p) < 2:
            continue
        try:
            lon, lat = float(p[0]), float(p[1])
        except ValueError:
            continue

        br, _uf = resolve_br_uf_for_point(lon, lat, snv_tree, snv_segments)
        if br is None:
            continue

        markers.append({"br": br, "km_num": km_num, "lon": lon, "lat": lat})
    return markers


def parse_marcos_kml(root, snv_tree, snv_segments):
    """Dispatch entre os formatos (a) e (b) de KML de marcos."""
    markers = parse_marcos_from_root(root)
    if not markers:
        markers = parse_marcos_mq153_from_root(root, snv_tree, snv_segments)
    if not markers:
        raise CalcError(
            "KML_SEM_MARCOS",
            "marcos_kml: nenhum marco válido — use Placemarks Point com nome "
            "'BR-NNN KM-MMM' ou com atributo DESC no padrão 'km-NNN'.",
        )
    return markers


def parse_eixo_from_root(root):
    """
    Extrai o eixo rodoviário de um ET root.
    Suporta:
      - MultiGeometry com muitas LineStrings encadeadas (ex: Google Earth KMZ)
      - LineStrings avulsas em Placemarks separados
      - Folders aninhados
    O eixo não precisa de atributos BR/UF — é usado apenas como geometria de percurso.
    Retorna lista de coordenadas [(lon, lat), ...] contínua (ou None se vazio).
    A projeção para UTM é feita depois, sob demanda, na zona certa para cada
    ponto consultado (o eixo pode atravessar mais de uma zona UTM).
    """
    placemark_lines = []

    for pm in root.findall(".//kml:Placemark", NS):
        pts = []
        for ls in pm.findall(".//kml:LineString", NS):
            coords_el = ls.find("kml:coordinates", NS)
            if coords_el is None:
                continue
            new_pts = parse_coords(coords_el)
            if not new_pts:
                continue
            if pts and haversine_m(pts[-1][0], pts[-1][1],
                                    new_pts[0][0], new_pts[0][1]) < 1.0:
                new_pts = new_pts[1:]
            pts.extend(new_pts)

        if len(pts) >= 2:
            placemark_lines.append(pts)

    if not placemark_lines:
        return None

    merged = join_trechos(placemark_lines)
    return merged if merged else None
