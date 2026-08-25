"""
Localizador KM Rodoviário — app Streamlit autossuficiente.

Camadas:
  snv      — SNV puro (STRtree sobre KMZ do DNIT, km por interpolação km_ini/km_fim)
  snv_mq   — Eixo SNV + Marcos do usuário (km pelo marco mais próximo anterior no eixo SNV)
  eixo_mq  — Eixo do usuário + Marcos do usuário (mesma lógica, geometria do usuário)

Formatos aceitos:
  Pontos a verificar (obrigatório): TXT, CSV (Latitude;Longitude ou Latitude,Longitude) ou JSON.
  Marcos quilométricos (opcional):  KML/KMZ (2 formatos) ou TXT/CSV/JSON (Latitude, Longitude, KM).
  Eixo (opcional, requer Marcos):   KML ou KMZ.
"""

import os

import pandas as pd
import pydeck as pdk
import streamlit as st

from core.calc import calcular_pontos, flatten_resultados, flatten_resultados_download
from core.errors import CalcError
from core.parsers_tabular import parse_pontos_file
from core.snv import list_brs_in_kmz, load_snv

_DIR    = os.path.dirname(os.path.abspath(__file__))
KMZ_SNV = os.path.join(_DIR, "SNV_202604A.kmz")
SNV_CACHE_VERSION = "principal-variante-v2"

st.set_page_config(page_title="Localizador KM Rodoviário", layout="wide")


@st.cache_data(show_spinner=False)
def _brs_disponiveis():
    if not os.path.isfile(KMZ_SNV):
        return []
    return list_brs_in_kmz(KMZ_SNV)


@st.cache_resource(show_spinner="Carregando malha SNV...", max_entries=5)
def _load_snv_cached(brs_filtro: tuple, cache_version: str):
    _ = cache_version
    return load_snv(KMZ_SNV, set(brs_filtro) if brs_filtro else None)


st.title("Localizador KM Rodoviário")
st.caption(
    "Calcula a quilometragem de pontos sobre rodovias federais, combinando a malha "
    "SNV (DNIT) com Marcos Quilométricos e eixo opcionalmente enviados pelo usuário."
)

with st.sidebar:
    st.header("Malha SNV")
    if not os.path.isfile(KMZ_SNV):
        st.error(f"Arquivo não encontrado: {os.path.basename(KMZ_SNV)}")
        st.stop()

    opcoes_br = _brs_disponiveis()
    default_br = ["153"] if "153" in opcoes_br else []
    brs_sel = st.multiselect(
        "Filtrar BRs do SNV",
        options=opcoes_br,
        default=default_br,
        help="Vazio = carrega a malha nacional completa (mais lento, mais memória).",
    )

    snv_segments, snv_tree, snv_eixo = _load_snv_cached(tuple(sorted(brs_sel)), SNV_CACHE_VERSION)
    brs_carregadas = sorted({s["br"] for s in snv_segments})
    st.success(f"{len(snv_segments)} trechos carregados (principal + variantes)\nBRs: {brs_carregadas}")
    st.caption(f"Cache SNV: {SNV_CACHE_VERSION}")

st.subheader("Arquivos de entrada")
col1, col2, col3 = st.columns(3)

with col1:
    pontos_upload = st.file_uploader(
        "Pontos a Verificar (obrigatório)",
        type=["txt", "csv", "json"],
        help=(
            "Lat/Lon por linha ('Latitude; Longitude' ou 'Latitude, Longitude'), "
            "ou ID;Latitude, Longitude. JSON tambem e aceito."
        ),
    )

with col2:
    marcos_upload = st.file_uploader(
        "Marcos Quilométricos (opcional)",
        type=["kml", "kmz", "txt", "csv", "json"],
        help="KML/KMZ com os marcos físicos, ou TXT/CSV/JSON com Latitude, Longitude e KM.",
    )

    st.caption(
        "KML/KMZ: aceita BR-NNN KM-MMM, DESC km-NNN/NNN ou nome numerico. "
        "Sem BR explicita, selecione a BR correta no filtro SNV."
    )

with col3:
    eixo_upload = st.file_uploader(
        "Eixo da rodovia (opcional — requer Marcos)",
        type=["kml", "kmz"],
        help="KML ou KMZ com o traçado do eixo. Só é usado se Marcos também for enviado.",
    )

calcular = st.button("Calcular quilometragem", type="primary")

if calcular:
    if not pontos_upload:
        st.error("Envie um arquivo de Pontos a Verificar antes de calcular.")
        st.stop()
    if eixo_upload and not marcos_upload:
        st.error("O arquivo de Eixo requer que Marcos Quilométricos também seja enviado.")
        st.stop()

    try:
        pontos = parse_pontos_file(pontos_upload.name, pontos_upload.getvalue())

        marcos_file = (marcos_upload.name, marcos_upload.getvalue()) if marcos_upload else None
        eixo_file   = (eixo_upload.name, eixo_upload.getvalue()) if eixo_upload else None

        with st.spinner("Calculando..."):
            body = calcular_pontos(
                pontos, marcos_file=marcos_file, eixo_file=eixo_file,
                snv_segments=snv_segments, snv_tree=snv_tree, snv_eixo=snv_eixo,
            )
    except CalcError as e:
        st.error(f"[{e.codigo}] {e.mensagem}")
        st.stop()

    st.success(
        f"{body['total_pontos']} ponto(s) calculado(s) — "
        f"camadas disponíveis: {', '.join(body['camadas_disponiveis'])}"
    )

    df = pd.DataFrame(flatten_resultados(body))
    st.dataframe(df, use_container_width=True)

    df_download = pd.DataFrame(flatten_resultados_download(body))
    st.download_button(
        "Baixar Resultados (CSV)",
        data=df_download.to_csv(index=False).encode("utf-8"),
        file_name="resultados_km.csv",
        mime="text/csv",
    )

    debug = body.get("debug", {})

    if debug.get("eixo_snv") or debug.get("marcos") or debug.get("eixo_usuario"):
        st.subheader("Mapa de diagnóstico — eixo SNV, eixo enviado e marcos")
        st.caption(
            "🔵 Eixo SNV (malha do DNIT) · 🟠 Eixo enviado · 🟢 Marcos quilométricos · "
            "🔴 Pontos consultados. Divergência grande entre SNV+MQ e Eixo+MQ costuma "
            "aparecer aqui como um desvio visível entre a linha azul e a laranja perto "
            "dos pontos vermelhos, ou como marcos verdes mais próximos de uma linha "
            "do que da outra."
        )

        COR_EIXO_SNV = [30, 144, 255]
        COR_EIXO_USR = [255, 140, 0]
        COR_MARCO    = [34, 197, 94]
        COR_PONTO    = [239, 68, 68]

        layers = []
        focus_coords = []  # usados para enquadrar o mapa — só a área de interesse do usuário

        for key, coords in debug.get("eixo_snv", {}).items():
            layers.append(pdk.Layer(
                "PathLayer",
                data=[{"path": coords, "label": f"Eixo SNV {key}"}],
                get_path="path",
                get_color=COR_EIXO_SNV,
                get_width=4,
                width_min_pixels=2,
                pickable=True,
            ))
            # Não entra no enquadramento: o eixo SNV de uma BR/UF inteira pode ter
            # centenas de km, muito além da área que o usuário quer inspecionar.

        if debug.get("eixo_usuario"):
            layers.append(pdk.Layer(
                "PathLayer",
                data=[{"path": debug["eixo_usuario"], "label": "Eixo enviado"}],
                get_path="path",
                get_color=COR_EIXO_USR,
                get_width=4,
                width_min_pixels=2,
                pickable=True,
            ))
            focus_coords.extend(debug["eixo_usuario"])

        if debug.get("marcos"):
            marcos_data = [
                {"position": [m["lon"], m["lat"]],
                 "label": f"Marco KM {m['km_num']} (BR-{m['br']})"}
                for m in debug["marcos"]
            ]
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=marcos_data,
                get_position="position",
                get_fill_color=COR_MARCO,
                get_radius=25,
                radius_min_pixels=3,
                pickable=True,
            ))
            focus_coords.extend(m["position"] for m in marcos_data)

        pontos_data = [
            {"position": [row["lon"], row["lat"]],
             "label": f"Ponto #{row['indice']}: SNV {row.get('snv_resultado', '-')} "
                      f"| SNV+MQ {row.get('snv_mq_resultado', '-')} "
                      f"| Eixo+MQ {row.get('eixo_mq_resultado', '-')}"}
            for row in flatten_resultados(body)
            if row.get("lat") is not None and row.get("lon") is not None
        ]
        if pontos_data:
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=pontos_data,
                get_position="position",
                get_fill_color=COR_PONTO,
                get_radius=35,
                radius_min_pixels=4,
                pickable=True,
            ))
            focus_coords.extend(p["position"] for p in pontos_data)

        # Fallback: se não há marcos/eixo/pontos válidos (não deveria ocorrer, já
        # que pontos são obrigatórios), enquadra pelo eixo SNV mesmo.
        if not focus_coords:
            for coords in debug.get("eixo_snv", {}).values():
                focus_coords.extend(coords)

        if focus_coords:
            view_state = pdk.data_utils.compute_view(focus_coords, view_proportion=0.7)
            view_state.zoom = min(view_state.zoom, 16)
            st.pydeck_chart(pdk.Deck(
                layers=layers,
                initial_view_state=view_state,
                tooltip={"text": "{label}"},
                map_style=None,
            ))
    elif {"lat", "lon"}.issubset(df.columns):
        mapa_df = df[["lat", "lon"]].dropna().rename(columns={"lat": "latitude", "lon": "longitude"})
        if not mapa_df.empty:
            st.subheader("Mapa dos pontos consultados")
            st.map(mapa_df)
