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
import streamlit as st

from core.calc import calcular_pontos, flatten_resultados
from core.errors import CalcError
from core.parsers_tabular import parse_pontos_file
from core.snv import list_brs_in_kmz, load_snv

_DIR    = os.path.dirname(os.path.abspath(__file__))
KMZ_SNV = os.path.join(_DIR, "SNV_202604A.kmz")

st.set_page_config(page_title="Localizador KM Rodoviário", layout="wide")


@st.cache_data(show_spinner=False)
def _brs_disponiveis():
    if not os.path.isfile(KMZ_SNV):
        return []
    return list_brs_in_kmz(KMZ_SNV)


@st.cache_resource(show_spinner="Carregando malha SNV...", max_entries=5)
def _load_snv_cached(brs_filtro: tuple):
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

    snv_segments, snv_tree, snv_eixo = _load_snv_cached(tuple(sorted(brs_sel)))
    brs_carregadas = sorted({s["br"] for s in snv_segments})
    st.success(f"{len(snv_segments)} trechos carregados\nBRs: {brs_carregadas}")

st.subheader("Arquivos de entrada")
col1, col2, col3 = st.columns(3)

with col1:
    pontos_upload = st.file_uploader(
        "Pontos a Verificar (obrigatório)",
        type=["txt", "csv", "json"],
        help="Lat/Lon por linha ('Latitude; Longitude' ou 'Latitude, Longitude') ou JSON.",
    )

with col2:
    marcos_upload = st.file_uploader(
        "Marcos Quilométricos (opcional)",
        type=["kml", "kmz", "txt", "csv", "json"],
        help="KML/KMZ com os marcos físicos, ou TXT/CSV/JSON com Latitude, Longitude e KM.",
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

    st.download_button(
        "Baixar resultados (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="resultados_km.csv",
        mime="text/csv",
    )

    if {"lat", "lon"}.issubset(df.columns):
        mapa_df = df[["lat", "lon"]].dropna().rename(columns={"lat": "latitude", "lon": "longitude"})
        if not mapa_df.empty:
            st.subheader("Mapa dos pontos consultados")
            st.map(mapa_df)
