"""Projeção de marcos quilométricos sobre um eixo (SNV ou eixo do cliente)."""

from shapely.geometry import Point

from core.geometry import to_utm_point


def _marker_matches_route_hint(marker, route_tipo_sigla):
    marker_hint = marker.get("tipo_sigla_hint")
    if not marker_hint or not route_tipo_sigla:
        return True
    return marker_hint == route_tipo_sigla


def _route_distance_limit(candidates, max_dist_m, route_band_m):
    near = [c for c in candidates if c["dist_to_eixo_m"] <= max_dist_m]
    if not near:
        return []

    best_dist = min(c["dist_to_eixo_m"] for c in near)
    adaptive_limit = min(max_dist_m, max(route_band_m, best_dist + route_band_m))
    strict = [c for c in near if c["dist_to_eixo_m"] <= adaptive_limit]
    return strict if len(strict) >= 2 else near


def project_markers_onto_line(markers, br_filter, eixo_line_utm, epsg, max_dist_m=2000,
                              route_tipo_sigla=None, route_band_m=150, uf_filter=None):
    """
    Projeta sobre `eixo_line_utm` (já em UTM, na zona `epsg`) os marcos do
    BR indicado, e ordena o resultado por posição ao longo do eixo.

    Filtra marcos fisicamente distantes do eixo e, quando o KML traz pista
    de ramo (ex: MQ_040_V ou MQ_040_EP), usa apenas o ramo compatível.

    Quando `uf_filter` é informado, descarta marcos de outra UF já
    identificada (mantém os de UF desconhecida). A numeração de km do SNV
    reinicia por UF — perto da divisa entre dois estados, um marco km-baixo
    do estado vizinho pode cair fisicamente perto do fim do trecho local
    (ex: km 831) e "roubar" o par de interpolação sem esse filtro, levando
    a quilometragem completamente errada.

    Depois do filtro amplo (`max_dist_m`), uma faixa adaptativa mantém os
    marcos realmente aderentes ao eixo. Isso evita que eixos paralelos e
    próximos, como subida/descida de serra, contaminem a interpolação.

    Retorna lista de {..., utm_pt, d_on_eixo}, ordenada por d_on_eixo.
    """
    candidates = []
    for mk in markers:
        if mk["br"] != br_filter:
            continue
        if uf_filter and mk.get("uf") and mk["uf"] != uf_filter:
            continue
        if not _marker_matches_route_hint(mk, route_tipo_sigla):
            continue
        utm_pt = Point(*to_utm_point(mk["lon"], mk["lat"], epsg))
        entry = dict(mk)
        entry["utm_pt"]    = utm_pt
        entry["dist_to_eixo_m"] = eixo_line_utm.distance(utm_pt)
        entry["d_on_eixo"] = eixo_line_utm.project(utm_pt)
        entry["proj_pt_utm"] = eixo_line_utm.interpolate(entry["d_on_eixo"])
        candidates.append(entry)

    result = _route_distance_limit(candidates, max_dist_m, route_band_m)
    result.sort(key=lambda x: x["d_on_eixo"])
    return result
