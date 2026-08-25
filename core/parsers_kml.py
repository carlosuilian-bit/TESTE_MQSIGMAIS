"""Parsers de KML/KMZ enviados pelo usuario: marcos quilometricos e eixo."""

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from core.errors import CalcError
from core.geometry import NS, haversine_m, join_trechos, parse_coords
from core.snv import nearest_snv_segment

_KML_URI           = NS["kml"]
_KML_DOCUMENT_TAG  = f"{{{_KML_URI}}}Document"
_KML_FOLDER_TAG    = f"{{{_KML_URI}}}Folder"
_KML_PLACEMARK_TAG = f"{{{_KML_URI}}}Placemark"

_RE_NOME_MARCO = re.compile(r"\bBR[-_\s]*0*(\d{1,3})\b.*?\bKM[-_\s]*(\d+)\b", re.IGNORECASE)
_RE_DESC_KM    = re.compile(r"\bkm[-_\s]*(\d+)\b", re.IGNORECASE)
_RE_INTEIRO    = re.compile(r"^\d+$")
_RE_BR_HINT    = re.compile(r"(?:^|[^A-Z0-9])(?:BR|MQ)[-_\s]*0*(\d{1,3})(?=$|[^A-Z0-9])",
                             re.IGNORECASE)
_RE_VARIANTE_HINT  = re.compile(r"(?:^|[^A-Z0-9])(?:V|VARIANTE)(?=$|[^A-Z0-9])", re.IGNORECASE)
_RE_PRINCIPAL_HINT = re.compile(r"(?:^|[^A-Z0-9])(?:B|EP|PRINCIPAL)(?=$|[^A-Z0-9])", re.IGNORECASE)


def kml_root_from_bytes(data: bytes):
    """Extrai o ET root de bytes de KML ou KMZ."""
    if data[:2] == b"PK":  # ZIP magic -> KMZ
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kml_name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            data = z.read(kml_name)
    return ET.parse(io.BytesIO(data)).getroot()


def _normaliza_br(br_text: str) -> str:
    """Remove zeros a esquerda para casar com a BR carregada do SNV."""
    return str(int(br_text))


def _nome_el_text(el) -> str:
    name_el = el.find("kml:name", NS)
    return (name_el.text or "").strip() if name_el is not None else ""


def _br_hint_from_text(text: str):
    m = _RE_BR_HINT.search(text or "")
    return _normaliza_br(m.group(1)) if m else None


def _tipo_hint_from_text(text: str):
    if _RE_VARIANTE_HINT.search(text or ""):
        return "V"
    if _RE_PRINCIPAL_HINT.search(text or ""):
        return "B"
    return None


def _iter_placemarks_with_hints(el, inherited_br=None, inherited_tipo=None):
    """Itera Placemarks carregando pistas vindas de Document/Folder."""
    current_br = inherited_br
    current_tipo = inherited_tipo
    if el.tag in (_KML_DOCUMENT_TAG, _KML_FOLDER_TAG):
        name = _nome_el_text(el)
        current_br = _br_hint_from_text(name) or current_br
        current_tipo = _tipo_hint_from_text(name) or current_tipo

    if el.tag == _KML_PLACEMARK_TAG:
        yield el, current_br, current_tipo
        return

    for child in list(el):
        yield from _iter_placemarks_with_hints(child, current_br, current_tipo)


def _km_from_text(text: str, allow_plain_number=False):
    text = (text or "").strip()
    m = _RE_DESC_KM.search(text)
    if m:
        return int(m.group(1))
    if allow_plain_number and _RE_INTEIRO.fullmatch(text):
        return int(text)
    return None


def _lonlat_from_placemark(pm):
    coords_el = pm.find(".//kml:Point/kml:coordinates", NS)
    if coords_el is None or not coords_el.text:
        return None
    p = coords_el.text.strip().split(",")
    if len(p) < 2:
        return None
    try:
        lon, lat = float(p[0]), float(p[1])
    except ValueError:
        return None
    return lon, lat


def _br_from_hint_or_snv(lon, lat, br_hint, snv_tree, snv_segments, max_dist_m=2000):
    if br_hint:
        return br_hint

    nearest = nearest_snv_segment(lon, lat, snv_tree, snv_segments)
    if nearest is None or nearest["dist_m"] > max_dist_m:
        return None
    return nearest["seg"]["br"]


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
        m = _RE_NOME_MARCO.search((name_el.text or "").strip())
        if not m:
            continue
        br, km_num = _normaliza_br(m.group(1)), int(m.group(2))

        lonlat = _lonlat_from_placemark(pm)
        if lonlat is None:
            continue
        lon, lat = lonlat

        markers.append({"br": br, "km_num": km_num, "lon": lon, "lat": lat})
    return markers


def parse_marcos_mq153_from_root(root, snv_tree, snv_segments):
    """
    Formato (b): Placemarks Point sem BR explicita.

    Aceita KM em ExtendedData/SimpleData[@name='DESC'] como "km-NNN" ou "NNN",
    e tambem em nomes puramente numericos. A BR vem de uma pasta/Document no
    padrao "MQ_040"/"BR-040", quando existir; caso contrario, e resolvida
    projetando o marco sobre a malha SNV e pegando a BR do segmento mais proximo.
    Retorna lista de {br, km_num, lon, lat}.
    """
    markers = []
    used_br_hint = False
    for pm, br_hint, tipo_hint in _iter_placemarks_with_hints(root):
        km_num = None

        desc_el = pm.find(".//kml:SimpleData[@name='DESC']", NS)
        if desc_el is not None and desc_el.text:
            km_num = _km_from_text(desc_el.text, allow_plain_number=True)

        if km_num is None:
            km_num = _km_from_text(_nome_el_text(pm), allow_plain_number=True)

        if km_num is None:
            continue

        lonlat = _lonlat_from_placemark(pm)
        if lonlat is None:
            continue
        lon, lat = lonlat

        if br_hint:
            used_br_hint = True
        br = _br_from_hint_or_snv(lon, lat, br_hint, snv_tree, snv_segments)
        if br is None:
            continue

        marker = {"br": br, "km_num": km_num, "lon": lon, "lat": lat}
        if tipo_hint:
            marker["tipo_sigla_hint"] = tipo_hint
        markers.append(marker)

    brs = sorted({mk["br"] for mk in markers})
    if markers and not used_br_hint and len(brs) > 1:
        raise CalcError(
            "KML_BR_AMBIGUA",
            "marcos_kml: KML sem BR explicita foi associado a multiplas BRs "
            f"({', '.join(brs)}). Selecione uma unica BR no filtro SNV ou use "
            "pastas/nomes no padrao MQ_NNN ou BR-NNN.",
        )

    return markers


def parse_marcos_kml(root, snv_tree, snv_segments):
    """Dispatch entre os formatos (a) e (b) de KML de marcos."""
    markers = parse_marcos_from_root(root)
    if not markers:
        markers = parse_marcos_mq153_from_root(root, snv_tree, snv_segments)
    if not markers:
        raise CalcError(
            "KML_SEM_MARCOS",
            "marcos_kml: nenhum marco valido - use Placemarks Point com nome "
            "'BR-NNN KM-MMM', atributo DESC como 'km-NNN'/'NNN' ou nome numerico.",
        )
    return markers


def parse_eixo_from_root(root):
    """
    Extrai o eixo rodoviario de um ET root.
    Suporta:
      - MultiGeometry com muitas LineStrings encadeadas (ex: Google Earth KMZ)
      - LineStrings avulsas em Placemarks separados
      - Folders aninhados
    O eixo nao precisa de atributos BR/UF; e usado apenas como geometria de percurso.
    Retorna lista de coordenadas [(lon, lat), ...] continua (ou None se vazio).
    A projecao para UTM e feita depois, sob demanda, na zona certa para cada
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
