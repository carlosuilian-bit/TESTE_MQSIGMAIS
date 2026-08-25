# Localizador KM Rodoviário — Streamlit

App Streamlit autossuficiente para localização quilométrica rodoviária, em três
camadas de precisão crescente:

- **SNV puro** (`snv`) — usa exclusivamente a malha nacional SNV do DNIT
  (`SNV_202604A.kmz`, pré-carregada) para interpolar o KM pelo segmento mais
  próximo.
- **SNV + Marcos** (`snv_mq`) — o usuário envia um arquivo de Marcos
  Quilométricos; o KM é calculado pelo marco mais próximo anterior, projetado
  sobre o eixo SNV. Elimina o erro sistemático dos atributos de km do SNV.
- **Eixo + Marcos** (`eixo_mq`) — o usuário também envia o eixo da rodovia
  (KML/KMZ); os marcos são projetados sobre esse eixo em vez do eixo SNV.

Diferente da versão original em Flask, este app **não depende de um servidor
HTTP à parte** — toda a lógica de parsing e cálculo está em `core/` e é
chamada diretamente pela UI Streamlit, em memória. Isso é necessário porque o
Streamlit Community Cloud roda um único processo Python.

## Rodando localmente

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Abra o endereço indicado no terminal (geralmente `http://localhost:8501`).

## Deploy no Streamlit Community Cloud

1. Crie um repositório no GitHub contendo o conteúdo desta pasta na raiz
   (`streamlit_app.py`, `core/`, `requirements.txt`, `SNV_202604A.kmz`, etc).
2. `git init && git add . && git commit -m "..." && git push` para esse repositório.
3. Em [share.streamlit.io](https://share.streamlit.io), clique em **New app**,
   selecione o repositório/branch e informe `streamlit_app.py` como main file.
4. Aguarde o build (a primeira execução instala `shapely`/`pyproj` e pode
   demorar alguns minutos).

**Atenção**: `SNV_202604A.kmz` tem ~49MB — está abaixo do limite rígido do
GitHub (100MB por arquivo), mas acima do limite de aviso (50MB). O push
funciona normalmente; o GitHub apenas exibirá um aviso recomendando Git LFS,
que pode ser ignorado.

**Os arquivos de `Inputs/` não são versionados** (estão no `.gitignore`) —
servem apenas como exemplos para teste local. Quem for usar o app publicado
deve enviar seus próprios arquivos pela interface.

## Filtro de BR (sidebar)

Carregar a malha SNV nacional completa é pesado (dezenas de milhares de
segmentos). Use o filtro de BR na barra lateral para restringir o
carregamento às rodovias de interesse (ex: `153`) — isso acelera bastante o
tempo de carregamento e reduz o uso de memória, algo especialmente
importante no Streamlit Community Cloud (recursos limitados). Deixar o
filtro vazio carrega o Brasil inteiro.

## Formatos de arquivo aceitos

### Pontos a Verificar (obrigatório)

- **TXT/CSV**: uma linha por ponto, `Latitude;Longitude` ou
  `Latitude,Longitude`. Linhas vazias e começadas com `#` são ignoradas.
- **JSON**: lista de objetos `{"lat": ..., "lon": ...}` (aceita também
  `latitude`/`longitude`/`lng`) ou lista de arrays `[lat, lon]`.

### Marcos Quilométricos (opcional)

- **KML/KMZ formato 1**: Placemarks Point com nome no padrão `BR-NNN KM-MMM`.
- **KML formato 2** (ex: `MQ_153.kml`): Placemarks Point com atributo
  `DESC` no padrão `km-NNN` (sem BR explícita — a BR é resolvida
  automaticamente projetando o marco sobre a malha SNV carregada).
- **KML/KMZ adicional**: tambem aceita `DESC` numerico puro (`71`), nome do
  Placemark numerico puro (`71`) e pista de BR em pasta/Document no padrao
  `MQ_040` ou `BR-040`. Sem pista de BR, a BR e resolvida pela malha SNV
  carregada; selecione a BR correta no filtro SNV.
- **TXT/CSV**: uma linha por marco, `Latitude;Longitude;KM` ou
  `Latitude,Longitude,KM` (BR também resolvida via SNV).
- **JSON**: lista de objetos `{"lat":..., "lon":..., "km":...}` ou lista de
  arrays `[lat, lon, km]`.

### Eixo da rodovia (opcional, requer Marcos)

- **KML ou KMZ** com o traçado (LineStrings), sem necessidade de atributos
  de BR/UF.

## Limitações herdadas

- Projeção sempre em UTM Zona 22S (EPSG:32722) — adequada para GO, MG, SP,
  MS, MT. Rodovias em outras regiões do Brasil terão erro de projeção maior.
- Rodovias federais que reiniciam a quilometragem em divisas estaduais (ex:
  BR-153 entre GO/MG) são tratadas corretamente na Camada 1 (SNV puro), mas
  Camadas 2/3 podem ter imprecisão na faixa próxima à divisa.
