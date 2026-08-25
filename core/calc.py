"""Lógica de cálculo por camada + orquestrador principal (sem dependência de Flask)."""

from shapely.geometry import Point

from core.errors import CalcError
from core.parsers_kml import kml_root_from_bytes, parse_eixo_from_root
from core.parsers_tabular import parse_marcos_file
from core.projection import project_markers_onto_line
from core.snv import nearest_snv_segment
from core.geometry import from_utm_line, from_utm_point, to_utm_line, to_utm_point, utm_epsg_for_lonlat


def _pair_distance_score(a, b, click_utm_pt):
    if click_utm_pt is None:
        return 0
    return (
        a["utm_pt"].distance(click_utm_pt)
        + b["utm_pt"].distance(click_utm_pt)
        + a.get("dist_to_eixo_m", 0)
        + b.get("dist_to_eixo_m", 0)
    )


def _interpolate_km(markers_sorted, d_query, click_utm_pt=None):
    """
    Calcula a quilometragem usando a distancia fisica no eixo a partir do
    marco-base do par que envolve d_query.

    Se os marcos KM 153 e KM 154 estao separados por 1500 m, um ponto a
    700 m do KM 153 vira 153+700; um ponto a 1010 m vira 153+999. O teto
    evita que a metragem ultrapasse o formato valido de tres digitos.

    Retorna (km, metros).
    """
    if len(markers_sorted) == 1:
        return markers_sorted[0]["km_num"], 0

    bracket_pairs = [
        i for i in range(len(markers_sorted) - 1)
        if markers_sorted[i]["d_on_eixo"] <= d_query <= markers_sorted[i + 1]["d_on_eixo"]
    ]

    if bracket_pairs:
        idx = min(
            bracket_pairs,
            key=lambda i: _pair_distance_score(markers_sorted[i], markers_sorted[i + 1], click_utm_pt),
        )
    elif d_query < markers_sorted[0]["d_on_eixo"]:
        idx = 0
    else:
        idx = len(markers_sorted) - 2

    a, b = markers_sorted[idx], markers_sorted[idx + 1]
    if a["km_num"] <= b["km_num"]:
        km = a["km_num"]
        offset_m = d_query - a["d_on_eixo"]
    else:
        km = b["km_num"]
        offset_m = b["d_on_eixo"] - d_query

    metros = max(0, min(999, round(offset_m)))
    return km, metros


def _calc_camada1(lon, lat, click_utm_pt, epsg, snv_tree, snv_segments, seg_utm_cache):
    """
    Camada 1 — SNV puro.
    Acha o segmento SNV mais próximo (busca aproximada em lon/lat) e
    interpola km_ini/km_fim na zona UTM resolvida para este ponto.
    """
    nearest = nearest_snv_segment(lon, lat, snv_tree, snv_segments,
                                   epsg=epsg, seg_utm_cache=seg_utm_cache)
    if nearest is None:
        return None

    seg, proj_d, seg_len = nearest["seg"], nearest["proj_d"], nearest["seg_len"]
    km_span = seg["km_fim"] - seg["km_ini"]

    frac   = max(0.0, min(1.0, proj_d / seg_len if seg_len > 0 else 0.0))
    km_pos = seg["km_ini"] + frac * km_span
    km     = int(km_pos)
    metros = max(0, min(999, round((km_pos - km) * 1000)))

    proj_pt    = nearest["seg_line_utm"].interpolate(proj_d)
    plon, plat = from_utm_point(proj_pt.x, proj_pt.y, epsg)

    return {
        "resultado":  f"{km}+{metros:03d}",
        "km":         km,
        "metros":     metros,
        "br":         seg["br"],
        "uf":         seg["uf"],
        "codigo_snv": seg["codigo"],
        "tipo_snv":   seg.get("tipo"),
        "snv_eixo_key": seg.get("eixo_key", f"{seg['br']}/{seg['uf']}"),
        "proj_lat":   round(plat, 7),
        "proj_lon":   round(plon, 7),
        "dist_m":     round(nearest["dist_m"], 1),
    }


def _calc_camada2(click_utm_pt, br, uf, eixo_line_utm, markers_proj):
    """Camada 2 — SNV eixo + Marcos do cliente. BR/UF vêm do resultado da Camada 1."""
    if eixo_line_utm is None or not markers_proj:
        return None

    d_query    = eixo_line_utm.project(click_utm_pt)
    km, metros = _interpolate_km(markers_proj, d_query, click_utm_pt)
    return {
        "resultado": f"{km}+{metros:03d}",
        "km":        km,
        "metros":    metros,
        "br":        br,
        "uf":        uf,
    }


def _calc_camada3(click_utm_pt, br, eixo_line_utm, markers_proj):
    """Camada 3 — Eixo do cliente + Marcos do cliente. BR vem do resultado da Camada 1."""
    if eixo_line_utm is None or not markers_proj:
        return None

    d_query    = eixo_line_utm.project(click_utm_pt)
    km, metros = _interpolate_km(markers_proj, d_query, click_utm_pt)
    return {
        "resultado": f"{km}+{metros:03d}",
        "km":        km,
        "metros":    metros,
        "br":        br,
    }


class _ZoneCache:
    """
    Cache por-requisição de geometrias reprojetadas por zona UTM.

    Pontos de uma mesma requisição podem cair em zonas UTM diferentes (ex:
    uma concessionária consultando pontos em vários estados) — e até um
    único eixo enviado pode atravessar a fronteira entre duas zonas.
    Reprojetar do zero a cada ponto seria correto, porém caro; este cache
    garante que cada eixo (SNV por BR/UF, ou eixo do usuário) só é
    reprojetado uma vez por zona efetivamente usada na requisição, e só
    para BR/UF que realmente aparecem nos pontos consultados.
    """

    def __init__(self, snv_eixo, session_marcos, session_eixo_lonlat):
        self._snv_eixo            = snv_eixo
        self._session_marcos      = session_marcos or []
        self._session_eixo_lonlat = session_eixo_lonlat
        self._snv_eixo_utm        = {}   # (key, epsg) -> LineString | None
        self._snv_mq_proj         = {}   # (key, epsg) -> markers projetados
        self._eixo_usuario_utm    = {}   # epsg        -> LineString
        self._eixo_mq_proj        = {}   # (br, epsg)  -> markers projetados
        self.seg_utm_cache        = {}   # (idx, epsg) -> LineString (segmentos SNV)

    def snv_mq_markers(self, br, uf, epsg, eixo_key=None):
        key       = eixo_key or f"{br}/{uf}"
        cache_key = (key, epsg)
        if cache_key not in self._snv_eixo_utm:
            eixo_info = self._snv_eixo.get(key) or self._snv_eixo.get(f"{br}/{uf}")
            self._snv_eixo_utm[cache_key] = (
                to_utm_line(eixo_info["coords_lonlat"], epsg) if eixo_info else None
            )
        else:
            eixo_info = self._snv_eixo.get(key) or self._snv_eixo.get(f"{br}/{uf}")
        eixo_line_utm = self._snv_eixo_utm[cache_key]

        if cache_key not in self._snv_mq_proj:
            self._snv_mq_proj[cache_key] = (
                project_markers_onto_line(
                    self._session_marcos,
                    br,
                    eixo_line_utm,
                    epsg,
                    route_tipo_sigla=eixo_info.get("tipo_sigla") if eixo_info else None,
                )
                if eixo_line_utm is not None else []
            )
        return eixo_line_utm, self._snv_mq_proj[cache_key]

    def eixo_mq_markers(self, br, epsg):
        if self._session_eixo_lonlat is None:
            return None, []
        if epsg not in self._eixo_usuario_utm:
            self._eixo_usuario_utm[epsg] = to_utm_line(self._session_eixo_lonlat, epsg)
        eixo_line_utm = self._eixo_usuario_utm[epsg]

        cache_key = (br, epsg)
        if cache_key not in self._eixo_mq_proj:
            self._eixo_mq_proj[cache_key] = project_markers_onto_line(
                self._session_marcos, br, eixo_line_utm, epsg,
            )
        return eixo_line_utm, self._eixo_mq_proj[cache_key]


def calcular_pontos(pontos, marcos_file=None, eixo_file=None, *,
                     snv_segments, snv_tree, snv_eixo):
    """
    Orquestrador principal.

    A zona UTM de cálculo é resolvida automaticamente PARA CADA PONTO, a
    partir da própria longitude (core.geometry.utm_epsg_for_lonlat) — não
    existe mais uma zona fixa para o Brasil inteiro. Isso é necessário
    porque uma mesma requisição pode ter pontos em regiões diferentes do
    país, e um único eixo enviado pode atravessar a fronteira entre duas
    zonas UTM.

    pontos:      lista de {lat, lon}
    marcos_file: tupla (filename, bytes) opcional
    eixo_file:   tupla (filename, bytes) opcional — requer marcos_file
    Levanta CalcError em caso de entrada inválida.
    Retorna {"camadas_disponiveis", "total_pontos", "resultados", "debug"}.
    """
    if not pontos:
        raise CalcError("PONTOS_AUSENTE", "Nenhum ponto informado.")

    session_marcos      = None
    session_eixo_lonlat = None
    camadas              = ["snv"]

    if marcos_file is not None:
        filename, data = marcos_file
        session_marcos = parse_marcos_file(filename, data, snv_tree, snv_segments)
        camadas.append("snv_mq")

    if eixo_file is not None:
        if session_marcos is None:
            raise CalcError("MARCOS_AUSENTE",
                             "Arquivo de eixo requer que Marcos Quilométricos também seja enviado.")
        filename, data = eixo_file
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if ext not in ("kml", "kmz"):
            raise CalcError("KML_INVALIDO", f"Extensão não suportada para eixo: .{ext}")
        session_eixo_lonlat = parse_eixo_from_root(kml_root_from_bytes(data))
        if session_eixo_lonlat is None:
            raise CalcError("KML_SEM_EIXO", "eixo_kml: nenhuma geometria de eixo encontrada.")
        camadas.append("eixo_mq")

    zc = _ZoneCache(snv_eixo, session_marcos, session_eixo_lonlat)

    resultados = []
    for i, ponto in enumerate(pontos):
        try:
            lat = float(ponto["lat"])
            lon = float(ponto["lon"])
        except (KeyError, ValueError, TypeError):
            resultados.append({"indice": i, "erro": "lat/lon inválidos ou ausentes"})
            continue

        epsg      = utm_epsg_for_lonlat(lon, lat)
        click_utm = Point(*to_utm_point(lon, lat, epsg))

        res = {"indice": i, "lat": lat, "lon": lon, "utm_epsg": epsg}

        r1 = _calc_camada1(lon, lat, click_utm, epsg, snv_tree, snv_segments, zc.seg_utm_cache)
        res["snv"] = r1 or {"erro": "SNV não disponível"}

        br = r1["br"] if r1 else None
        uf = r1["uf"] if r1 else None

        if "snv_mq" in camadas and br and uf:
            eixo_utm, markers_proj = zc.snv_mq_markers(br, uf, epsg, r1.get("snv_eixo_key"))
            r2 = _calc_camada2(click_utm, br, uf, eixo_utm, markers_proj)
            res["snv_mq"] = r2 or {"erro": f"Sem marcos para BR-{br}/{uf}"}

        if "eixo_mq" in camadas and br:
            eixo_utm, markers_proj = zc.eixo_mq_markers(br, epsg)
            r3 = _calc_camada3(click_utm, br, eixo_utm, markers_proj)
            res["eixo_mq"] = r3 or {"erro": f"Sem marcos/eixo para BR-{br}"}

        resultados.append(res)

    return {
        "camadas_disponiveis": camadas,
        "total_pontos":        len(resultados),
        "resultados":          resultados,
        "debug":               _build_debug_geometrias(resultados, session_marcos,
                                                         session_eixo_lonlat, zc),
    }


def _build_debug_geometrias(resultados, session_marcos, session_eixo_lonlat, zc):
    """
    Monta geometrias auxiliares (lon/lat) para diagnóstico visual: eixo(s)
    SNV efetivamente usados pelos pontos calculados, eixo do usuário e
    marcos. Reaproveita o cache de zonas já construído durante o cálculo
    (não reprojeta nada de novo).
    """
    debug = {}

    brs_uf_epsg_usados = {
        (r["snv"]["br"], r["snv"]["uf"], r["utm_epsg"], r["snv"].get("snv_eixo_key"))
        for r in resultados
        if "snv" in r and "erro" not in r["snv"]
    }
    if brs_uf_epsg_usados:
        debug["eixo_snv"] = {}
        for br, uf, epsg, eixo_key in sorted(brs_uf_epsg_usados):
            eixo_line_utm, _ = zc.snv_mq_markers(br, uf, epsg, eixo_key)
            if eixo_line_utm is not None:
                debug["eixo_snv"].setdefault(eixo_key or f"{br}/{uf}",
                                             from_utm_line(eixo_line_utm, epsg))

    if session_marcos:
        debug["marcos"] = [
            {"br": mk["br"], "km_num": mk["km_num"],
             "lat": round(mk["lat"], 7), "lon": round(mk["lon"], 7)}
            for mk in session_marcos
        ]

    if session_eixo_lonlat is not None:
        debug["eixo_usuario"] = [[lon, lat] for lon, lat in session_eixo_lonlat]

    return debug


def flatten_resultados(body: dict) -> list:
    """Achata os resultados aninhados em linhas simples para exibição em tabela."""
    camadas = body.get("camadas_disponiveis", [])
    linhas  = []
    for r in body.get("resultados", []):
        row = {"indice": r.get("indice"), "lat": r.get("lat"), "lon": r.get("lon"),
               "utm_epsg": r.get("utm_epsg")}

        if "erro" in r:
            row["erro"] = r["erro"]
            linhas.append(row)
            continue

        snv = r.get("snv") or {}
        row["snv_resultado"] = snv.get("resultado", "-") if "erro" not in snv else f"[{snv['erro']}]"
        row["snv_br"]        = snv.get("br")
        row["snv_uf"]        = snv.get("uf")
        row["snv_codigo_snv"] = snv.get("codigo_snv")
        row["snv_tipo_snv"]  = snv.get("tipo_snv")
        row["snv_dist_m"]    = snv.get("dist_m")

        if "snv_mq" in camadas:
            smq = r.get("snv_mq") or {}
            row["snv_mq_resultado"] = smq.get("resultado", "-") if "erro" not in smq else f"[{smq['erro']}]"

        if "eixo_mq" in camadas:
            emq = r.get("eixo_mq") or {}
            row["eixo_mq_resultado"] = emq.get("resultado", "-") if "erro" not in emq else f"[{emq['erro']}]"

        linhas.append(row)
    return linhas


def flatten_resultados_download(body: dict) -> list:
    """
    Gera linhas enxutas para o CSV, usando o melhor nivel de precisao disponivel.

    Nivel 1: coordenadas + SNV.
    Nivel 2: coordenadas + SNV + Marcos Quilometricos.
    Nivel 3: coordenadas + Marcos Quilometricos + eixo enviado.
    """
    camadas = body.get("camadas_disponiveis", [])
    if "eixo_mq" in camadas:
        campo_resultado = "eixo_mq"
    elif "snv_mq" in camadas:
        campo_resultado = "snv_mq"
    else:
        campo_resultado = "snv"

    linhas = []
    for r in body.get("resultados", []):
        row = {
            "indice": r.get("indice"),
            "lat": r.get("lat"),
            "lon": r.get("lon"),
        }

        if "erro" in r:
            row["km+m"] = f"[{r['erro']}]"
        else:
            camada = r.get(campo_resultado) or {}
            row["km+m"] = (
                camada.get("resultado", "-")
                if "erro" not in camada
                else f"[{camada['erro']}]"
            )

        linhas.append(row)
    return linhas
