"""
Scraper 23F - Versión Final
============================
Extrae los 167 documentos desclasificados del 23F de RTVE y los
categoriza automáticamente en:
  - noche_23f     : comunicaciones y documentos de la noche del golpe y reacción inmediata
  - contexto_1981 : documentos de 1981 posteriores al golpe
  - juicio_1982   : sesiones del juicio militar de 1982
  - otro_XXXX     : documentos de otros años
  - sin_clasificar: sin información suficiente para clasificar

Requisitos:
    pip install requests beautifulsoup4 pandas tqdm

Uso:
    python scraper_23f_v2.py
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
from tqdm import tqdm
from datetime import datetime

BASE_URL   = "https://23fbuscador.rtve.es"
ID_INICIO  = 1859
OUTPUT_CSV = "dataset_23f.csv"
DELAY      = 1.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://23fbuscador.rtve.es/",
}

MESES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
}

# Keywords inequívocas de juicio (buscan en título + resumen)
KEYWORDS_JUICIO = [
    'acotaciones al desarrollo del juicio',
    'comentarios sobre la sentencia del 23-f',
    'comisiones militares en la vista de la causa',
    'información integrada',
]

# Keywords inequívocas de noche del golpe (buscan en título + resumen)
KEYWORDS_NOCHE = [
    # Comunicaciones en tiempo real
    'dentro del congreso', 'asalto al congreso',
    'el día 23-f', '23-f informando',
    'esposa de tejero', 'garcia carres y tejero',
    'el pardo (24 de febrero', 'planificación del golpe',
    'secuencia parcial de los hechos del asalto',
    'ocupación del palacio del congreso',
    'situación actual en las distintas regiones',
    'relato de los sucesos de los días 23 y 24',
    # CESID y respuesta institucional inmediata
    'incidentes en el congreso',
    'actitud del cesid ante la situación',
    'actuación del departamento de defensa interna',
    'participación de miembros de la aome',
    'relación cesid', 'prejujem',
    'guión que sirvió de base para la reunión',
    'papel de la jujem en la crisis',
    # Reacción internacional inmediata
    'secretario haig', 'todman',
    'solidaridad con el pueblo español',
    'intento de golpe de estado en españa',
    'intento de golpe contra la democracia',
    'intento de cambio violento',
    'golpe fascista', 'golpe de estado del 23',
    'secuestro en el congreso',
    'toma del parlamento español',
    
    # Procesamiento inmediato de implicados (marzo 1981)
    'hechos ocurridos los días 23 y 24 de febrero',
    'causa relacionada con los hechos ocurridos',
    'sucesos de febrero de ese año',
    'arresto impuesto tras los sucesos',
    # Planificación y antecedentes
    'posible golpe de estado',
    'involucionismo político provocado',
    'transcripción de conversación telefónica de garcía carres',
    'defensa de las instituciones democráticas',
]

# IDs hardcoded para casos ambiguos conocidos
IDS_JUICIO   = {1786, 1713, 1710, 1737, 1739, 1742, 1715, 1803, 1783, 1790}
IDS_CONTEXTO = {1703, 1778, 1779, 1787, 1709, 1735, 1736, 1777, 1780, 1784, 1797, 1707}

session = requests.Session()
session.headers.update(HEADERS)


def extraer_fecha(titulo, palabras_clave, resumen):
    """
    Extrae año y mes del documento buscando en tres fuentes por orden de fiabilidad:
    1. Título (más fiable - indica de qué trata el doc)
    2. Palabras clave
    3. Primera fecha mencionada en el resumen (con cautela - puede ser fecha histórica referenciada)
    """
    año, mes, dia = None, None, None

    patron_completo = re.compile(
        r'\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|'
        r'septiembre|octubre|noviembre|diciembre)\s+de\s+(\d{4})\b',
        re.IGNORECASE
    )
    patron_corto = re.compile(r'(\d{1,2})[-/](\d{2})[-/](\d{2,4})')

    def buscar_en(texto):
        texto = str(texto)
        # Fecha completa con día
        m = patron_completo.search(texto)
        if m:
            d = int(m.group(1))
            ms = MESES[m.group(2).lower()]
            a = int(m.group(3))
            if 1975 <= a <= 1990 and 1 <= d <= 31:
                return a, ms, d
        # Formato corto
        m = patron_corto.search(texto)
        if m:
            d = int(m.group(1))
            ms = int(m.group(2))
            a = int(m.group(3))
            if a < 100: a += 1900
            if 1975 <= a <= 1990 and 1 <= ms <= 12 and 1 <= d <= 31:
                return a, ms, d
        # Solo año
        años = re.findall(r'\b(197\d|198\d)\b', texto)
        if años:
            return int(años[0]), None, None
        return None, None, None

    # 1. Título
    año, mes, dia = buscar_en(titulo)
    if año:
        fecha_iso = f"{año}-{mes or 1:02d}-{dia or 1:02d}"
        return {"fecha_iso": fecha_iso, "año_doc": año, "mes_doc": mes, "dia_doc": dia}

    # 2. Palabras clave
    año, mes, dia = buscar_en(palabras_clave)
    if año:
        fecha_iso = f"{año}-{mes or 1:02d}-{dia or 1:02d}"
        return {"fecha_iso": fecha_iso, "año_doc": año, "mes_doc": mes, "dia_doc": dia}

    # 3. Resumen (primeras 400 chars para evitar fechas históricas referenciadas)
    # Buscamos la primera fecha mencionada, que suele ser la del documento
    resumen_corto = str(resumen)[:400]
    año, mes, dia = buscar_en(resumen_corto)
    if año:
        fecha_iso = f"{año}-{mes or 1:02d}-{dia or 1:02d}"
        return {"fecha_iso": fecha_iso, "año_doc": año, "mes_doc": mes, "dia_doc": dia}

    return {"fecha_iso": None, "año_doc": None, "mes_doc": None, "dia_doc": None}


def categorizar(doc_id, titulo, resumen, palabras_clave, año_doc, mes_doc):
    """
    Categoriza un documento con la siguiente lógica:
    1. IDs hardcoded para casos conocidos
    2. Keywords de juicio en título+resumen
    3. Keywords de noche del golpe en título+resumen
    4. Fecha del documento
    5. Año en el título como último recurso
    """
    titulo_l  = str(titulo).lower()
    resumen_l = str(resumen).lower()
    todo = titulo_l + ' ' + resumen_l

    # 1. IDs hardcoded
    if doc_id in IDS_JUICIO:   return 'juicio_1982'
    if doc_id in IDS_CONTEXTO: return 'contexto_1981'

    # 2. Juicio por título explícito
    if 'vista oral' in titulo_l and any(a in titulo_l for a in ['1982','1983','1984']):
        return 'juicio_1982'
    if any(kw in todo for kw in KEYWORDS_JUICIO):
        return 'juicio_1982'

    # 3. Noche del golpe por keywords en título+resumen
    if any(kw in todo for kw in KEYWORDS_NOCHE):
        return 'noche_23f'

    # 4. Por fecha del documento
    if año_doc:
        año = int(año_doc)
        mes = int(mes_doc) if mes_doc else None
        if año == 1981 and mes in [2, 3]:  # feb-mar 1981 → reacción inmediata
            return 'noche_23f'
        if año == 1981:
            return 'contexto_1981'
        if año == 1982:
            return 'juicio_1982'
        return f'otro_{año}'

    # 5. Año en el título como último recurso
    años = re.findall(r'\b(197\d|198\d)\b', titulo_l)
    if años:
        a = int(años[0])
        if a == 1982: return 'juicio_1982'
        if a == 1981: return 'contexto_1981'
        return f'otro_{a}'

    return 'sin_clasificar'


def parsear_documento(html, doc_id):
    soup = BeautifulSoup(html, "html.parser")
    doc = {
        "id": doc_id, "titulo": "", "tipo": "", "estado": "",
        "n_paginas": "", "modelo_ocr": "", "resumen": "",
        "palabras_clave": "", "texto_completo": "",
        "fecha_iso": None, "año_doc": None, "mes_doc": None, "dia_doc": None,
        "actores": "", "categoria": "",
        "id_anterior": None, "id_siguiente": None,
    }

    h2 = soup.find("h2")
    if h2: doc["titulo"] = h2.get_text(strip=True)

    detail_grid = soup.find("div", class_="detail-grid")
    if detail_grid:
        for div in detail_grid.find_all("div"):
            t = div.get_text(separator=" ", strip=True)
            if "Tipo:"    in t: doc["tipo"]        = t.replace("Tipo:", "").strip()
            elif "Estado:" in t: doc["estado"]     = t.replace("Estado:", "").strip()
            elif "Páginas:" in t: doc["n_paginas"] = t.replace("Páginas:", "").strip()
            elif "Modelo:" in t: doc["modelo_ocr"] = t.replace("Modelo:", "").strip()

    text_box = soup.find("p", class_="text-box")
    if text_box: doc["resumen"] = text_box.get_text(separator=" ", strip=True)

    kws = []
    for h3 in soup.find_all("h3"):
        if any(x in h3.get_text().lower() for x in ["palabra", "clave", "keyword"]):
            sec = h3.find_parent("section") or h3.find_next_sibling()
            if sec:
                for t in sec.find_all(["span", "a", "li", "p"]):
                    txt = t.get_text(strip=True)
                    if txt and txt not in ["Palabras clave", "Keywords"]:
                        kws.append(txt)
            break
    if not kws:
        tags = soup.find_all(["span","a"], class_=lambda x: x and any(
            k in x.lower() for k in ["tag","keyword","label","badge","chip"]))
        kws = [t.get_text(strip=True) for t in tags if t.get_text(strip=True)]
    doc["palabras_clave"] = " | ".join(kws)

    pre = soup.find("pre")
    if pre:
        doc["texto_completo"] = pre.get_text(separator="\n", strip=True)
    else:
        for art in soup.find_all("article"):
            h3 = art.find("h3")
            if h3 and any(x in h3.get_text().lower() for x in ["texto","transcri","ocr","completo"]):
                doc["texto_completo"] = art.get_text(separator="\n", strip=True)
                break

    for link in soup.find_all("a", class_="nav-doc-button"):
        href = link.get("href", "")
        txt  = link.get_text(strip=True).lower()
        m = re.search(r"/document/ocr/(\d+)", href)
        if m:
            nid = int(m.group(1))
            if "anterior" in txt:    doc["id_anterior"] = nid
            elif "siguiente" in txt: doc["id_siguiente"] = nid

    # Extraer fecha buscando en título, palabras_clave Y resumen
    fecha = extraer_fecha(doc["titulo"], doc["palabras_clave"], doc["resumen"])
    doc.update(fecha)

    # Extraer actores
    patron_actor = re.compile(
        r'(Coronel|General|Capitán|Teniente|Comandante|Almirante|'
        r'Gral\.|Tcol\.|Ayte\.|Tejero|Milans|Armada|Cortina|Suárez)',
        re.IGNORECASE
    )
    fuentes = doc["palabras_clave"].split(" | ") if doc["palabras_clave"] else []
    doc["actores"] = " | ".join([k for k in fuentes if patron_actor.search(k)])

    # Categorizar usando título + resumen + fecha
    doc["categoria"] = categorizar(
        doc_id, doc["titulo"], doc["resumen"],
        doc["palabras_clave"], doc["año_doc"], doc["mes_doc"]
    )

    return doc


def obtener_todos_los_ids():
    print("\n[1/3] Descubriendo IDs...")
    ids_visitados   = set()
    ids_por_visitar = {ID_INICIO}

    try:
        r = session.get(f"{BASE_URL}/?page_size=25&page=1", timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for link in soup.find_all("a", href=re.compile(r"/document/ocr/\d+")):
            m = re.search(r"/document/ocr/(\d+)", link["href"])
            if m: ids_por_visitar.add(int(m.group(1)))
        print(f"  → {len(ids_por_visitar)} IDs iniciales")
    except Exception as e:
        print(f"  [!] {e}")

    pbar = tqdm(desc="  Descubriendo", unit=" docs")
    while ids_por_visitar:
        doc_id = ids_por_visitar.pop()
        if doc_id in ids_visitados: continue
        try:
            r = session.get(
                f"{BASE_URL}/document/ocr/{doc_id}?page_size=25&page=1", timeout=15)
            if r.status_code == 200:
                ids_visitados.add(doc_id)
                soup = BeautifulSoup(r.text, "html.parser")
                for link in soup.find_all("a", class_="nav-doc-button"):
                    m = re.search(r"/document/ocr/(\d+)", link.get("href",""))
                    if m:
                        nid = int(m.group(1))
                        if nid not in ids_visitados:
                            ids_por_visitar.add(nid)
                pbar.update(1)
            else:
                ids_visitados.add(doc_id)
        except Exception as e:
            print(f"\n  [ERROR] {doc_id}: {e}")
        time.sleep(DELAY * 0.5)
    pbar.close()
    print(f"  → {len(ids_visitados)} IDs totales")
    return sorted(ids_visitados)


def scrape_documento(doc_id):
    try:
        r = session.get(
            f"{BASE_URL}/document/ocr/{doc_id}?page_size=25&page=1", timeout=15)
        if r.status_code == 200:
            return parsear_documento(r.text, doc_id)
    except Exception as e:
        print(f"\n  [ERROR] {doc_id}: {e}")
    return None


def main():
    print("=" * 60)
    print("  SCRAPER 23F - Dataset con Categorización Automática")
    print("=" * 60)

    ids = obtener_todos_los_ids()
    if not ids:
        print("[ERROR] No se encontraron IDs.")
        return

    print(f"\n[2/3] Extrayendo {len(ids)} documentos...")
    documentos = []
    for doc_id in tqdm(ids, desc="  Descargando"):
        doc = scrape_documento(doc_id)
        if doc:
            documentos.append(doc)
            if len(documentos) % 25 == 0:
                pd.DataFrame(documentos).to_csv(
                    "dataset_23f_parcial.csv", index=False, encoding="utf-8-sig")
        time.sleep(DELAY)

    print(f"\n[3/3] Guardando...")
    df = pd.DataFrame(documentos)
    df = df.drop(columns=["id_anterior", "id_siguiente"], errors="ignore")
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n{'='*60}")
    print(f"  COMPLETADO: {len(df)} documentos")
    print(f"\n  Categorías:")
    for cat, n in df['categoria'].value_counts().items():
        print(f"    {cat:<20} {n}")
    print(f"\n  Fechas extraídas: {df['fecha_iso'].notna().sum()}/{len(df)}")
    print(f"  Guardado en: {OUTPUT_CSV}")
    print(f"{'='*60}")

    print("\n  Documentos noche_23f:")
    for _, row in df[df['categoria'] == 'noche_23f'].iterrows():
        print(f"    [{row['id']}] {str(row['titulo'])[:80]}")


if __name__ == "__main__":
    main()
