"""
scraper_23f.py
==============
Scraper del archivo documental del 23F (https://23fbuscador.rtve.es)
 
Produce un CSV con estos campos:
  id, titulo, tipo, estado, modelo_ocr, resumen, palabras_clave,
  texto_completo, fecha_doc, anio_doc, mes_doc, dia_doc,
  actores, categoria
 
Notas de diseno:
  - fecha_doc: formato DD/MM/YYYY. Prioridad: parentesis en titulo >
    ultima fecha del titulo > primera fecha del resumen > primera
    fecha del texto completo. Rango valido 1900-1995. Acepta formatos
    "23 de febrero de 1981", "23-02-81", "24. FEB. 1961", "1.983".
  - Para 26 documentos sin fecha extraible automaticamente se aplican
    fechas manuales basadas en analisis del contenido.
  - actores: combina lista fija de protagonistas del 23F con extraccion
    de cargos/rangos militares de las palabras clave.
  - categoria: noche_23f | contexto_1981 | juicio_1982 | diplomatico |
    otro | sin_clasificar
 
Requisitos:
    pip install requests beautifulsoup4 pandas tqdm
 
Uso:
    python scraper_23f.py
"""
 
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw):
        return it
from datetime import datetime
from collections import Counter
 
# ---------------------------------------------------------------------------
# CONFIGURACION
# ---------------------------------------------------------------------------
BASE_URL   = "https://23fbuscador.rtve.es"
ID_INICIO  = 1859
OUTPUT_CSV = "dataset_23f.csv"
DELAY      = 1.0
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://23fbuscador.rtve.es/",
}
 
# ---------------------------------------------------------------------------
# TABLAS DE MESES
# ---------------------------------------------------------------------------
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
MESES_ABREV = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}
_MS = "|".join(MESES.keys())
 
# ---------------------------------------------------------------------------
# REGEX (compilados una sola vez)
# ---------------------------------------------------------------------------
# "23 de febrero de 1981"  o  "23 de febrero de 1.981"
_RE_LARGO = re.compile(
    r"(\d{1,2})\s+de\s+(" + _MS + r")\s+de\s+(\d[\d.]{2,4})",
    re.IGNORECASE,
)
# "23-02-81"  /  "23/02/1981"
_RE_CORTO = re.compile(r"\b(\d{1,2})[-/](\d{2})[-/](\d{2,4})\b")
# "febrero de 1981"
_RE_MES_ANIO = re.compile(
    r"(" + _MS + r")\s+de\s+(\d{4})",
    re.IGNORECASE,
)
# "(19 de febrero de 1982)"
_RE_PAREN_LARGO = re.compile(
    r"\((\d{1,2}\s+de\s+(?:" + _MS + r")\s+de\s+\d[\d.]{2,4})\)",
    re.IGNORECASE,
)
# "(junio 1981)"  o  "(junio de 1981)"
_RE_PAREN_MES = re.compile(
    r"\(((?:" + _MS + r")\s+(?:de\s+)?\d{4})\)",
    re.IGNORECASE,
)
# "24. FEB. 1961"
_ABREV_PAT = "|".join(MESES_ABREV.keys())
_RE_ABREV = re.compile(
    r"(\d{1,2})\.\s*(" + _ABREV_PAT + r")\.?\s*(\d{4})",
    re.IGNORECASE,
)
# Patron AGMAE / AGA (sin \b por el guion bajo en los titulos)
_RE_AGMAE = re.compile(r"AGMAE|AGA-\d{2}", re.IGNORECASE)
# Rangos/cargos militares y diplomaticos para extraccion de actores
_RE_CARGO = re.compile(
    r"(Coronel|General|Capit[a\u00e1]n|Teniente|Comandante|Almirante|Sargento|"
    r"Gral\.|Tcol\.|Ministro|Presidente|Embajador|"
    r"Secretario\s+de\s+Estado|C[o\u00f3]nsul(?!ado)|Encargado\s+de\s+Negocios)",
    re.IGNORECASE,
)
 
# ---------------------------------------------------------------------------
# ACTORES CONOCIDOS DEL 23F
# ---------------------------------------------------------------------------
ACTORES_FIJOS = [
    # Golpistas principales
    "Tejero", "Milans del Bosch", "Armada", "Cortina",
    "Torres Rojas", "Pardo Zancada", "Garcia Carres",
    # Cadena de mando leal
    "San Martin", "Manchado", "Gabeiras",
    "Quintana Lacaci", "Juste", "Cassinello",
    # Politicos
    "Adolfo Suarez", "Calvo Sotelo", "Gutierrez Mellado",
    "Felipe Gonzalez", "Carrillo", "Fraga",
    # Corona
    "Juan Carlos",
    # Instituciones clave
    "CESID", "JUJEM", "Consejo Supremo",
    # Actores diplomaticos y contexto amplio
    "Haig", "Perez-Llorca", "Carrero Blanco",
]
_RE_ACTORES = [
    (a, re.compile(r"\b" + re.escape(a) + r"\b", re.IGNORECASE))
    for a in ACTORES_FIJOS
]
# PREJUJEM contiene JUJEM pero no tiene word-boundary: patron especial
_RE_PREJUJEM = re.compile(r"PREJUJEM", re.IGNORECASE)
 
# ---------------------------------------------------------------------------
# FECHAS MANUALES
# Documentos cuyo texto no contiene fecha extraible automaticamente.
# Valores: (anio, mes, dia, categoria_forzada_o_None)
# Justificacion: analisis del contenido de cada documento.
# ---------------------------------------------------------------------------
FECHAS_MANUALES = {
    # Transcripciones de la noche del 23F (sin fecha en texto)
    1694: (1981,  2, 23, "noche_23f"),   # Conversacion Garcia Carres-Tejero en el Congreso
    1695: (1981,  2, 23, "noche_23f"),   # Conversacion Garcia Carres - otra persona
    1696: (1981,  2, 24, "noche_23f"),   # Conversaciones El Pardo, madrugada del 24
    1698: (1981,  2, 23, "noche_23f"),   # Manuscrito planificacion del golpe
    1700: (1981,  2, 23, "noche_23f"),   # Secuencia de hechos, EM Guardia Civil
    1788: (1981,  2, 23, "noche_23f"),   # Nota involucionismo politico, Brigada Info Valladolid
    1792: (1981,  2, 23, "noche_23f"),   # Relacion CESID - Jefe EM Capitania
    1793: (1981,  2, 23, "noche_23f"),   # Relacion CESID - PREJUJEM
    1794: (1981,  2, 23, "noche_23f"),   # Resumen actuacion Dept Defensa Interna
    1795: (1981,  2, 23, "noche_23f"),   # Actitud CESID ante incidentes en el Congreso
    # Proceso judicial (inicio antes de la vista oral de 1982)
    1721: (1981,  3,  5, "juicio_1982"), # Oficio toma de declaracion ("dia de ayer, 5 del corriente")
    1725: (1981,  3, 11, "juicio_1982"), # Procesamiento Milans ("11 de Marzo")
    1732: (1981,  4, 22, "juicio_1982"), # Procesamiento Torres Rojas (resumen: 22 abril 1981)
    1738: (1983,  2, 18, "juicio_1982"), # Levantamiento incomunicacion Tejero ("18 de febrero de 1.983")
    1741: (1981,  4, 30, "juicio_1982"), # Detencion Milans del Bosch (texto: 30 Abril 1981)
    1800: (1982,  3,  1, "juicio_1982"), # Comisiones militares en vista causa 2/81
    1807: (1982,  6,  1, "juicio_1982"), # Papel JUJEM en crisis post-sentencia
    # Contexto previo
    1712: (1981,  2, 23, "contexto_1981"), # Oficio zona Pais Vasco sobre Tejero
    # Diplomaticos con fechas históricas (fuera del golpe)
    1750: (1981,  2, 25, "diplomatico"), # Carta felicitacion al nuevo PM de Espana
    1752: (1931,  3,  4, "diplomatico"), # Doc AGMAE 4 marzo 1931
    1754: (1931,  3, 10, "diplomatico"), # Doc AGMAE 10 marzo 1931
    1760: (1961,  2, 24, "diplomatico"), # Assembleia Municipal Campo Maior, 24 FEB 1961
    1761: (1921,  3, 13, "diplomatico"), # Camara Municipal Campo Maior, 13 MAR 1921
    1773: (1981,  2, 24, "diplomatico"), # Mensaje Reina Isabel II a Juan Carlos I
}
 
# Correcciones puntuales de categoria (sin cambiar fecha)
CORRECCIONES_CAT = {
    1713: "juicio_1982",
    1703: "contexto_1981",
}
 
# ---------------------------------------------------------------------------
# FUNCIONES DE FECHA
# ---------------------------------------------------------------------------
 
def _limpiar_anio(s):
    """'1.983' -> 1983"""
    try:
        return int(str(s).replace(".", ""))
    except (ValueError, TypeError):
        return None
 
 
def _candidatos(texto, rmin=1900, rmax=1995):
    """
    Devuelve lista de (posicion, anio, mes, dia) para todas las fechas
    validas en el texto, buscando multiples formatos.
    """
    texto = str(texto)
    c = []
 
    for m in _RE_LARGO.finditer(texto):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = _limpiar_anio(m.group(3))
        me = MESES.get(ms, 0)
        if a and rmin <= a <= rmax and 1 <= d <= 31 and me:
            c.append((m.start(), a, me, d))
 
    for m in _RE_CORTO.finditer(texto):
        d, me, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a < 100:
            a += 1900
        if rmin <= a <= rmax and 1 <= me <= 12 and 1 <= d <= 31:
            c.append((m.start(), a, me, d))
 
    for m in _RE_MES_ANIO.finditer(texto):
        ms = m.group(1).lower()
        a = int(m.group(2))
        me = MESES.get(ms, 0)
        if rmin <= a <= rmax and me:
            c.append((m.start(), a, me, 1))
 
    for m in _RE_ABREV.finditer(texto):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = int(m.group(3))
        me = MESES_ABREV.get(ms, 0)
        if rmin <= a <= rmax and 1 <= d <= 31 and me:
            c.append((m.start(), a, me, d))
 
    return c
 
 
def _fmt(a, me, d):
    """Construye el dict de fecha: DD/MM/YYYY + campos numericos."""
    try:
        fecha_doc = datetime(a, me, d).strftime("%d/%m/%Y")
    except ValueError:
        fecha_doc = f"01/{me:02d}/{a}"
    return {
        "fecha_doc": fecha_doc,
        "anio_doc": a,
        "mes_doc": me,
        "dia_doc": d,
    }
 
 
_FV = {"fecha_doc": None, "anio_doc": None, "mes_doc": None, "dia_doc": None}
 
 
def _primera_fecha(texto):
    """Primera fecha por posicion en el texto."""
    c = _candidatos(texto)
    if not c:
        return _FV
    _, a, me, d = sorted(c, key=lambda x: x[0])[0]
    return _fmt(a, me, d)
 
 
def extraer_fecha(titulo, resumen, texto):
    """
    Extrae la fecha del documento con prioridad:
    1. Fecha entre parentesis al final del titulo
    2. Ultima fecha completa del titulo
    3. Primera fecha del resumen (500 chars)
    4. Primera fecha del texto completo
    """
    titulo = str(titulo)
 
    # 1a. Parentesis con fecha completa
    matches = list(_RE_PAREN_LARGO.finditer(titulo))
    if matches:
        c = _candidatos(matches[-1].group(1))
        if c:
            _, a, me, d = sorted(c, key=lambda x: x[0])[0]
            return _fmt(a, me, d)
 
    # 1b. Parentesis con mes-anio: "(junio 1981)" o "(junio de 1981)"
    matches = list(_RE_PAREN_MES.finditer(titulo))
    if matches:
        tp = matches[-1].group(1)
        m2 = re.search(
            r"(" + _MS + r")\s+(?:de\s+)?(\d{4})", tp, re.IGNORECASE
        )
        if m2:
            me = MESES.get(m2.group(1).lower(), 0)
            a = int(m2.group(2))
            if 1900 <= a <= 1995 and me:
                return _fmt(a, me, 1)
 
    # 2. Ultima fecha completa del titulo (dd de mes de yyyy o 24. FEB. 1961)
    cl = []
    for m in _RE_LARGO.finditer(titulo):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = _limpiar_anio(m.group(3))
        me = MESES.get(ms, 0)
        if a and 1900 <= a <= 1995 and 1 <= d <= 31 and me:
            cl.append((m.start(), a, me, d))
    for m in _RE_ABREV.finditer(titulo):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = int(m.group(3))
        me = MESES_ABREV.get(ms, 0)
        if 1900 <= a <= 1995 and 1 <= d <= 31 and me:
            cl.append((m.start(), a, me, d))
    if cl:
        _, a, me, d = sorted(cl, key=lambda x: x[0])[-1]
        return _fmt(a, me, d)
 
    # Ultimo mes-anio del titulo
    cm = []
    for m in _RE_MES_ANIO.finditer(titulo):
        ms = m.group(1).lower()
        a = int(m.group(2))
        me = MESES.get(ms, 0)
        if 1900 <= a <= 1995 and me:
            cm.append((m.start(), a, me))
    if cm:
        _, a, me = sorted(cm, key=lambda x: x[0])[-1]
        return _fmt(a, me, 1)
 
    # 3. Primera fecha del resumen
    f = _primera_fecha(str(resumen)[:500])
    if f["fecha_doc"]:
        return f
 
    # 4. Primera fecha del texto completo
    return _primera_fecha(str(texto))
 
 
# ---------------------------------------------------------------------------
# FUNCIONES DE ACTORES
# ---------------------------------------------------------------------------
 
_EXCLUIR_ACTOR = re.compile(
    r"no\s+identificad|no\s+consta|desconocid|personas?\s+no|"
    r"fuerzas?\s+arm|ejército|ejercito|militares\b|implicados\b|"
    r"procesados\b|acusados\b|centrales?\s+sindical|partidos?\s+pol|"
    r"guardia\s+civil|estado\s+mayor|direcci[oó]n\s+general|"
    r"al\s+mando\s+de|jefatura|departamento|ministerio\s+de|"
    r"cuerpo\s+de|policia\s+nacional|tribunal\b|congreso\b",
    re.IGNORECASE,
)
# Cargo completamente solo (sin nombre): ignorar
_RE_SOLO_CARGO = re.compile(
    r"^(Coronel|General|Capit[aá]n|Teniente|Comandante|Almirante|Sargento|"
    r"Ministro|Presidente|Embajador|Secretario\s+de\s+Estado|"
    r"Encargado\s+de\s+Negocios)$",
    re.IGNORECASE,
)
 
def extraer_actores(titulo, resumen, texto, palabras_clave):
    """
    Combina tres estrategias:
    1. Lista fija de protagonistas del 23F (busqueda en titulo+resumen+kws+texto)
    2. Entradas KW formato "Nombre:Cargo" — se extrae el Nombre
    3. Entradas KW (<= 60 chars) con cargo/rango + contenido adicional (el nombre)
    """
    todo = "{} {} {} {}".format(titulo, resumen, palabras_clave, str(texto)[:3000])
    encontrados = set()
 
    # Estrategia 1: lista fija
    for actor, patron in _RE_ACTORES:
        if patron.search(todo):
            encontrados.add(actor)
 
    # PREJUJEM / PREJUJEN como alias de JUJEM
    if _RE_PREJUJEM.search(todo) or re.search(r"PREJUJEN", todo, re.IGNORECASE):
        encontrados.add("JUJEM")
 
    # S.M. / Su Majestad como alias de Juan Carlos
    if re.search(r"\bS\.M\.\b|Su\s+Majestad", todo, re.IGNORECASE):
        encontrados.add("Juan Carlos")
 
    if palabras_clave:
        for kw in str(palabras_clave).split(" | "):
            kw = kw.strip()
            if not kw or len(kw) < 4:
                continue
            if _EXCLUIR_ACTOR.search(kw):
                continue
            if _RE_SOLO_CARGO.match(kw):
                continue
 
            # Estrategia 2: "Nombre:Cargo" — extraer solo el nombre
            if ":" in kw:
                nombre, _, rol = kw.partition(":")
                nombre = nombre.strip()
                rol = rol.strip()
                if nombre and _RE_CARGO.search(rol) and 4 <= len(nombre) <= 50:
                    encontrados.add(nombre)
 
            # Estrategia 3: KW con cargo + algo mas fuera del cargo
            elif _RE_CARGO.search(kw) and len(kw) <= 60:
                cargo_m = _RE_CARGO.search(kw)
                resto = (kw[:cargo_m.start()] + kw[cargo_m.end():]).strip()
                if len(resto) >= 3:  # hay un nombre ademas del cargo
                    encontrados.add(kw)
 
    return " | ".join(sorted(encontrados))
 
 
# ---------------------------------------------------------------------------
# LIMPIEZA DE PALABRAS CLAVE
# ---------------------------------------------------------------------------
 
def limpiar_kws(kws_raw):
    """Filtra numeros sueltos, entradas cortas/largas y duplicados."""
    vistos = set()
    limpias = []
    for kw in kws_raw:
        kw = kw.strip()
        if not kw:
            continue
        if re.match(r"^[\d\s.,;:\-/]+$", kw):
            continue
        if len(kw) < 3 or len(kw) > 80:
            continue
        norm = kw.lower()
        if norm in vistos:
            continue
        vistos.add(norm)
        limpias.append(kw)
    return " | ".join(limpias)
 
 
# ---------------------------------------------------------------------------
# CATEGORIZACION
# ---------------------------------------------------------------------------
 
def categorizar(row):
    """
    Asigna categoria basandose en titulo, resumen y fecha.
    NaN-safe: comprueba anio == anio antes de int() (NaN != NaN).
    """
    tit = str(row.get("titulo", "")).lower()
    res = str(row.get("resumen", "")).lower()
    todo = tit + " " + res
 
    # Diplomaticos (AGMAE / AGA en el titulo)
    if _RE_AGMAE.search(str(row.get("titulo", ""))):
        return "diplomatico"
 
    # Juicio 1982-1984
    if "vista oral" in tit and any(y in tit for y in ["1982", "1983", "1984"]):
        return "juicio_1982"
    kw_juicio = [
        "informaci\u00f3n integrada", "informacion integrada",
        "acotaciones al desarrollo del juicio",
        "comentarios sobre la sentencia del 23-f",
        "comisiones militares en la vista",
    ]
    if any(k in todo for k in kw_juicio):
        return "juicio_1982"
 
    # Noche del golpe y 72h posteriores
    kw_noche = [
        "dentro del congreso", "asalto al congreso",
        "el d\u00eda 23-f", "el dia 23-f", "23-f informando",
        "esposa de tejero", "garcia carres y tejero",
        "el pardo (24 de febrero",
        "planificaci\u00f3n del golpe", "planificacion del golpe",
        "secuencia parcial de los hechos",
        "ocupaci\u00f3n del palacio del congreso",
        "ocupacion del palacio del congreso",
        "situaci\u00f3n actual en las distintas regiones",
        "situacion actual en las distintas regiones",
        "relato de los sucesos de los d\u00edas 23 y 24",
        "relato de los sucesos de los dias 23 y 24",
        "incidentes en el congreso",
        "actitud del cesid ante la situaci\u00f3n",
        "actitud del cesid ante la situacion",
        "actuaci\u00f3n del departamento de defensa interna",
        "actuacion del departamento de defensa interna",
        "participaci\u00f3n de miembros de la aome",
        "participacion de miembros de la aome",
        "relaci\u00f3n cesid", "relacion cesid", "prejujem",
        "gui\u00f3n que sirvi\u00f3 de base", "guion que sirvio de base",
        "secretario haig", "todman",
        "solidaridad con el pueblo espa\u00f1ol",
        "solidaridad con el pueblo espanol",
        "intento de golpe de estado en espa\u00f1a",
        "intento de golpe de estado en espana",
        "defensa de las instituciones democr\u00e1ticas",
        "defensa de las instituciones democraticas",
        "posible golpe de estado",
        "involucionismo pol\u00edtico provocado",
        "involucionismo politico provocado",
        "papel de la jujem en la crisis",
        "transcripci\u00f3n de conversaci\u00f3n telef\u00f3nica",
        "transcripcion de conversacion telefonica",
    ]
    if any(k in todo for k in kw_noche):
        return "noche_23f"
 
    # Por fecha (NaN-safe)
    anio = row.get("anio_doc")
    mes = row.get("mes_doc")
    if anio is not None and anio == anio:
        anio = int(anio)
        if anio == 1981 and mes is not None and mes == mes and int(mes) == 2:
            return "noche_23f"
        if anio == 1981:
            return "contexto_1981"
        if anio in (1982, 1983, 1984):
            return "juicio_1982"
        if anio:
            return "otro"
 
    # Por anio en el titulo
    anios_tit = re.findall(r"\b(198\d)\b", tit)
    if anios_tit:
        a = int(anios_tit[0])
        if a in (1982, 1983, 1984):
            return "juicio_1982"
        if a == 1981:
            return "contexto_1981"
        return "otro"
 
    return "sin_clasificar"
 
 
# ---------------------------------------------------------------------------
# PARSEO DE UN DOCUMENTO HTML
# ---------------------------------------------------------------------------
 
def parsear_documento(html, doc_id):
    """Extrae todos los campos de un documento a partir de su HTML."""
    soup = BeautifulSoup(html, "html.parser")
 
    doc = {
        "id": doc_id,
        "titulo": "",
        "tipo": "",
        "estado": "",
        "modelo_ocr": "",
        "resumen": "",
        "palabras_clave": "",
        "texto_completo": "",
        "fecha_doc": None,
        "anio_doc": None,
        "mes_doc": None,
        "dia_doc": None,
        "actores": "",
        "categoria": "",
        # auxiliares para navegacion (se eliminan antes de guardar)
        "_id_anterior": None,
        "_id_siguiente": None,
    }
 
    # Titulo
    h2 = soup.find("h2")
    if h2:
        doc["titulo"] = h2.get_text(strip=True)
 
    # Metadatos del grid
    grid = soup.find("div", class_="detail-grid")
    if grid:
        for div in grid.find_all("div"):
            txt = div.get_text(separator=" ", strip=True)
            if "Tipo:" in txt:
                doc["tipo"] = txt.replace("Tipo:", "").strip()
            elif "Estado:" in txt:
                doc["estado"] = txt.replace("Estado:", "").strip()
            elif "Modelo:" in txt:
                doc["modelo_ocr"] = txt.replace("Modelo:", "").strip()
 
    # Resumen
    tbox = soup.find("p", class_="text-box")
    if tbox:
        doc["resumen"] = tbox.get_text(separator=" ", strip=True)
 
    # Palabras clave
    kws_raw = []
    for h3 in soup.find_all("h3"):
        if any(x in h3.get_text().lower() for x in ["palabra", "clave", "keyword", "tag"]):
            section = h3.find_parent("section") or h3.find_next_sibling()
            if section:
                for t in section.find_all(["span", "a", "li", "p"]):
                    txt = t.get_text(strip=True)
                    if txt and txt not in ("Palabras clave", "Keywords"):
                        kws_raw.append(txt)
            break
    if not kws_raw:
        for t in soup.find_all(
            ["span", "a"],
            class_=lambda x: x and any(
                k in x.lower() for k in ["tag", "keyword", "label", "badge", "chip"]
            ),
        ):
            txt = t.get_text(strip=True)
            if txt:
                kws_raw.append(txt)
    doc["palabras_clave"] = limpiar_kws(kws_raw)
 
    # Texto completo
    pre = soup.find("pre")
    if pre:
        doc["texto_completo"] = pre.get_text(separator="\n", strip=True)
    else:
        for article in soup.find_all("article"):
            h3 = article.find("h3")
            if h3 and any(
                x in h3.get_text().lower()
                for x in ["texto", "transcri", "ocr", "completo"]
            ):
                doc["texto_completo"] = article.get_text(separator="\n", strip=True)
                break
 
    # Navegacion anterior/siguiente
    for link in soup.find_all("a", class_="nav-doc-button"):
        href = link.get("href", "")
        label = link.get_text(strip=True).lower()
        m = re.search(r"/document/ocr/(\d+)", href)
        if m:
            nid = int(m.group(1))
            if "anterior" in label:
                doc["_id_anterior"] = nid
            elif "siguiente" in label:
                doc["_id_siguiente"] = nid
 
    # Fecha
    fecha = extraer_fecha(doc["titulo"], doc["resumen"], doc["texto_completo"])
    doc.update(fecha)
 
    # Actores
    doc["actores"] = extraer_actores(
        doc["titulo"], doc["resumen"],
        doc["texto_completo"], doc["palabras_clave"],
    )
 
    return doc
 
 
# ---------------------------------------------------------------------------
# DESCUBRIMIENTO DE IDs
# ---------------------------------------------------------------------------
 
def obtener_todos_los_ids(session):
    """Navega la web siguiendo los botones Anterior/Siguiente."""
    print("\n[1/3] Descubriendo IDs...")
    visitados = set()
    por_visitar = {ID_INICIO}
 
    # Intentar obtener mas IDs desde el listado principal
    try:
        r = session.get(BASE_URL + "/?page_size=25&page=1", timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for link in soup.find_all("a", href=re.compile(r"/document/ocr/\d+")):
            m = re.search(r"/document/ocr/(\d+)", link["href"])
            if m:
                por_visitar.add(int(m.group(1)))
        print("  -> {} IDs en el listado".format(len(por_visitar)))
    except Exception as e:
        print("  [!] Listado no accesible: {}".format(e))
 
    pbar = tqdm(desc="  Descubriendo", unit=" docs")
    while por_visitar:
        doc_id = por_visitar.pop()
        if doc_id in visitados:
            continue
        url = "{}/document/ocr/{}?page_size=25&page=1".format(BASE_URL, doc_id)
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                visitados.add(doc_id)
                soup = BeautifulSoup(r.text, "html.parser")
                for link in soup.find_all("a", class_="nav-doc-button"):
                    m = re.search(r"/document/ocr/(\d+)", link.get("href", ""))
                    if m:
                        nid = int(m.group(1))
                        if nid not in visitados:
                            por_visitar.add(nid)
                pbar.update(1)
            else:
                visitados.add(doc_id)
        except Exception as e:
            print("\n  [ERROR] ID {}: {}".format(doc_id, e))
        time.sleep(DELAY * 0.5)
    pbar.close()
 
    print("  -> Total: {} IDs".format(len(visitados)))
    return sorted(visitados)
 
 
# ---------------------------------------------------------------------------
# SCRAPING DE UN DOCUMENTO
# ---------------------------------------------------------------------------
 
def scrape_doc(session, doc_id):
    url = "{}/document/ocr/{}?page_size=25&page=1".format(BASE_URL, doc_id)
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200:
            return parsear_documento(r.text, doc_id)
    except Exception as e:
        print("\n  [ERROR] ID {}: {}".format(doc_id, e))
    return None
 
 
# ---------------------------------------------------------------------------
# POST-PROCESADO DEL DATAFRAME
# ---------------------------------------------------------------------------
 
def aplicar_correcciones(df):
    """
    Aplica fechas manuales y correcciones de categoria para los documentos
    que no tienen fecha extraible automaticamente.
    """
    n_fechas = 0
    n_cats = 0
 
    for doc_id, (a, me, d, cat) in FECHAS_MANUALES.items():
        mask = df["id"] == doc_id
        if not mask.any():
            continue
        try:
            fd = datetime(a, me, d).strftime("%d/%m/%Y")
        except ValueError:
            fd = "01/{:02d}/{}".format(me, a)
        df.loc[mask, "fecha_doc"] = fd
        df.loc[mask, "anio_doc"] = a
        df.loc[mask, "mes_doc"] = me
        df.loc[mask, "dia_doc"] = d
        n_fechas += 1
        if cat:
            df.loc[mask, "categoria"] = cat
            n_cats += 1
 
    for doc_id, cat in CORRECCIONES_CAT.items():
        mask = df["id"] == doc_id
        if mask.any():
            df.loc[mask, "categoria"] = cat
            n_cats += 1
 
    print("  Fechas manuales aplicadas: {}".format(n_fechas))
    print("  Categorias corregidas: {}".format(n_cats))
    return df
 
 
def recuperar_fechas(df):
    """
    Para los docs que aun no tienen fecha, intenta extraer del texto
    completo con rango ampliado; si no, usa el anio mas mencionado.
    """
    sin = df[df["fecha_doc"].isna()]
    n = 0
    for idx, row in sin.iterrows():
        f = extraer_fecha(
            str(row["titulo"]), str(row["resumen"]), str(row["texto_completo"])
        )
        if f["fecha_doc"]:
            for k, v in f.items():
                df.loc[idx, k] = v
            n += 1
        else:
            anios = re.findall(r"\b(19[789]\d)\b", str(row["texto_completo"]))
            if anios:
                a = int(Counter(anios).most_common(1)[0][0])
                df.loc[idx, "fecha_doc"] = "01/01/{}".format(a)
                df.loc[idx, "anio_doc"] = a
                n += 1
    print("  Fechas recuperadas de texto: {}".format(n))
    return df
 
 
def reclasificar(df):
    """Reclasifica los sin_clasificar que ahora tienen fecha."""
    n = 0
    for idx, row in df[df["categoria"] == "sin_clasificar"].iterrows():
        if _RE_AGMAE.search(str(row.get("titulo", ""))):
            df.loc[idx, "categoria"] = "diplomatico"
            n += 1
            continue
        anio = row.get("anio_doc")
        if anio is not None and anio == anio:
            anio = int(anio)
            mes = row.get("mes_doc")
            if anio == 1981 and mes is not None and mes == mes and int(mes) == 2:
                df.loc[idx, "categoria"] = "noche_23f"
            elif anio == 1981:
                df.loc[idx, "categoria"] = "contexto_1981"
            elif anio in (1982, 1983, 1984):
                df.loc[idx, "categoria"] = "juicio_1982"
            else:
                df.loc[idx, "categoria"] = "otro"
            n += 1
    print("  Reclasificados: {}".format(n))
    return df
 
 
# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
 
def main():
    sep = "=" * 60
    print(sep)
    print("  SCRAPER 23F")
    print(sep)
 
    session = requests.Session()
    session.headers.update(HEADERS)
 
    # 1. Descubrir IDs
    ids = obtener_todos_los_ids(session)
    if not ids:
        print("[ERROR] No se encontraron IDs.")
        return
 
    # 2. Descargar documentos
    print("\n[2/3] Descargando {} documentos...".format(len(ids)))
    docs = []
    for doc_id in tqdm(ids, desc="  Descargando"):
        doc = scrape_doc(session, doc_id)
        if doc:
            docs.append(doc)
            if len(docs) % 25 == 0:
                pd.DataFrame(docs).to_csv(
                    "dataset_23f_parcial.csv", index=False, encoding="utf-8-sig"
                )
        time.sleep(DELAY)
 
    # 3. Construir DataFrame
    print("\n[3/3] Procesando y guardando...")
    df = pd.DataFrame(docs)
    df = df.drop(
        columns=["_id_anterior", "_id_siguiente", "n_paginas"],
        errors="ignore",
    )
 
    # Categorizacion automatica
    df["categoria"] = df.apply(categorizar, axis=1)
 
    # Correcciones manuales
    df = aplicar_correcciones(df)
 
    # Recuperar fechas restantes
    df = recuperar_fechas(df)
 
    # Reclasificar sin_clasificar
    df = reclasificar(df)
 
    # Fusionar categorias "otro_XXXX" en "otro"
    df["categoria"] = df["categoria"].str.replace(
        r"^otro_\d+$", "otro", regex=True
    )
 
    # Tipos numericos
    for col in ["anio_doc", "mes_doc", "dia_doc"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].isna().sum() == 0:
            df[col] = df[col].astype(int)
 
    # Guardar
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
 
    # Resumen
    n = len(df)
    print("\n" + sep)
    print("  COMPLETADO: {} documentos".format(n))
    print(sep)
    print("\nDistribucion por categoria:")
    for cat, cnt in df["categoria"].value_counts().items():
        print("  {}: {}".format(cat, cnt))
    n_fecha = df["fecha_doc"].notna().sum()
    n_act = (df["actores"].notna() & (df["actores"].str.len() > 0)).sum()
    n_sc = (df["categoria"] == "sin_clasificar").sum()
    print("\nFechas (DD/MM/YYYY): {}/{}".format(n_fecha, n))
    print("Con actores: {}/{}".format(n_act, n))
    print("Sin clasificar: {}".format(n_sc))
    print("\nColumnas: {}".format(list(df.columns)))
    print("Guardado en: {}".format(OUTPUT_CSV))
 
    print("\nMuestra:")
    cols_muestra = ["id", "titulo", "fecha_doc", "anio_doc", "actores", "categoria"]
    print(df[cols_muestra].head(10).to_string())
 
 
if __name__ == "__main__":
    main()