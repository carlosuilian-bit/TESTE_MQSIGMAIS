"""Lógica de cálculo por camada + orquestrador principal (sem dependência de Flask)."""

import math

from shapely.geometry import Point

from core.errors import CalcError
from core.parsers_kml import kml_root_from_bytes, parse_eixo_from_root, parse_marcos_kml
from core.parsers_tabular import parse_marcos_file
from core.projection import build_eixo_mq_proj, build_snv_mq_proj
from core.snv import nearest_snv_segment
from core.geometry import from_utm_line, from_utm_point, to_utm_point


def _interpolate_km(markers_sorted, d_query):
    """
    Interpola a posição quilométrica entre os dois marcos consecutivos (por
    d_on_eixo) que envolvem d_query.

    Independente do sentido em que o eixo foi desenhado: o KM físico pode
    crescer OU decrescer conforme d_on_eixo aumenta (depende de como o KML
    foi digitalizado), e a interpolação linear entre os dois marcos vizinhos
    lida com ambos os casos corretamente — diferente de simplesmente pegar o
    marco anterior e somar metros, que só funciona se o KM crescer no mesmo
    sentido do eixo.

    Retorna (km, metros).
    """
    if len(markers_sorted) == 1:
        return markers_sorted[0]["km_num"], 0

    idx = len(markers_sorted) - 2
    for i in range(len(markers_sorted) - 1):
        if markers_sorted[i]["d_on_eixo"] <= d_query <= markers_sorted[i + 1]["d_on_eixo"]:
            idx = i
            break
    else:
        if d_query < markers_sorted[0]["d_on_eixo"]:
            idx = 0

    a, b = markers_sorted[idx], markers_sorted[idx + 1]
    span = b["d_on_eixo"] - a["d_on_eixo"]
    frac = (d_query - a["d_on_eixo"]) / span if span > 0 else 0.0

    km_pos = a["km_num"] + (b["km_num"] - a["km_num"]) * frac
    km     = math.floor(km_pos)
    metros = max(0, min(999, round((km_pos - km) * 1000)))
    return km, metros


def _calc_camada1(click_pt, snv_tree, snv_segments):
    """
    Camada 1 — SNV puro.
    Usa STRtree para achar o segmento mais próximo e interpola km_ini/km_fim.
    """
    nearest = nearest_snv_segment(click_pt, snv_tree, snv_segments)
    if nearest is None:
        return None

    seg, proj_d, seg_len = nearest["seg"], nearest["proj_d"], nearest["seg_len"]
    km_span = seg["km_fim"] - seg["km_ini"]

    frac   = max(0.0, min(1.0, proj_d / seg_len if seg_len > 0 else 0.0))
    km_pos = seg["km_ini"] + frac * km_span
    km     = int(km_pos)
    metros = max(0, min(999, round((km_pos - km) * 1000)))

    proj_pt    = seg["line_utm"].interpolate(proj_d)
    plon, plat = from_utm_point(proj_pt.x, proj_pt.y)

    return {
        "resultado":  f"{km}+{metros:03d}",
        "km":         km,
        "metros":     metros,
        "br":         seg["br"],
        "uf":         seg["uf"],
        "codigo_snv": seg["codigo"],
        "proj_lat":   round(plat, 7),
        "proj_lon":   round(plon, 7),
        "dist_m":     round(nearest["dist_m"], 1),
    }


def _calc_camada2(click_pt, br, uf, snv_eixo, snv_mq_proj):
    """Camada 2 — SNV eixo + Marcos do cliente. BR/UF vêm do resultado da Camada 1."""
    key      = f"{br}/{uf}"
    eixo_inf = snv_eixo.get(key)
    markers  = snv_mq_proj.get(key, [])
    if not eixo_inf or not markers:
        return None

    d_query    = eixo_inf["line_utm"].project(click_pt)
    km, metros = _interpolate_km(markers, d_query)
    return {
        "resultado": f"{km}+{metros:03d}",
        "km":        km,
        "metros":    metros,
        "br":        br,
        "uf":        uf,
    }


def _calc_camada3(click_pt, br, eixo_line, eixo_mq_proj):
    """Camada 3 — Eixo do cliente + Marcos do cliente. BR vem do resultado da Camada 1."""
    markers = eixo_mq_proj.get(br, [])
    if not eixo_line or not markers:
        return None

    d_query    = eixo_line.project(click_pt)
    km, metros = _interpolate_km(markers, d_query)
    return {
        "resultado": f"{km}+{metros:03d}",
        "km":        km,
        "metros":    metros,
        "br":        br,
    }


def calcular_pontos(pontos, marcos_file=None, eixo_file=None, *,
                     snv_segments, snv_tree, snv_eixo):
    """
    Orquestrador principal — porta a lógica de POST /api/calc do app.py Flask,
    sem nenhuma dependência de HTTP.

    pontos:      lista de {lat, lon}
    marcos_file: tupla (filename, bytes) opcional
    eixo_file:   tupla (filename, bytes) opcional — requer marcos_file
    Levanta CalcError em caso de entrada inválida.
    Retorna {"camadas_disponiveis", "total_pontos", "resultados"}.
    """
    if not pontos:
        raise CalcError("PONTOS_AUSENTE", "Nenhum ponto informado.")

    session_marcos    = None
    session_snv_mq    = {}
    session_eixo_line = None
    session_eixo_mq   = {}
    camadas           = ["snv"]

    if marcos_file is not None:
        filename, data = marcos_file
        session_marcos = parse_marcos_file(filename, data, snv_tree, snv_segments)
        session_snv_mq = build_snv_mq_proj(session_marcos, snv_eixo)
        camadas.append("snv_mq")

    if eixo_file is not None:
        if session_marcos is None:
            raise CalcError("MARCOS_AUSENTE",
                             "Arquivo de eixo requer que Marcos Quilométricos também seja enviado.")
        filename, data = eixo_file
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if ext not in ("kml", "kmz"):
            raise CalcError("KML_INVALIDO", f"Extensão não suportada para eixo: .{ext}")
        session_eixo_line = parse_eixo_from_root(kml_root_from_bytes(data))
        if session_eixo_line is None:
            raise CalcError("KML_SEM_EIXO", "eixo_kml: nenhuma geometria de eixo encontrada.")
        session_eixo_mq = build_eixo_mq_proj(session_marcos, session_eixo_line)
        camadas.append("eixo_mq")

    resultados = []
    for i, ponto in enumerate(pontos):
        try:
            lat = float(ponto["lat"])
            lon = float(ponto["lon"])
        except (KeyError, ValueError, TypeError):
            resultados.append({"indice": i, "erro": "lat/lon inválidos ou ausentes"})
            continue

        cx, cy   = to_utm_point(lon, lat)
        click_pt = Point(cx, cy)

        res = {"indice": i, "lat": lat, "lon": lon}

        r1 = _calc_camada1(click_pt, snv_tree, snv_segments)
        res["snv"] = r1 or {"erro": "SNV não disponível"}

        br = r1["br"] if r1 else None
        uf = r1["uf"] if r1 else None

        if "snv_mq" in camadas and br and uf:
            r2 = _calc_camada2(click_pt, br, uf, snv_eixo, session_snv_mq)
            res["snv_mq"] = r2 or {"erro": f"Sem marcos para BR-{br}/{uf}"}

        if "eixo_mq" in camadas and br:
            r3 = _calc_camada3(click_pt, br, session_eixo_line, session_eixo_mq)
            res["eixo_mq"] = r3 or {"erro": f"Sem marcos/eixo para BR-{br}"}

        resultados.append(res)

    return {
        "camadas_disponiveis": camadas,
        "total_pontos":        len(resultados),
        "resultados":          resultados,
        "debug":               _build_debug_geometrias(resultados, session_marcos,
                                                         session_eixo_line, snv_eixo),
    }


def _build_debug_geometrias(resultados, session_marcos, session_eixo_line, snv_eixo):
    """
    Monta geometrias auxiliares (lon/lat) para diagnóstico visual: eixo(s) SNV
    efetivamente usados pelos pontos calculados, eixo do usuário e marcos.
    """
    debug = {}

    brs_uf_usados = {
        (r["snv"]["br"], r["snv"]["uf"])
        for r in resultados
        if "snv" in r and "erro" not in r["snv"]
    }
    if brs_uf_usados:
        debug["eixo_snv"] = {}
        for br, uf in sorted(brs_uf_usados):
            key      = f"{br}/{uf}"
            eixo_inf = snv_eixo.get(key)
            if eixo_inf:
                debug["eixo_snv"][key] = from_utm_line(eixo_inf["line_utm"])

    if session_marcos:
        debug["marcos"] = []
        for mk in session_marcos:
            lon, lat = from_utm_point(mk["utm_pt"].x, mk["utm_pt"].y)
            debug["marcos"].append({
                "br": mk["br"], "km_num": mk["km_num"],
                "lat": round(lat, 7), "lon": round(lon, 7),
            })

    if session_eixo_line is not None:
        debug["eixo_usuario"] = from_utm_line(session_eixo_line)

    return debug


def flatten_resultados(body: dict) -> list:
    """Achata os resultados aninhados em linhas simples para exibição em tabela."""
    camadas = body.get("camadas_disponiveis", [])
    linhas  = []
    for r in body.get("resultados", []):
        row = {"indice": r.get("indice"), "lat": r.get("lat"), "lon": r.get("lon")}

        if "erro" in r:
            row["erro"] = r["erro"]
            linhas.append(row)
            continue

        snv = r.get("snv") or {}
        row["snv_resultado"] = snv.get("resultado", "-") if "erro" not in snv else f"[{snv['erro']}]"
        row["snv_br"]        = snv.get("br")
        row["snv_uf"]        = snv.get("uf")
        row["snv_dist_m"]    = snv.get("dist_m")

        if "snv_mq" in camadas:
            smq = r.get("snv_mq") or {}
            row["snv_mq_resultado"] = smq.get("resultado", "-") if "erro" not in smq else f"[{smq['erro']}]"

        if "eixo_mq" in camadas:
            emq = r.get("eixo_mq") or {}
            row["eixo_mq_resultado"] = emq.get("resultado", "-") if "erro" not in emq else f"[{emq['erro']}]"

        linhas.append(row)
    return linhas
