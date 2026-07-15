"""Projeção de marcos quilométricos sobre um eixo (SNV ou eixo do cliente)."""

from shapely.geometry import Point

from core.geometry import to_utm_point


def project_markers_onto_line(markers, br_filter, eixo_line_utm, epsg, max_dist_m=2000):
    """
    Projeta sobre `eixo_line_utm` (já em UTM, na zona `epsg`) os marcos do
    BR indicado, e ordena o resultado por posição ao longo do eixo.

    Filtra marcos fisicamente distantes do eixo (>max_dist_m) para evitar
    que marcos de outro trecho estadual da mesma BR contaminem o resultado
    (ex: marcos GO projetados sobre eixo MG quando a BR reinicia o KM na
    divisa).

    Retorna lista de {..., utm_pt, d_on_eixo}, ordenada por d_on_eixo.
    """
    result = []
    for mk in markers:
        if mk["br"] != br_filter:
            continue
        utm_pt = Point(*to_utm_point(mk["lon"], mk["lat"], epsg))
        if eixo_line_utm.distance(utm_pt) > max_dist_m:
            continue
        entry = dict(mk)
        entry["utm_pt"]    = utm_pt
        entry["d_on_eixo"] = eixo_line_utm.project(utm_pt)
        result.append(entry)
    result.sort(key=lambda x: x["d_on_eixo"])
    return result
