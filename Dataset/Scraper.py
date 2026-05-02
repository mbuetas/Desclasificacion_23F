"""
scraper_23f_v2.py
=================
Scraper del archivo documental del 23F (https://23fbuscador.rtve.es)

Produce un CSV con estos campos:
  id, titulo, tipo, estado, modelo_ocr, resumen, palabras_clave,
  texto_completo, fecha_doc, anio_doc, mes_doc, dia_doc,
  fecha_estimada, actores, categoria

Mejoras respecto a v1:
  - Normalización de actores: "General Armada" → "Armada", elimina
    duplicados con cargo/rango + nombre base.
  - Retry con backoff exponencial en peticiones HTTP.
  - Campo fecha_estimada (bool): True cuando la fecha se infiere
    solo del año o es un placeholder (dia=1 sin evidencia concreta).
  - Descubrimiento de IDs por paginación completa del listado +
    navegación anterior/siguiente.
  - Clasificación feb-1981 más precisa: solo 23-24 feb → noche_23f;
    resto de feb → contexto_1981 (salvo diplomáticos).
  - Normalización de tildes en deduplicación de palabras clave.

Requisitos:
    pip install requests beautifulsoup4 pandas tqdm

Uso:
    python scraper_23f_v2.py
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import unicodedata
import logging

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw):
        return it

from datetime import datetime
from collections import Counter

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper_23f")

# ---------------------------------------------------------------------------
# CONFIGURACION
# ---------------------------------------------------------------------------
BASE_URL    = "https://23fbuscador.rtve.es"
ID_INICIO   = 1859
OUTPUT_CSV  = "dataset_23f.csv"
DELAY       = 1.0
MAX_PAGES   = 50          # paginas máximas del listado a recorrer
PAGE_SIZE   = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://23fbuscador.rtve.es/",
}

# ---------------------------------------------------------------------------
# SESSION CON RETRY + BACKOFF
# ---------------------------------------------------------------------------

def crear_session():
    """Crea una session con retry automático y backoff exponencial."""
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(
        total=4,
        backoff_factor=2,          # 0s, 2s, 4s, 8s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


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
_RE_LARGO = re.compile(
    r"(\d{1,2})\s+de\s+(" + _MS + r")\s+de\s+(\d[\d.]{2,4})",
    re.IGNORECASE,
)
_RE_CORTO = re.compile(r"\b(\d{1,2})[-/](\d{2})[-/](\d{2,4})\b")
_RE_MES_ANIO = re.compile(
    r"(" + _MS + r")\s+de\s+(\d{4})",
    re.IGNORECASE,
)
_RE_PAREN_LARGO = re.compile(
    r"\((\d{1,2}\s+de\s+(?:" + _MS + r")\s+de\s+\d[\d.]{2,4})\)",
    re.IGNORECASE,
)
_RE_PAREN_MES = re.compile(
    r"\(((?:" + _MS + r")\s+(?:de\s+)?\d{4})\)",
    re.IGNORECASE,
)
_ABREV_PAT = "|".join(MESES_ABREV.keys())
_RE_ABREV = re.compile(
    r"(\d{1,2})\.\s*(" + _ABREV_PAT + r")\.?\s*(\d{4})",
    re.IGNORECASE,
)
_RE_AGMAE = re.compile(r"AGMAE|AGA-\d{2}", re.IGNORECASE)
_RE_CARGO = re.compile(
    r"(Coronel(?:es)?|General(?:es)?|Capit[a\u00e1]n(?:es)?|"
    r"Teniente(?:s)?|Comandante|Almirante|Sargento|"
    r"Gral\.|Tcol\.|TCol\.|TCOL\.|Tte\.|"
    r"Ministro|Presidente|Embajador|"
    r"Secretario\s+de\s+Estado|C[o\u00f3]nsul(?!ado)|"
    r"Encargado\s+de\s+Negocios)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# ACTORES CONOCIDOS DEL 23F
# ---------------------------------------------------------------------------
ACTORES_FIJOS = [
    "Tejero", "Milans del Bosch", "Armada", "Cortina",
    "Torres Rojas", "Pardo Zancada", "Garcia Carres",
    "San Martin", "Manchado", "Gabeiras",
    "Quintana Lacaci", "Juste", "Cassinello",
    "Adolfo Suarez", "Calvo Sotelo", "Gutierrez Mellado",
    "Felipe Gonzalez", "Carrillo", "Fraga",
    "Juan Carlos",
    "CESID", "JUJEM", "Consejo Supremo",
    "Haig", "Perez-Llorca", "Carrero Blanco",
]
_RE_ACTORES = [
    (a, re.compile(r"\b" + re.escape(a) + r"\b", re.IGNORECASE))
    for a in ACTORES_FIJOS
]
_RE_PREJUJEM = re.compile(r"PREJUJEM", re.IGNORECASE)

# ---------------------------------------------------------------------------
# NORMALIZACIÓN DE ACTORES
# ---------------------------------------------------------------------------

def _strip_tildes(s):
    """Elimina tildes: á→a, é→e, etc. para comparaciones."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Mapa de alias → nombre canónico (sin tildes para matching)
_ALIAS_MAP_RAW = {
    # Tejero
    "tejero molina": "Tejero",
    "antonio tejero": "Tejero",
    "antonio tejero molina": "Tejero",
    # Milans del Bosch
    "milans del bosch y ussia": "Milans del Bosch",
    "jaime milans del bosch": "Milans del Bosch",
    "jaime milans del bosch y ussia": "Milans del Bosch",
    "milans del boch": "Milans del Bosch",   # typo en datos
    "milans del -- bosch": "Milans del Bosch",
    "milans": "Milans del Bosch",
    "milan del bosch": "Milans del Bosch",    # typo
    "milana del bosch": "Milans del Bosch",   # typo
    # Armada
    "armada comyn": "Armada",
    "alfonso armada": "Armada",
    "alfonso armada comyn": "Armada",
    "alfonso armada y comin": "Armada",
    # Cortina
    "cortina prieto": "Cortina",
    "jose cortina prieto": "Cortina",
    "jose cortina --- prieto": "Cortina",
    # Torres Rojas
    "torres rojas": "Torres Rojas",
    # Pardo Zancada
    "pardo zancada": "Pardo Zancada",
    "ricardo pardo zancada": "Pardo Zancada",
    "pardo": "Pardo Zancada",
    # Gabeiras
    "gabeiras montero": "Gabeiras",
    # Manchado
    "miguel manchado": "Manchado",
    # Cassinello
    "cassinello perez": "Cassinello",
    "andres cassinello perez": "Cassinello",
    # Gutiérrez Mellado (varias grafías)
    "gutierrez mellado": "Gutierrez Mellado",
    "gutierrez meilado": "Gutierrez Mellado",  # typo
    # Calvo Sotelo
    "calvo sotelo": "Calvo Sotelo",
    # Suárez
    "suarez": "Adolfo Suarez",
    "suárez": "Adolfo Suarez",
    # San Martín
    "san martin": "San Martin",
    "sanmartin": "San Martin",
    "sanmartín": "San Martin",
}

# Prefijos de cargo a eliminar para normalizar
# Solo consume descriptores de servicio (de Infantería, de Caballería, etc.)
# NO consume el nombre propio que sigue al rango
_CARGO_PREFIXES = re.compile(
    r"^(?:Exmo\.\s*Sr\.\s*)?(?:Don\s+|D\.\s*|D\s+)?"
    r"(?:Teniente\s+General|"
    r"General\s+(?:de\s+)?(?:División|Brigada|Intendencia)|General|"
    r"Teniente\s+Coronel(?:\s+de\s+(?:Infantería|Caballería|Artillería|Ingenieros|Intendencia)\w*)?|"
    r"Coronel(?:\s+de\s+(?:Infantería|Caballería|Artillería|Ingenieros|Intendencia|la\s+G\.?\s*Civil)\w*)?|"
    r"Comandante(?:\s+de\s+(?:Infantería|Caballería|Artillería|Ingenieros|Intendencia\s+de\s+la\s+Armada)\w*)?|"
    r"Capit[aá]n(?:\s+General|\s+de\s+(?:Infantería|Caballería|Artillería|Ingenieros|Nav[íi]o|Intendencia|la\s+(?:G\.?\s*C\.|Civil|GC)))?|"
    r"Tcol\.|TCol\.|TCOL\.|Tte\.\s*(?:General|Coronel)|Gral\.|"
    r"Almirante|Sargento(?:\s+1°)?|"
    r"Presidente|Ministro|Embajador|"
    r"Coroneles|Generales|Tenientes|"
    r"el\s+|la\s+|las\s+|los\s+)"
    r"\s*",
    re.IGNORECASE,
)


def normalizar_actor(nombre):
    """
    Dado un nombre de actor (posiblemente con cargo/rango), devuelve
    el nombre canónico si es un protagonista conocido del 23F.
    Devuelve el nombre limpio si es un actor no conocido pero válido.
    Devuelve None si es un cargo genérico sin nombre propio.
    """
    nombre = nombre.strip()
    if not nombre:
        return None

    # 1. Check directo en la lista fija
    for actor_fijo in ACTORES_FIJOS:
        if actor_fijo.lower() == nombre.lower():
            return actor_fijo

    # 2. Quitar prefijo de cargo y "Don/D."
    limpio = _CARGO_PREFIXES.sub("", nombre).strip()
    limpio = re.sub(r"^(?:Don\s+|D\.\s*|D\s+)", "", limpio, flags=re.IGNORECASE).strip()

    # Filtrar si queda vacío o es un término genérico
    if len(limpio) < 4:
        return None
    _genericos = {
        "jefe", "director", "togado", "instructor", "funciones",
        "gobierno", "sala", "consejo", "estado", "republica",
        "general", "jefe e.m.", "jefe e.m", "consejero",
    }
    if _strip_tildes(limpio).lower() in _genericos:
        return None
    # Descartar si parece un descriptor institucional, no un nombre propio
    if re.match(
        r"^(?:de\s+|del\s+|en\s+|la\s+|las\s+|los\s+|el\s+|"
        r"a\s+sus\s+|segundo\s+|(?:IA|1ª|2ª|3ª)\s+)",
        limpio, re.IGNORECASE
    ):
        return None
    # Descartar entradas que claramente son instituciones/cargos genéricos
    if re.search(
        r"(?:CAPITANIA|Capitanía|Cuartel|Asesoría|Comisaría|"
        r"letrado|defensor|Sala|no\s+nombrado|sustituido|"
        r"órdenes|ordenes|Zona\s+Mar[íi]tima|Región\s+Militar|"
        r"Dirección|Departamento|Jefatura)",
        limpio, re.IGNORECASE
    ):
        return None

    # 3. Buscar en alias (sin tildes)
    limpio_norm = _strip_tildes(limpio).lower()
    canon = _ALIAS_MAP_RAW.get(limpio_norm)
    if canon:
        return canon

    # 4. Check si el nombre limpio contiene un actor fijo (min 5 chars para evitar falsos)
    if len(limpio_norm) >= 5:
        for actor_fijo in ACTORES_FIJOS:
            af_norm = _strip_tildes(actor_fijo).lower()
            if len(af_norm) >= 5 and (af_norm in limpio_norm or limpio_norm in af_norm):
                return actor_fijo

    # 5. Si no reconocido, devolver el nombre limpio
    return limpio


# ---------------------------------------------------------------------------
# FECHAS MANUALES
# ---------------------------------------------------------------------------
FECHAS_MANUALES = {
    1694: (1981,  2, 23, "noche_23f"),
    1695: (1981,  2, 23, "noche_23f"),
    1696: (1981,  2, 24, "noche_23f"),
    1698: (1981,  2, 23, "noche_23f"),
    1700: (1981,  2, 23, "noche_23f"),
    1788: (1981,  2, 23, "noche_23f"),
    1792: (1981,  2, 23, "noche_23f"),
    1793: (1981,  2, 23, "noche_23f"),
    1794: (1981,  2, 23, "noche_23f"),
    1795: (1981,  2, 23, "noche_23f"),
    1721: (1981,  3,  5, "juicio_1982"),
    1725: (1981,  3, 11, "juicio_1982"),
    1732: (1981,  4, 22, "juicio_1982"),
    1738: (1983,  2, 18, "juicio_1982"),
    1741: (1981,  4, 30, "juicio_1982"),
    1800: (1982,  3,  1, "juicio_1982"),
    1807: (1982,  6,  1, "juicio_1982"),
    1712: (1981,  2, 23, "contexto_1981"),
    1750: (1981,  2, 25, "diplomatico"),
    1752: (1931,  3,  4, "diplomatico"),
    1754: (1931,  3, 10, "diplomatico"),
    1760: (1961,  2, 24, "diplomatico"),
    1761: (1921,  3, 13, "diplomatico"),
    1773: (1981,  2, 24, "diplomatico"),
}

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
    Devuelve lista de (posicion, anio, mes, dia, es_exacta) para todas
    las fechas válidas en el texto.
    es_exacta = True si tiene día concreto; False si solo mes+año.
    """
    texto = str(texto)
    c = []

    for m in _RE_LARGO.finditer(texto):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = _limpiar_anio(m.group(3))
        me = MESES.get(ms, 0)
        if a and rmin <= a <= rmax and 1 <= d <= 31 and me:
            c.append((m.start(), a, me, d, True))

    for m in _RE_CORTO.finditer(texto):
        d, me, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a < 100:
            a += 1900
        if rmin <= a <= rmax and 1 <= me <= 12 and 1 <= d <= 31:
            c.append((m.start(), a, me, d, True))

    for m in _RE_MES_ANIO.finditer(texto):
        ms = m.group(1).lower()
        a = int(m.group(2))
        me = MESES.get(ms, 0)
        if rmin <= a <= rmax and me:
            c.append((m.start(), a, me, 1, False))   # dia=1 estimado

    for m in _RE_ABREV.finditer(texto):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = int(m.group(3))
        me = MESES_ABREV.get(ms, 0)
        if rmin <= a <= rmax and 1 <= d <= 31 and me:
            c.append((m.start(), a, me, d, True))

    return c


def _fmt(a, me, d, estimada=False):
    """Construye el dict de fecha."""
    try:
        fecha_doc = datetime(a, me, d).strftime("%d/%m/%Y")
    except ValueError:
        fecha_doc = f"01/{me:02d}/{a}"
        estimada = True
    return {
        "fecha_doc": fecha_doc,
        "anio_doc": a,
        "mes_doc": me,
        "dia_doc": d,
        "fecha_estimada": estimada,
    }


_FV = {
    "fecha_doc": None, "anio_doc": None,
    "mes_doc": None, "dia_doc": None,
    "fecha_estimada": None,
}


def _primera_fecha(texto):
    """Primera fecha por posición en el texto."""
    c = _candidatos(texto)
    if not c:
        return _FV
    primero = sorted(c, key=lambda x: x[0])[0]
    _, a, me, d, exacta = primero
    return _fmt(a, me, d, estimada=not exacta)


def extraer_fecha(titulo, resumen, texto):
    """
    Extrae la fecha del documento con prioridad:
    1. Fecha entre paréntesis al final del título
    2. Última fecha completa del título
    3. Primera fecha del resumen (500 chars)
    4. Primera fecha del texto completo
    """
    titulo = str(titulo)

    # 1a. Paréntesis con fecha completa
    matches = list(_RE_PAREN_LARGO.finditer(titulo))
    if matches:
        c = _candidatos(matches[-1].group(1))
        if c:
            primero = sorted(c, key=lambda x: x[0])[0]
            _, a, me, d, exacta = primero
            return _fmt(a, me, d, estimada=not exacta)

    # 1b. Paréntesis con mes-año: "(junio 1981)"
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
                return _fmt(a, me, 1, estimada=True)

    # 2. Última fecha completa del título
    cl = []
    for m in _RE_LARGO.finditer(titulo):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = _limpiar_anio(m.group(3))
        me = MESES.get(ms, 0)
        if a and 1900 <= a <= 1995 and 1 <= d <= 31 and me:
            cl.append((m.start(), a, me, d, True))
    for m in _RE_ABREV.finditer(titulo):
        d = int(m.group(1))
        ms = m.group(2).lower()
        a = int(m.group(3))
        me = MESES_ABREV.get(ms, 0)
        if 1900 <= a <= 1995 and 1 <= d <= 31 and me:
            cl.append((m.start(), a, me, d, True))
    if cl:
        ultimo = sorted(cl, key=lambda x: x[0])[-1]
        _, a, me, d, exacta = ultimo
        return _fmt(a, me, d, estimada=not exacta)

    # Último mes-año del título
    cm = []
    for m in _RE_MES_ANIO.finditer(titulo):
        ms = m.group(1).lower()
        a = int(m.group(2))
        me = MESES.get(ms, 0)
        if 1900 <= a <= 1995 and me:
            cm.append((m.start(), a, me))
    if cm:
        _, a, me = sorted(cm, key=lambda x: x[0])[-1]
        return _fmt(a, me, 1, estimada=True)

    # 3. Primera fecha del resumen
    f = _primera_fecha(str(resumen)[:500])
    if f["fecha_doc"]:
        return f

    # 4. Primera fecha del texto completo
    return _primera_fecha(str(texto))


# ---------------------------------------------------------------------------
# FUNCIONES DE ACTORES (con normalización)
# ---------------------------------------------------------------------------

_EXCLUIR_ACTOR = re.compile(
    r"no\s+identificad|no\s+consta|desconocid|personas?\s+no|"
    r"fuerzas?\s+arm|ejército|ejercito|militares\b|implicados\b|"
    r"procesados\b|acusados\b|centrales?\s+sindical|partidos?\s+pol|"
    r"guardia\s+civil|estado\s+mayor|direcci[oó]n\s+general|"
    r"al\s+mando\s+de|jefatura|departamento|ministerio\s+de|"
    r"cuerpo\s+de|policia\s+nacional|tribunal\b|congreso\b|"
    r"capitanía\s+general|capitania\s+general|"
    r"(?:1ª|2ª|3ª)\s+R\.?M\.|"
    r"institución|a\s+sus\s+órdenes|a\s+sus\s+ordenes|"
    r"excmo\.?\s+se[ñn]or|ministro\s+de\s+|"
    r"presidente\s+d[ae]l?\s+(?:gobierno|consejo|republica|estados)|"
    r"se[ñn]or\s+ministro|letrado\s+sr\.|defensor\s+del?",
    re.IGNORECASE,
)

_RE_SOLO_CARGO = re.compile(
    r"^(Coronel(?:es)?|General(?:es)?|Capit[aá]n(?:es)?|"
    r"Teniente(?:s)?(?:\s+Coronel)?(?:\s+General)?|"
    r"Comandante|Almirante|Sargento|"
    r"Ministro|Presidente|Embajador|"
    r"Secretario\s+de\s+Estado|"
    r"Encargado\s+de\s+Negocios|"
    r"teniente\s+coronel(?:\s+de\s+\w+)?|"
    r"comandante\s+de\s+\w+|"
    r"general\s+(?:segundo\s+)?(?:jefe|director|consejero)(?:\s+\w+)*|"
    r"presidente\s+(?:en\s+funciones|del?\s+\w+(?:\s+\w+)*)|"
    r"capitan\s+general)$",
    re.IGNORECASE,
)

# Cargos/instituciones genéricas que NO son actores individuales
_RE_GENERICO = re.compile(
    r"(?:CAPITANIA\s+GENERAL|GENERAL\s+JEFE(?:\s+\w+)*|"
    r"TENIENTE\s+GENERAL\s+JEFE|"
    r"Cuartel\s+General|Asesor[íi]a\s+Jur[íi]dica|"
    r"Comisaría|letrado|defensor|Sala|"
    r"no\s+nombrado|sustituido|órdenes|zona\s+mar[íi]tima)",
    re.IGNORECASE,
)


def extraer_actores(titulo, resumen, texto, palabras_clave):
    """
    Combina tres estrategias con normalización:
    1. Lista fija de protagonistas del 23F
    2. Entradas KW formato "Nombre:Cargo"
    3. Entradas KW con cargo/rango + nombre
    Todos se normalizan al nombre canónico cuando es posible.
    """
    todo = "{} {} {} {}".format(titulo, resumen, palabras_clave, str(texto)[:3000])
    encontrados = set()

    # Estrategia 1: lista fija
    for actor, patron in _RE_ACTORES:
        if patron.search(todo):
            encontrados.add(actor)

    # Alias PREJUJEM/PREJUJEN → JUJEM
    if _RE_PREJUJEM.search(todo) or re.search(r"PREJUJEN", todo, re.IGNORECASE):
        encontrados.add("JUJEM")

    # S.M. / Su Majestad → Juan Carlos
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
            if _RE_GENERICO.search(kw):
                continue

            # Estrategia 2: "Nombre:Cargo"
            if ":" in kw:
                nombre, _, rol = kw.partition(":")
                nombre = nombre.strip()
                if nombre and _RE_CARGO.search(rol) and 4 <= len(nombre) <= 50:
                    canon = normalizar_actor(nombre)
                    if canon:
                        encontrados.add(canon)

            # Estrategia 3: KW con cargo + algo más
            elif _RE_CARGO.search(kw) and len(kw) <= 60:
                canon = normalizar_actor(kw)
                if canon:
                    encontrados.add(canon)

    return " | ".join(sorted(encontrados))


# ---------------------------------------------------------------------------
# LIMPIEZA DE PALABRAS CLAVE
# ---------------------------------------------------------------------------

def limpiar_kws(kws_raw):
    """Filtra números sueltos, entradas cortas/largas y duplicados (tilde-aware)."""
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
        norm = _strip_tildes(kw).lower()
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
    Asigna categoría basándose en título, resumen y fecha.
    Feb-1981: solo 23-24 → noche_23f; 25+ o sin día → contexto_1981.
    """
    tit = str(row.get("titulo", "")).lower()
    res = str(row.get("resumen", "")).lower()
    todo = tit + " " + res

    # Diplomáticos (AGMAE / AGA en el título)
    if _RE_AGMAE.search(str(row.get("titulo", ""))):
        return "diplomatico"

    # Juicio 1982-1984
    if "vista oral" in tit and any(y in tit for y in ["1982", "1983", "1984"]):
        return "juicio_1982"
    kw_juicio = [
        "información integrada", "informacion integrada",
        "acotaciones al desarrollo del juicio",
        "comentarios sobre la sentencia del 23-f",
        "comisiones militares en la vista",
    ]
    if any(k in todo for k in kw_juicio):
        return "juicio_1982"

    # Noche del golpe (23-24 feb + keywords específicos)
    kw_noche = [
        "dentro del congreso", "asalto al congreso",
        "el día 23-f", "el dia 23-f", "23-f informando",
        "esposa de tejero", "garcia carres y tejero",
        "el pardo (24 de febrero",
        "planificación del golpe", "planificacion del golpe",
        "secuencia parcial de los hechos",
        "ocupación del palacio del congreso",
        "ocupacion del palacio del congreso",
        "situación actual en las distintas regiones",
        "situacion actual en las distintas regiones",
        "relato de los sucesos de los días 23 y 24",
        "relato de los sucesos de los dias 23 y 24",
        "incidentes en el congreso",
        "actitud del cesid ante la situación",
        "actitud del cesid ante la situacion",
        "actuación del departamento de defensa interna",
        "actuacion del departamento de defensa interna",
        "participación de miembros de la aome",
        "participacion de miembros de la aome",
        "relación cesid", "relacion cesid", "prejujem",
        "guión que sirvió de base", "guion que sirvio de base",
        "secretario haig", "todman",
        "solidaridad con el pueblo español",
        "solidaridad con el pueblo espanol",
        "intento de golpe de estado en españa",
        "intento de golpe de estado en espana",
        "defensa de las instituciones democráticas",
        "defensa de las instituciones democraticas",
        "posible golpe de estado",
        "involucionismo político provocado",
        "involucionismo politico provocado",
        "papel de la jujem en la crisis",
        "transcripción de conversación telefónica",
        "transcripcion de conversacion telefonica",
    ]
    if any(k in todo for k in kw_noche):
        return "noche_23f"

    # Por fecha (NaN-safe)
    anio = row.get("anio_doc")
    mes = row.get("mes_doc")
    dia = row.get("dia_doc")
    if anio is not None and anio == anio:
        anio = int(anio)
        if anio == 1981 and mes is not None and mes == mes:
            mes_i = int(mes)
            if mes_i == 2:
                # Solo 23-24 feb → noche_23f; resto → contexto_1981
                if dia is not None and dia == dia and int(dia) in (23, 24):
                    return "noche_23f"
                return "contexto_1981"
            return "contexto_1981"
        if anio in (1982, 1983, 1984):
            return "juicio_1982"
        if anio:
            return "otro"

    # Por año en el título
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
        "fecha_estimada": None,
        "actores": "",
        "categoria": "",
        "_id_anterior": None,
        "_id_siguiente": None,
    }

    # Título
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

    # Navegación anterior/siguiente
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
# DESCUBRIMIENTO DE IDs (paginación + navegación)
# ---------------------------------------------------------------------------

def obtener_todos_los_ids(session):
    """
    Descubre IDs por dos vías:
    1. Paginación completa del listado (?page=1, ?page=2, ...)
    2. Navegación Anterior/Siguiente desde cada documento
    """
    log.info("Descubriendo IDs...")
    visitados = set()
    por_visitar = {ID_INICIO}

    # Vía 1: Paginación del listado
    for page in range(1, MAX_PAGES + 1):
        try:
            url = f"{BASE_URL}/?page_size={PAGE_SIZE}&page={page}"
            r = session.get(url, timeout=15)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.find_all("a", href=re.compile(r"/document/ocr/\d+"))
            if not links:
                break
            nuevos = 0
            for link in links:
                m = re.search(r"/document/ocr/(\d+)", link["href"])
                if m:
                    nid = int(m.group(1))
                    if nid not in por_visitar:
                        nuevos += 1
                    por_visitar.add(nid)
            log.info(f"  Página {page}: {nuevos} IDs nuevos (total: {len(por_visitar)})")
            if nuevos == 0 and page > 1:
                break
            time.sleep(DELAY * 0.3)
        except Exception as e:
            log.warning(f"  Página {page} no accesible: {e}")
            break

    log.info(f"  IDs del listado: {len(por_visitar)}")

    # Vía 2: Navegación Anterior/Siguiente
    pbar = tqdm(desc="  Navegando", unit=" docs")
    while por_visitar:
        doc_id = por_visitar.pop()
        if doc_id in visitados:
            continue
        url = f"{BASE_URL}/document/ocr/{doc_id}?page_size={PAGE_SIZE}&page=1"
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
            log.error(f"  ID {doc_id}: {e}")
        time.sleep(DELAY * 0.5)
    pbar.close()

    log.info(f"  Total IDs descubiertos: {len(visitados)}")
    return sorted(visitados)


# ---------------------------------------------------------------------------
# SCRAPING DE UN DOCUMENTO
# ---------------------------------------------------------------------------

def scrape_doc(session, doc_id):
    url = f"{BASE_URL}/document/ocr/{doc_id}?page_size={PAGE_SIZE}&page=1"
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200:
            return parsear_documento(r.text, doc_id)
        else:
            log.warning(f"  ID {doc_id}: HTTP {r.status_code}")
    except Exception as e:
        log.error(f"  ID {doc_id}: {e}")
    return None


# ---------------------------------------------------------------------------
# POST-PROCESADO DEL DATAFRAME
# ---------------------------------------------------------------------------

def aplicar_correcciones(df):
    """Aplica fechas manuales y correcciones de categoría."""
    n_fechas = 0
    n_cats = 0

    for doc_id, (a, me, d, cat) in FECHAS_MANUALES.items():
        mask = df["id"] == doc_id
        if not mask.any():
            continue
        try:
            fd = datetime(a, me, d).strftime("%d/%m/%Y")
        except ValueError:
            fd = f"01/{me:02d}/{a}"
        df.loc[mask, "fecha_doc"] = fd
        df.loc[mask, "anio_doc"] = a
        df.loc[mask, "mes_doc"] = me
        df.loc[mask, "dia_doc"] = d
        df.loc[mask, "fecha_estimada"] = False  # manuales = verificadas
        n_fechas += 1
        if cat:
            df.loc[mask, "categoria"] = cat
            n_cats += 1

    for doc_id, cat in CORRECCIONES_CAT.items():
        mask = df["id"] == doc_id
        if mask.any():
            df.loc[mask, "categoria"] = cat
            n_cats += 1

    log.info(f"  Fechas manuales: {n_fechas}  |  Categorías corregidas: {n_cats}")
    return df


def recuperar_fechas(df):
    """
    Para docs sin fecha, intenta extraer del texto completo;
    si no, usa el año más mencionado (marcando fecha_estimada=True).
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
                df.loc[idx, "fecha_doc"] = f"01/01/{a}"
                df.loc[idx, "anio_doc"] = a
                df.loc[idx, "fecha_estimada"] = True
                n += 1
    log.info(f"  Fechas recuperadas de texto: {n}")
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
            dia = row.get("dia_doc")
            if anio == 1981 and mes is not None and mes == mes:
                mes_i = int(mes)
                if mes_i == 2 and dia is not None and dia == dia and int(dia) in (23, 24):
                    df.loc[idx, "categoria"] = "noche_23f"
                elif anio == 1981:
                    df.loc[idx, "categoria"] = "contexto_1981"
            elif anio in (1982, 1983, 1984):
                df.loc[idx, "categoria"] = "juicio_1982"
            else:
                df.loc[idx, "categoria"] = "otro"
            n += 1
    log.info(f"  Reclasificados: {n}")
    return df


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    sep = "=" * 60
    log.info(sep)
    log.info("  SCRAPER 23F v2")
    log.info(sep)

    session = crear_session()

    # 1. Descubrir IDs
    ids = obtener_todos_los_ids(session)
    if not ids:
        log.error("No se encontraron IDs.")
        return

    # 2. Descargar documentos
    log.info(f"Descargando {len(ids)} documentos...")
    docs = []
    errores = []
    for doc_id in tqdm(ids, desc="  Descargando"):
        doc = scrape_doc(session, doc_id)
        if doc:
            docs.append(doc)
            if len(docs) % 25 == 0:
                pd.DataFrame(docs).to_csv(
                    "dataset_23f_parcial.csv", index=False, encoding="utf-8-sig"
                )
        else:
            errores.append(doc_id)
        time.sleep(DELAY)

    if errores:
        log.warning(f"  {len(errores)} docs con error: {errores}")
        # Reintentar errores una vez más
        log.info("  Reintentando errores...")
        time.sleep(5)
        for doc_id in errores:
            doc = scrape_doc(session, doc_id)
            if doc:
                docs.append(doc)
                log.info(f"    ID {doc_id}: recuperado")
            time.sleep(DELAY * 2)

    # 3. Construir DataFrame
    log.info("Procesando y guardando...")
    df = pd.DataFrame(docs)
    df = df.drop(columns=["_id_anterior", "_id_siguiente"], errors="ignore")

    # Categorización automática
    df["categoria"] = df.apply(categorizar, axis=1)

    # Correcciones manuales
    df = aplicar_correcciones(df)

    # Recuperar fechas restantes
    df = recuperar_fechas(df)

    # Reclasificar sin_clasificar
    df = reclasificar(df)

    # Fusionar categorías "otro_XXXX" → "otro"
    df["categoria"] = df["categoria"].str.replace(
        r"^otro_\d+$", "otro", regex=True
    )

    # Tipos numéricos
    for col in ["anio_doc", "mes_doc", "dia_doc"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].isna().sum() == 0:
            df[col] = df[col].astype(int)

    # fecha_estimada a bool
    df["fecha_estimada"] = df["fecha_estimada"].fillna(True).astype(bool)

    # Guardar
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # Resumen
    n = len(df)
    log.info("")
    log.info(sep)
    log.info(f"  COMPLETADO: {n} documentos")
    log.info(sep)
    log.info("")
    log.info("Distribución por categoría:")
    for cat, cnt in df["categoria"].value_counts().items():
        log.info(f"  {cat}: {cnt}")

    n_fecha = df["fecha_doc"].notna().sum()
    n_est = df["fecha_estimada"].sum()
    n_act = (df["actores"].notna() & (df["actores"].str.len() > 0)).sum()
    n_sc = (df["categoria"] == "sin_clasificar").sum()
    log.info("")
    log.info(f"Fechas: {n_fecha}/{n} ({n_est} estimadas)")
    log.info(f"Con actores: {n_act}/{n}")
    log.info(f"Sin clasificar: {n_sc}")
    log.info(f"Columnas: {list(df.columns)}")
    log.info(f"Guardado en: {OUTPUT_CSV}")

    # Muestra
    cols_muestra = [
        "id", "titulo", "fecha_doc", "fecha_estimada",
        "actores", "categoria",
    ]
    print("\nMuestra:")
    print(df[cols_muestra].head(10).to_string())


if __name__ == "__main__":
    main()
