"""Pré-computação de marcos projetados sobre eixos (SNV ou eixo do cliente)."""


def project_markers_onto_line(markers, br_filter, eixo_line, max_dist_m=2000):
    """
    Projeta os marcos do BR indicado sobre o eixo e ordena por posição.
    Filtra marcos fisicamente distantes do eixo (>max_dist_m) para evitar
    que marcos de outro trecho estadual da mesma BR contaminem o resultado
    (ex: marcos GO projetados sobre eixo MG quando a BR reinicia o KM na divisa).
    Retorna lista de {br, km_num, utm_pt, d_on_eixo}.
    """
    result = []
    for mk in markers:
        if mk["br"] != br_filter:
            continue
        if eixo_line.distance(mk["utm_pt"]) > max_dist_m:
            continue
        d = eixo_line.project(mk["utm_pt"])
        entry = dict(mk)
        entry["d_on_eixo"] = d
        result.append(entry)
    result.sort(key=lambda x: x["d_on_eixo"])
    return result


def build_snv_mq_proj(markers, snv_eixo):
    """
    Para cada eixo SNV (br/uf), projeta os marcos do mesmo BR.
    Retorna {"153/GO": [marcos_ordenados], "153/MG": [...], ...}.
    """
    result = {}
    for key, eixo_info in snv_eixo.items():
        proj = project_markers_onto_line(markers, eixo_info["br"], eixo_info["line_utm"])
        if proj:
            result[key] = proj
    return result


def build_eixo_mq_proj(markers, eixo_line):
    """
    Para cada BR presente nos marcos, projeta-os sobre o eixo do cliente.
    Retorna {"153": [marcos_ordenados], "262": [...], ...}.
    """
    brs = {mk["br"] for mk in markers}
    return {
        br: project_markers_onto_line(markers, br, eixo_line)
        for br in brs
    }
