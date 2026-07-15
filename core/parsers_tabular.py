"""Parsers de TXT/CSV/JSON para pontos de consulta e marcos quilométricos."""

import json
import os

from core.errors import CalcError
from core.snv import resolve_br_uf_for_point

_LAT_KEYS = ("lat", "latitude")
_LON_KEYS = ("lon", "lng", "longitude")
_KM_KEYS  = ("km", "km_num", "quilometro", "kmnum")


def _split_row(line: str) -> list:
    """Detecta ';' como separador se presente, senão ','."""
    sep = ";" if ";" in line else ","
    return [p.strip() for p in line.split(sep)]


def _first_key(d: dict, keys, cast=float):
    for k in keys:
        if k in d:
            try:
                return cast(d[k])
            except (TypeError, ValueError):
                return None
    return None


# ── Pontos a verificar (lat, lon) ──────────────────────────────────────────

def parse_pontos_txt_csv(text: str) -> list:
    pontos = []
    for linha in text.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        partes = _split_row(linha)
        if len(partes) < 2:
            continue
        try:
            pontos.append({"lat": float(partes[0]), "lon": float(partes[1])})
        except ValueError:
            continue
    return pontos


def parse_pontos_json(data: bytes) -> list:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError:
        raise CalcError("PONTOS_INVALIDO", "JSON de pontos inválido.")

    if not isinstance(raw, list):
        raise CalcError("PONTOS_INVALIDO", "JSON de pontos deve ser uma lista.")

    pontos = []
    for item in raw:
        if isinstance(item, dict):
            lat = _first_key(item, _LAT_KEYS)
            lon = _first_key(item, _LON_KEYS)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                lat, lon = float(item[0]), float(item[1])
            except (TypeError, ValueError):
                lat = lon = None
        else:
            lat = lon = None
        if lat is not None and lon is not None:
            pontos.append({"lat": lat, "lon": lon})
    return pontos


def parse_pontos_file(filename: str, data: bytes) -> list:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".json":
        pontos = parse_pontos_json(data)
    elif ext in (".txt", ".csv"):
        pontos = parse_pontos_txt_csv(data.decode("utf-8-sig"))
    else:
        raise CalcError("PONTOS_INVALIDO", f"Extensão não suportada para pontos: {ext}")

    if not pontos:
        raise CalcError("PONTOS_INVALIDO",
                         "Nenhuma coordenada válida encontrada no arquivo de pontos.")
    return pontos


# ── Marcos quilométricos (lat, lon, km) ────────────────────────────────────

def _marker_from_latlonkm(lat, lon, km_num, snv_tree, snv_segments):
    br, _uf = resolve_br_uf_for_point(lon, lat, snv_tree, snv_segments)
    if br is None:
        return None
    return {"br": br, "km_num": int(km_num), "lon": lon, "lat": lat}


def parse_marcos_txt_csv(text: str, snv_tree, snv_segments) -> list:
    markers = []
    for linha in text.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        partes = _split_row(linha)
        if len(partes) < 3:
            continue
        try:
            lat, lon, km_num = float(partes[0]), float(partes[1]), float(partes[2])
        except ValueError:
            continue
        mk = _marker_from_latlonkm(lat, lon, km_num, snv_tree, snv_segments)
        if mk:
            markers.append(mk)
    return markers


def parse_marcos_json(data: bytes, snv_tree, snv_segments) -> list:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError:
        raise CalcError("MARCOS_INVALIDO", "JSON de marcos inválido.")

    if not isinstance(raw, list):
        raise CalcError("MARCOS_INVALIDO", "JSON de marcos deve ser uma lista.")

    markers = []
    for item in raw:
        if isinstance(item, dict):
            lat = _first_key(item, _LAT_KEYS)
            lon = _first_key(item, _LON_KEYS)
            km  = _first_key(item, _KM_KEYS)
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            try:
                lat, lon, km = float(item[0]), float(item[1]), float(item[2])
            except (TypeError, ValueError):
                lat = lon = km = None
        else:
            lat = lon = km = None

        if lat is None or lon is None or km is None:
            continue
        mk = _marker_from_latlonkm(lat, lon, km, snv_tree, snv_segments)
        if mk:
            markers.append(mk)
    return markers


def parse_marcos_file(filename: str, data: bytes, snv_tree, snv_segments) -> list:
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".kml", ".kmz"):
        from core.parsers_kml import kml_root_from_bytes, parse_marcos_kml
        root = kml_root_from_bytes(data)
        return parse_marcos_kml(root, snv_tree, snv_segments)
    elif ext == ".json":
        markers = parse_marcos_json(data, snv_tree, snv_segments)
    elif ext in (".txt", ".csv"):
        markers = parse_marcos_txt_csv(data.decode("utf-8-sig"), snv_tree, snv_segments)
    else:
        raise CalcError("MARCOS_INVALIDO", f"Extensão não suportada para marcos: {ext}")

    if not markers:
        raise CalcError("MARCOS_INVALIDO",
                         "Nenhum marco válido encontrado no arquivo (verifique lat, "
                         "lon, km e se os pontos estão próximos de uma BR do SNV carregado).")
    return markers
