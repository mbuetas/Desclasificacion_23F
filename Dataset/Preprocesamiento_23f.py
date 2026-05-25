"""
PREPROCESAMIENTO 23F — Construcción del Dataset 
================================================================

Este script transforma el CSV inicial (167 docs con OCR de Mistral) en un
dataset listo para los 4 casos de uso de ML del proyecto.

Salidas:
    data/processed/dataset_23f_master.csv       # Tabla principal enriquecida
    data/processed/entidades_canonicas.json     # Diccionario de resolución
    data/processed/embeddings_23f.npy           # Embeddings semánticos (opcional)
    data/processed/eventos_horarios_noche.csv   # Cronología extraída (Caso 4)

Uso:
    python preprocesar_23f.py --input data/raw/dataset_23f.csv

Requisitos mínimos (paso 1-5):
    pip install pandas numpy rapidfuzz

Requisitos opcionales (paso 6 NER, paso 7 embeddings):
    pip install spacy sentence-transformers
    python -m spacy download es_core_news_md

Tiempo estimado: 30 segundos sin embeddings, 3-5 minutos con embeddings.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path("data/processed")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_MASTER = OUTPUT_DIR / "dataset_23f_master.csv"
OUT_CANONICAS = OUTPUT_DIR / "entidades_canonicas.json"
OUT_EMBEDDINGS = OUTPUT_DIR / "embeddings_23f.npy"
OUT_EVENTOS = OUTPUT_DIR / "eventos_horarios_noche.csv"

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. METADATOS ESTRUCTURALES (catálogo de La Moncloa)
# ═══════════════════════════════════════════════════════════════════════════════

# Cada entrada mapea un patrón del título a sus metadatos en La Moncloa.
# Construido manualmente desde la web oficial.

CATALOGO_MONCLOA = [
    # Interior - Guardia Civil
    ("Transcripción de conversación telefónica de (presuntamente) García Carres y Tejero",
     "Interior", "Guardia Civil", "sin_marca", "1981-02-23"),
    ("Transcripción de conversación telefónica de García Carres con otra persona",
     "Interior", "Guardia Civil", "sin_marca", "1981-02-23"),
    ("Conversaciones telefónicas de (presuntamente) la unidad militar El Pardo",
     "Interior", "Guardia Civil", "sin_marca", "1981-02-24"),
    ("Documentación con una presunta planificación del golpe, manuscrita",
     "Interior", "Guardia Civil", "sin_marca", "1980-01-01"),
    ("Documento manuscrito de posible planificación del golpe",
     "Interior", "Guardia Civil", "sin_marca", None),
    ("intervenidas a la esposa de Tejero",
     "Interior", "Guardia Civil", "sin_marca", "1981-02-23"),
    ("secuencia parcial de los hechos del asalto al Congreso",
     "Interior", "Guardia Civil", "sin_marca", "1981-02-23"),
    ("Télex interiores y de agencias",
     "Interior", "Guardia Civil", "sin_marca", "1981-02-23"),
    ("Oficio zona País Vasco que expresa una comunicación del teniente coronel Tejero",
     "Interior", "Guardia Civil", "sin_marca", "1975-01-01"),
    ("comparecencia del teniente coronel Tejero informando sobre una reunión en la cafetería Galaxia",
     "Interior", "Guardia Civil", "sin_marca", "1978-01-01"),
    ("repercusión en prensa del arresto de Tejero en 1978",
     "Interior", "Guardia Civil", "sin_marca", "1978-01-01"),
    # Interior - Policía
    ("Situación actual en las distintas regiones policiales",
     "Interior", "Policía", "sin_marca", None),
    ("Fuerza Nueva. El divorcio del Rey con el Ejército",
     "Interior", "Policía", "sin_marca", "1981-03-12"),
    ("Ayudas a los implicados en el 23-F",
     "Interior", "Policía", "sin_marca", "1981-03-18"),
    ("Operación Ariete",
     "Interior", "Policía", "sin_marca", "1981-03-18"),
    ("Bloqueada una cuenta de la asociación de mujeres de militares",
     "Interior", "Policía", "sin_marca", "1981-03-27"),
    ("Partido Comunista de España PCE. Intento de la extrema derecha",
     "Interior", "Policía", "sin_marca", "1981-05-11"),
    ("Campaña de formación del PSOE sobre involución",
     "Interior", "Policía", "sin_marca", "1981-05-13"),
    ("Apoyo económico a los implicados en el 23-F",
     "Interior", "Policía", "sin_marca", "1983-05-10"),
    # Interior - Archivo
    ("Policía Nacional. Informe de situación. Marca: reservado-confidencial",
     "Interior", "Archivo Interior", "RESERVADO", "1981-11-12"),
    ("Índices de subversión en las FAS",
     "Interior", "Archivo Interior", "SECRETO", "1981-12-01"),
    ("acotaciones al desarrollo del juicio",
     "Interior", "Archivo Interior", "sin_marca", "1982-01-01"),
    ("Campaña contra S.M",
     "Interior", "Archivo Interior", "sin_marca", None),
    ("Involucionismo político provocado",
     "Interior", "Archivo Interior", "sin_marca", None),
    ("Posible golpe de estado",
     "Interior", "Archivo Interior", "sin_marca", None),
    ("Notas de 1983",
     "Interior", "Archivo Interior", "sin_marca", "1983-01-01"),
    # Defensa - CNI
    ("Guión que sirvió de base para la reunión de S.M. el Rey",
     "Defensa", "CNI", "sin_marca", "1981-12-14"),
    ("Relación CESID (Dirección) - General Jefe del Estado Mayor",
     "Defensa", "CNI", "sin_marca", "1981-02-23"),
    ("Relación CESID (Dirección) - PREJUJEM",
     "Defensa", "CNI", "sin_marca", "1981-02-23"),
    ("Resumen de la actuación del Departamento de Defensa Interna",
     "Defensa", "CNI", "sin_marca", "1981-02-23"),
    ("Actitud del CESID ante la situación",
     "Defensa", "CNI", "sin_marca", "1981-02-23"),
    ("participación de miembros de la AOME",
     "Defensa", "CNI", "sin_marca", "1981-02-23"),
    ("Investigación y declaraciones personal AOME",
     "Defensa", "CNI", "sin_marca", "1981-04-09"),
    ("Carta de José Cortina Prieto para Emilio Manglano",
     "Defensa", "CNI", "sin_marca", "1981-10-03"),
    ("Comisiones militares (10 de marzo",
     "Defensa", "CNI", "sin_marca", "1982-03-10"),
    ("Comisiones militares en la vista de la causa",
     "Defensa", "CNI", "sin_marca", "1982-03-01"),
    ("Ambiente en los cuarteles",
     "Defensa", "CNI", "sin_marca", "1982-02-25"),
    ("anunciada libertad provisional",
     "Defensa", "CNI", "sin_marca", "1982-04-19"),
    ("Reunión sobre acontecimientos recientes",
     "Defensa", "CNI", "sin_marca", "1982-03-08"),
    ("crónica de un golpe anunciado",
     "Defensa", "CNI", "sin_marca", None),
    ("Incidente entre la defensa del Teniente General Milans",
     "Defensa", "CNI", "sin_marca", "1982-04-28"),
    ("Comentarios sobre la sentencia del 23-F",
     "Defensa", "CNI", "sin_marca", "1982-06-06"),
    ("papel de la JUJEM en la crisis",
     "Defensa", "CNI", "sin_marca", "1981-02-23"),
    ("Reacciones ante la sentencia 23-F",
     "Defensa", "CNI", "sin_marca", "1982-06-04"),
    ("Estado de opinión sobre las sentencias",
     "Defensa", "CNI", "sin_marca", "1982-06-07"),
    ("Relato de los sucesos de los días 23 y 24",
     "Defensa", "CNI", "sin_marca", "1981-02-23"),
    ("Vista oral 2/81",
     "Defensa", "CNI", "sin_marca", None),  # fecha viene del título
    ("Información integrada",
     "Defensa", "CNI", "sin_marca", None),
    ("Semestral de la amenaza interior",
     "Defensa", "CNI", "sin_marca", "1981-02-10"),
    ("Relaciones entre algunos militares y paisanos armados",
     "Defensa", "CNI", "sin_marca", "1981-04-14"),
    ("Referencias en los medios de comunicación social a personal del centro",
     "Defensa", "CNI", "sin_marca", "1981-04-30"),
    ("Procesamiento de un jefe",
     "Defensa", "CNI", "sin_marca", "1981-05-22"),
    ("Sánchez Valiente en Roma",
     "Defensa", "CNI", "sin_marca", "1981-06-30"),
    ("Capitán Sánchez Valiente (2 de junio",
     "Defensa", "CNI", "sin_marca", "1981-06-02"),
    ("Traslado de un escrito",
     "Defensa", "CNI", "sin_marca", None),
    ("Tostón de la Calle",
     "Defensa", "CNI", "sin_marca", "1982-01-05"),
    ("Solicitando datos sobre el Cte. Cortina",
     "Defensa", "CNI", "sin_marca", "1982-01-08"),
    ("Certificación solicitada para su unión a la causa 2/81",
     "Defensa", "CNI", "sin_marca", "1982-01-12"),
    ("Comparecimiento en Consejo Supremo un oficial",
     "Defensa", "CNI", "sin_marca", "1982-01-12"),
    ("entrevistas de S.M el Rey con militares implicados",
     "Defensa", "CNI", "sin_marca", "1982-02-05"),
    ("Revisión de la sentencia dictada en la causa 2/81",
     "Defensa", "CNI", "sin_marca", "1987-10-19"),
    # Defensa - Archivo (clasificación marcada)
    ("parte por abandono de destino del Cap. Sánchez Valiente",
     "Defensa", "Archivo Defensa - Causa 94/81", "RESERVADO", None),
    ("Hoja de servicios del Cap. Sánchez Valiente",
     "Defensa", "Archivo Defensa - Causa 94/81", "RESERVADO", None),
    ("Asesoría Jurídica General sobre su situación administrativa",
     "Defensa", "Archivo Defensa - Causa 94/81", "RESERVADO", None),
    ("comunicando sanción a consejeros del Consejo Supremo",
     "Defensa", "Archivo Defensa - Carpeta 21801", "SECRETO", None),
    ("levantamiento de incomunicación de Tejero",
     "Defensa", "Archivo Defensa - Carpeta 21802", "SECRETO", None),
    ("medidas de seguridad con las visitas a Tejero",
     "Defensa", "Archivo Defensa - Carpeta 21802", "SECRETO", None),
    ("recurso de queja de Milans del Bosch",
     "Defensa", "Archivo Defensa - Carpeta 21804", "RESERVADO", None),
    ("circunstancias de la detención de Milans del Bosch",
     "Defensa", "Archivo Defensa - Carpeta 21804", "SECRETO", None),
    ("distribución de los procesados por la Causa 2/81",
     "Defensa", "Archivo Defensa - Carpeta 21804", "SECRETO", None),
    # Exteriores
    ("AGMAE_R39017",
     "Exteriores", "Subsecretaría - EEUU", "sin_marca", "1981-02-23"),
    ("AGMAE_R40201",
     "Exteriores", "DG Asuntos Consulares", "sin_marca", "1981-02-23"),
    ("AGA-83-07633",
     "Exteriores", "DG Iberoamérica", "sin_marca", "1981-02-23"),
    ("AGA-83-08764",
     "Exteriores", "DG Iberoamérica - Juicio", "sin_marca", "1982-01-01"),
    ("AGA-83-09301",
     "Exteriores", "DG Política Exterior Europa", "sin_marca", "1981-02-23"),
]


def match_moncloa(titulo: str) -> tuple | None:
    """Encuentra la entrada del catálogo que mejor encaja con el título."""
    titulo_lower = str(titulo).lower()
    best_match = None
    best_len = 0
    for entry in CATALOGO_MONCLOA:
        pattern = entry[0].lower()
        if pattern in titulo_lower and len(pattern) > best_len:
            best_match = entry
            best_len = len(pattern)
    return best_match


def extraer_clasificacion_titulo(titulo: str) -> str:
    """Extrae SECRETO/RESERVADO del prefijo del título."""
    titulo = str(titulo).strip()
    titulo_upper = titulo.upper()
    if titulo_upper.startswith("SECRETO"):
        return "SECRETO"
    if titulo_upper.startswith("RESERVADO"):
        return "RESERVADO"
    if re.search(r"marca:\s*reservado", titulo, re.IGNORECASE):
        return "RESERVADO"
    if re.search(r"marca:\s*secreto", titulo, re.IGNORECASE):
        return "SECRETO"
    return "sin_marca"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DIMENSIÓN TEMPORAL: FASES
# ═══════════════════════════════════════════════════════════════════════════════

def asignar_fase(fecha_str) -> str:
    """Asigna la fase histórica del documento."""
    if pd.isna(fecha_str) or not fecha_str:
        return "sin_fecha"
    try:
        # Soportar dd/mm/yyyy y yyyy-mm-dd
        s = str(fecha_str)
        if "/" in s:
            dt = datetime.strptime(s[:10], "%d/%m/%Y")
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return "sin_fecha"

    # Pre-golpe
    if dt < datetime(1981, 2, 22):
        return "1_pre_golpe"
    # Noche del golpe
    if datetime(1981, 2, 22) <= dt <= datetime(1981, 2, 24):
        return "2_noche_golpe"
    # Días inmediatos
    if dt <= datetime(1981, 3, 7):
        return "3_dias_inmediatos"
    # Instrucción de la causa
    if dt < datetime(1982, 2, 19):
        return "4_instruccion"
    # Juicio oral (vista oral fue feb-jun 1982)
    if dt <= datetime(1982, 6, 30):
        return "5_juicio_oral"
    # Post-sentencia
    return "6_post_sentencia"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EXTRACCIÓN DE TIMESTAMPS INTERNOS (Caso 4)
# ═══════════════════════════════════════════════════════════════════════════════

# Patrones de hora en los textos (con ruido OCR considerado)
PATRON_HORA = re.compile(
    r"\b(?:a\s+las\s+)?"
    r"(\d{1,2})[:.,]?(\d{2})"
    r"\s*(?:h(?:oras|s)?|H(?:ORAS|S)?)?\b"
)

# Versión más permisiva (capta "20:30" / "20.30" / "20,30")
PATRON_HORA_LIMPIO = re.compile(r"\b([01]?\d|2[0-3])[:.,]([0-5]\d)\b")


def extraer_horas_doc(texto: str, doc_id: int, fecha_doc: str) -> list[dict]:
    """
    Extrae horas del reloj mencionadas en un texto, con su contexto.
    Filtra agresivamente ruido típico del OCR:
    - Números de teléfono (ext. 200, 451, etc.)
    - Timecodes de transcripción (4.19.32 → 4 horas no es hora del reloj)
    - Páginas, párrafos, artículos
    - Decimales ("19.32 millones")
    Devuelve lista de eventos con id_doc, hora, contexto.
    """
    if pd.isna(texto) or not texto:
        return []

    eventos = []
    texto = str(texto)

    # Patrón más estricto: hora del reloj con marcador explícito
    # Acepta: "20:30 horas", "20.30 h", "a las 20:30", "20'30 horas"
    patron_estricto = re.compile(
        r"(?:a\s+las\s+)?"                          # opcional "a las"
        r"(?<![\d.,/])"                             # no precedido por dígito (evita timecodes)
        r"\b([01]?\d|2[0-3])"                       # hora 0-23
        r"[:.,'h]"                                  # separador
        r"([0-5]\d)"                                # minutos 0-59
        r"(?:[:.,'](\d{2}))?"                       # segundos opcionales
        r"\s*(h(?:oras?|s)?|H(?:ORAS?|S)?)?"        # opcional "horas"
        r"(?![\d/])",                               # no seguido por dígito o /
        re.IGNORECASE,
    )

    palabras_basura = re.compile(
        r"extensi[oó]n|ext\.|tel[eé]fono|tlf|p[aá]gina|p[aá]g\.?|"
        r"art[ií]culo|art\.|p[aá]rrafo|millon|por\s+ciento|porciento|euros?|"
        r"pesetas?|pts\.?|n[uú]mero",
        re.IGNORECASE,
    )

    for m in patron_estricto.finditer(texto):
        hh = int(m.group(1))
        mm = int(m.group(2))
        # Contexto
        ini = max(0, m.start() - 80)
        fin = min(len(texto), m.end() + 80)
        ctx_pre = texto[ini:m.start()]
        ctx_post = texto[m.end():fin]
        contexto = (ctx_pre + texto[m.start():m.end()] + ctx_post).strip()
        contexto = re.sub(r"\s+", " ", contexto)

        # Filtros de basura en contexto cercano (30 chars antes/después)
        ctx_cercano = (texto[max(0, m.start()-30):m.start()] +
                       texto[m.end():m.end()+30])
        if palabras_basura.search(ctx_cercano):
            continue

        # Marcador explícito de hora aumenta confianza
        tiene_horas_kw = bool(m.group(4)) or "hora" in ctx_cercano.lower()

        # Si no tiene marcador "horas" Y no está claramente en horario (≥10h),
        # exigimos contexto adicional
        if not tiene_horas_kw:
            # Patrón "a las X" en pre-contexto
            if not re.search(r"a\s+las\s*$", ctx_pre[-15:], re.IGNORECASE):
                continue

        eventos.append({
            "id_doc": doc_id,
            "fecha_doc": fecha_doc,
            "hora": f"{hh:02d}:{mm:02d}",
            "hora_decimal": hh + mm / 60.0,
            "contexto": contexto[:200],
            "marcador_explicito": tiene_horas_kw,
        })

    return eventos


# ═══════════════════════════════════════════════════════════════════════════════
# 4. NER + RESOLUCIÓN DE ENTIDADES
# ═══════════════════════════════════════════════════════════════════════════════

# Diccionario de entidades canónicas con sus variantes conocidas.
# Construido manualmente para los actores principales del 23-F.

ENTIDADES_CANONICAS = {
    # Personas - golpistas
    "Antonio Tejero": [
        "Tejero", "Antonio Tejero", "Tcol. Tejero", "T.C. Tejero",
        "teniente coronel Tejero", "Tcol Tejero", "Tcol.Tejero",
    ],
    "Jaime Milans del Bosch": [
        "Milans", "Milans del Bosch", "General Milans", "Jaime Milans",
        "Teniente General Milans", "T.G. Milans", "Capitán General de Valencia",
    ],
    "Alfonso Armada": [
        "Armada", "General Armada", "Alfonso Armada", "Gral. Armada",
        "Teniente General Armada",
    ],
    "Juan García Carrés": [
        "García Carres", "Garcia Carres", "García Carrés", "G.C.",
        "García Carrés", "Juan García Carrés",
    ],
    "José Luis Cortina": [
        "Cortina", "Cte. Cortina", "Comandante Cortina", "José Cortina",
        "José Cortina Prieto", "Cortina Prieto",
    ],
    "Sánchez Valiente": [
        "Sánchez Valiente", "Sanchez Valiente", "Capitán Sánchez Valiente",
        "Cap. Sánchez Valiente",
    ],
    "Carmen Díez (esposa de Tejero)": [
        "esposa de Tejero", "mujer de Tejero", "Carmen Díez",
    ],
    # Personas - lado constitucional
    "Juan Carlos I": [
        "Juan Carlos", "el Rey", "S.M. el Rey", "S.M.", "Juan Carlos I",
        "Su Majestad el Rey", "el monarca",
    ],
    "Adolfo Suárez": [
        "Suárez", "Adolfo Suárez", "Presidente Suárez",
    ],
    "Manuel Gutiérrez Mellado": [
        "Gutiérrez Mellado", "Gutierrez Mellado", "General Gutiérrez Mellado",
    ],
    "Leopoldo Calvo-Sotelo": [
        "Calvo-Sotelo", "Calvo Sotelo", "Leopoldo Calvo-Sotelo",
    ],
    "Santiago Carrillo": [
        "Carrillo", "Santiago Carrillo",
    ],
    "Felipe González": [
        "Felipe González", "González",
    ],
    "Sabino Fernández Campo": [
        "Sabino Fernández", "Fernández Campo", "Sabino Fernández Campo",
    ],
    "Emilio Manglano": [
        "Manglano", "Emilio Manglano",
    ],
    "Francisco Laína": [
        "Laína", "Francisco Laína", "Director de la Seguridad",
    ],
    "Luis Torres Rojas": [
        "Torres Rojas", "Luis Torres Rojas", "General Torres Rojas",
    ],
    "Pedro Pitarch": [
        "Pitarch", "Pedro Pitarch",
    ],
    "Alexander Haig": [
        "Haig", "Alexander Haig", "Secretario Haig", "Secretario de Estado Haig",
    ],
    # Instituciones
    "Congreso de los Diputados": [
        "Congreso", "Cortes", "Palacio del Congreso", "hemiciclo",
        "Congreso de los Diputados",
    ],
    "Casa Real / Zarzuela": [
        "Zarzuela", "Casa Real", "Palacio de la Zarzuela", "casa civil del rey",
    ],
    "CESID": [
        "CESID", "Centro Superior de Información de la Defensa",
    ],
    "AOME": [
        "AOME", "Agrupación Operativa de Misiones Especiales",
    ],
    "JUJEM": [
        "JUJEM", "Junta de Jefes de Estado Mayor", "PREJUJEM",
    ],
    "Guardia Civil": [
        "Guardia Civil", "G.C.", "Cuerpo de la Guardia Civil",
    ],
    "Capitanía General de Valencia": [
        "Capitanía General de Valencia", "Capitanía de Valencia",
        "III Región Militar",
    ],
    "División Acorazada Brunete": [
        "División Acorazada", "Brunete", "DAC", "Acorazada Brunete",
    ],
    "RTVE / Prado del Rey": [
        "RTVE", "Prado del Rey", "Televisión Española", "TVE",
    ],
    "Consejo Supremo de Justicia Militar": [
        "Consejo Supremo de Justicia Militar", "CSJM", "Consejo Supremo",
    ],
    "Fuerza Nueva": [
        "Fuerza Nueva",
    ],
    "Cafetería Galaxia": [
        "Galaxia", "cafetería Galaxia", "Operación Galaxia",
    ],
}

# Construir índice inverso para búsqueda rápida
def construir_indice_canonico():
    """Devuelve dict {variante_lower: canonica}."""
    indice = {}
    for canonica, variantes in ENTIDADES_CANONICAS.items():
        for v in variantes:
            indice[v.lower()] = canonica
    return indice


def normalizar_texto(s: str) -> str:
    """Quita tildes y pasa a lower para fuzzy matching."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def resolver_entidad(mencion: str, indice_canonico: dict, umbral: int = 85) -> str | None:
    """
    Dado un nombre/mención, devuelve la entidad canónica más cercana.
    Si no hay match razonable, devuelve None.
    """
    mencion_lower = mencion.lower().strip()
    # Match exacto
    if mencion_lower in indice_canonico:
        return indice_canonico[mencion_lower]
    # Fuzzy match
    candidatos = list(indice_canonico.keys())
    match = process.extractOne(mencion_lower, candidatos, scorer=fuzz.ratio)
    if match and match[1] >= umbral:
        return indice_canonico[match[0]]
    return None


def extraer_entidades_regex(texto: str, indice_canonico: dict) -> dict:
    """
    Extracción de entidades sin spaCy: busca todas las variantes conocidas
    en el texto. Es menos completo que NER real pero es 100% determinista
    y sin dependencias.
    """
    if pd.isna(texto):
        texto = ""
    texto = str(texto)
    encontradas = Counter()

    for variante, canonica in indice_canonico.items():
        # Buscar variante con word boundaries (para evitar falsos positivos cortos)
        if len(variante) < 4:
            continue
        # Escape regex y búsqueda case-insensitive
        patron = r"\b" + re.escape(variante) + r"\b"
        n = len(re.findall(patron, texto, re.IGNORECASE))
        if n > 0:
            encontradas[canonica] += n

    return dict(encontradas)


def extraer_entidades_spacy(texto: str, nlp, indice_canonico: dict) -> dict:
    """
    Extracción con spaCy NER + resolución contra catálogo canónico.
    Para entidades detectadas que no están en el catálogo, las añade
    con su forma original (útil para descubrir actores secundarios).
    """
    if pd.isna(texto):
        return {}
    texto = str(texto)[:1_000_000]  # spaCy tiene límite ~1M chars
    doc = nlp(texto)
    encontradas = Counter()

    for ent in doc.ents:
        if ent.label_ in {"PER", "ORG", "LOC"}:
            mencion = ent.text.strip()
            if len(mencion) < 3:
                continue
            canonica = resolver_entidad(mencion, indice_canonico)
            if canonica:
                encontradas[canonica] += 1
            else:
                # Entidad no canónica: la conservamos con su forma original
                # (útil para análisis de "actores menores")
                encontradas[f"otros::{mencion}"] += 1

    return dict(encontradas)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. FEATURES DERIVADAS
# ═══════════════════════════════════════════════════════════════════════════════

JERGA_MILITAR = {
    "regimiento", "división", "batallón", "compañía", "tropa", "tropas",
    "general", "coronel", "comandante", "capitán", "teniente", "sargento",
    "alférez", "brigada", "cuartel", "casino", "guarnición", "operativo",
    "operación", "unidad", "mando", "estado mayor", "ejército", "fuerzas armadas",
    "fas", "tanque", "blindado", "infantería", "artillería", "aviación",
    "marina", "armada", "guardia civil", "policía armada", "subversión",
    "golpe", "rebelión", "sublevación", "consigna", "orden",
}

JERGA_JURIDICA = {
    "causa", "auto", "procesamiento", "procesado", "sumario", "vista oral",
    "consejo de guerra", "consejo supremo", "sentencia", "recurso", "casación",
    "indulto", "incomunicación", "diligencia", "comparecencia", "declaración",
    "fiscal", "defensa", "abogado", "tribunal", "juzgado", "instrucción",
    "fallo", "condena", "prisión", "rebelión militar", "delito",
}

JERGA_DIPLOMATICA = {
    "embajada", "embajador", "consulado", "cónsul", "ministerio", "exterior",
    "exteriores", "comunicado", "nota verbal", "telegrama", "cable",
    "delegación", "representación", "diplomático", "consular",
    "solidaridad", "apoyo", "democracia", "estado", "gobierno",
}


def calcular_features(texto: str) -> dict:
    """Features estructurales y semánticas basadas en el texto."""
    if pd.isna(texto) or not texto:
        return {
            "n_chars": 0, "n_palabras": 0, "n_palabras_unicas": 0,
            "ttr": 0.0, "long_oracion_media": 0.0,
            "densidad_militar": 0.0, "densidad_juridica": 0.0,
            "densidad_diplomatica": 0.0, "tiene_horas": False,
            "n_horas_mencionadas": 0,
        }

    texto = str(texto)
    palabras = re.findall(r"\b[a-záéíóúñÁÉÍÓÚÑ]+\b", texto.lower())
    n_palabras = len(palabras)
    palabras_unicas = set(palabras)
    oraciones = re.split(r"[.!?]+\s", texto)
    oraciones = [o for o in oraciones if len(o.strip()) > 5]

    # Densidad de jerga (proporción de palabras que son término de jerga)
    if n_palabras > 0:
        d_mil = sum(1 for p in palabras if p in JERGA_MILITAR) / n_palabras
        d_jur = sum(1 for p in palabras if p in JERGA_JURIDICA) / n_palabras
        d_dip = sum(1 for p in palabras if p in JERGA_DIPLOMATICA) / n_palabras
    else:
        d_mil = d_jur = d_dip = 0.0

    horas = PATRON_HORA_LIMPIO.findall(texto)

    return {
        "n_chars": len(texto),
        "n_palabras": n_palabras,
        "n_palabras_unicas": len(palabras_unicas),
        "ttr": len(palabras_unicas) / n_palabras if n_palabras > 0 else 0.0,
        "long_oracion_media": np.mean([len(o.split()) for o in oraciones]) if oraciones else 0.0,
        "densidad_militar": d_mil,
        "densidad_juridica": d_jur,
        "densidad_diplomatica": d_dip,
        "tiene_horas": len(horas) > 0,
        "n_horas_mencionadas": len(horas),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EMBEDDINGS SEMÁNTICOS (opcional)
# ═══════════════════════════════════════════════════════════════════════════════

def calcular_embeddings(textos: list[str], modelo_nombre: str = "paraphrase-multilingual-MiniLM-L12-v2"):
    """
    Calcula embeddings con sentence-transformers.
    Modelo MiniLM es rápido (~80MB). Si quieres mejor calidad usa:
        paraphrase-multilingual-mpnet-base-v2 (1.1GB, mejor para español)
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("  [skip] sentence-transformers no instalado. Saltando embeddings.")
        print("         pip install sentence-transformers")
        return None

    print(f"  Cargando modelo {modelo_nombre}...")
    model = SentenceTransformer(modelo_nombre)
    # Truncar textos a 1000 chars para velocidad (los modelos truncan a 512 tokens igualmente)
    textos_cortos = [str(t)[:2000] if pd.notna(t) else "" for t in textos]
    print(f"  Calculando embeddings de {len(textos_cortos)} documentos...")
    embeddings = model.encode(textos_cortos, show_progress_bar=False, batch_size=32)
    return embeddings


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

def normalizar_columna_fecha(df: pd.DataFrame) -> pd.DataFrame:
    """Detecta el formato de fecha del CSV y crea fecha_iso normalizada."""
    # El CSV puede tener fecha_doc (dd/mm/yyyy) o fecha_iso (yyyy-mm-dd)
    if "fecha_iso" not in df.columns:
        if "fecha_doc" in df.columns:
            def parse(s):
                if pd.isna(s):
                    return None
                s = str(s)
                try:
                    if "/" in s:
                        return datetime.strptime(s[:10], "%d/%m/%Y").strftime("%Y-%m-%d")
                    return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
                except ValueError:
                    return None
            df["fecha_iso"] = df["fecha_doc"].apply(parse)
        else:
            df["fecha_iso"] = None

    # Normalizar nombres de año
    if "anio_doc" in df.columns and "año_doc" not in df.columns:
        df["año_doc"] = df["anio_doc"]
    return df


def main(args):
    print("=" * 70)
    print("  PREPROCESAMIENTO MAESTRO 23F — Construcción del Dataset Perfecto")
    print("=" * 70)

    # ── 0. CARGA ──
    print(f"\n[0/7] Cargando {args.input}...")
    df = pd.read_csv(args.input)
    print(f"      → {len(df)} documentos, {len(df.columns)} columnas")
    df = normalizar_columna_fecha(df)

    # ── 1. METADATOS MONCLOA ──
    print("\n[1/7] Asociando metadatos estructurales (La Moncloa)...")
    df["ministerio"] = ""
    df["seccion"] = ""
    df["clasificacion"] = "sin_marca"
    sin_match = 0
    for idx, row in df.iterrows():
        m = match_moncloa(row["titulo"])
        if m:
            df.at[idx, "ministerio"] = m[1]
            df.at[idx, "seccion"] = m[2]
            df.at[idx, "clasificacion"] = m[3]
            # Rellenar fecha si falta y catálogo la tiene
            if (pd.isna(df.at[idx, "fecha_iso"]) or not df.at[idx, "fecha_iso"]) and m[4]:
                df.at[idx, "fecha_iso"] = m[4]
        else:
            sin_match += 1

        # Inferir Vista oral → Defensa/CNI
        if not df.at[idx, "ministerio"] and "vista oral" in str(row["titulo"]).lower():
            df.at[idx, "ministerio"] = "Defensa"
            df.at[idx, "seccion"] = "CNI"

        # Inferir procesamientos → Defensa/Carpeta 21800
        if not df.at[idx, "ministerio"]:
            t = str(row["titulo"]).lower()
            if any(k in t for k in [
                "comunicación procesamiento", "comunicación del procesamiento",
                "oficio dando cuenta toma de declaración",
                "traslado de peticiones de los abogados",
                "informe jurídico sobre recurso",
            ]):
                df.at[idx, "ministerio"] = "Defensa"
                df.at[idx, "seccion"] = "Archivo Defensa - Carpeta 21800"

        # Clasificación: si el catálogo dice sin_marca, intentar extraer del título
        if df.at[idx, "clasificacion"] == "sin_marca":
            c = extraer_clasificacion_titulo(row["titulo"])
            if c != "sin_marca":
                df.at[idx, "clasificacion"] = c

    print(f"      → {(df['ministerio'] != '').sum()}/{len(df)} con ministerio asignado")
    print(f"      → Clasificación: {dict(df['clasificacion'].value_counts())}")
    if sin_match > 0:
        print(f"      [aviso] {sin_match} documentos sin match en catálogo Moncloa")

    # ── 2. FASE TEMPORAL ──
    print("\n[2/7] Asignando fase histórica...")
    df["fase"] = df["fecha_iso"].apply(asignar_fase)
    print(f"      → {dict(df['fase'].value_counts().sort_index())}")

    # ── 3. EXTRACCIÓN DE TIMESTAMPS (para Caso 4) ──
    print("\n[3/7] Extrayendo cronología horaria de docs de la noche...")
    eventos_noche = []
    docs_noche = df[df["fase"] == "2_noche_golpe"]
    for _, row in docs_noche.iterrows():
        eventos = extraer_horas_doc(row["texto_completo"], row["id"], row["fecha_iso"])
        eventos_noche.extend(eventos)
    print(f"      → {len(eventos_noche)} eventos horarios extraídos de {len(docs_noche)} docs")

    if eventos_noche:
        df_eventos = pd.DataFrame(eventos_noche)
        df_eventos.to_csv(OUT_EVENTOS, index=False, encoding="utf-8-sig")
        print(f"      → Guardado en {OUT_EVENTOS}")

    # ── 4. NER + RESOLUCIÓN DE ENTIDADES ──
    print("\n[4/7] NER + resolución de entidades...")
    indice_canonico = construir_indice_canonico()

    # Intentar usar spaCy si está disponible
    nlp = None
    if not args.no_spacy:
        try:
            import spacy
            print("      Cargando modelo es_core_news_md...")
            nlp = spacy.load("es_core_news_md")
            print("      → spaCy cargado correctamente")
        except (ImportError, OSError) as e:
            print(f"      [skip] spaCy no disponible ({e}). Usando regex.")
            print("              pip install spacy && python -m spacy download es_core_news_md")

    print(f"      Procesando {len(df)} documentos...")
    df["entidades"] = None
    df["n_personas_canonicas"] = 0
    df["entidades_principales"] = ""

    for idx, row in df.iterrows():
        texto = str(row.get("texto_completo", ""))
        if nlp:
            ents = extraer_entidades_spacy(texto, nlp, indice_canonico)
        else:
            ents = extraer_entidades_regex(texto, indice_canonico)

        # Top 10 entidades canónicas (sin "otros::")
        canonicas = {k: v for k, v in ents.items() if not k.startswith("otros::")}
        top = sorted(canonicas.items(), key=lambda x: -x[1])[:10]
        df.at[idx, "entidades"] = json.dumps(canonicas, ensure_ascii=False)
        df.at[idx, "n_personas_canonicas"] = len(canonicas)
        df.at[idx, "entidades_principales"] = " | ".join(f"{k}({v})" for k, v in top)

    # Guardar diccionario canónico
    with open(OUT_CANONICAS, "w", encoding="utf-8") as f:
        json.dump(ENTIDADES_CANONICAS, f, ensure_ascii=False, indent=2)
    print(f"      → Diccionario canónico guardado en {OUT_CANONICAS}")

    # ── 5. FEATURES DERIVADAS ──
    print("\n[5/7] Calculando features derivadas...")
    feats = df["texto_completo"].apply(calcular_features)
    feats_df = pd.DataFrame(feats.tolist())
    df = pd.concat([df.reset_index(drop=True), feats_df.reset_index(drop=True)], axis=1)
    print(f"      → Añadidas {len(feats_df.columns)} columnas de features")

    # ── 6. EMBEDDINGS SEMÁNTICOS (opcional) ──
    if not args.no_embeddings:
        print("\n[6/7] Calculando embeddings semánticos...")
        embeddings = calcular_embeddings(df["texto_completo"].tolist())
        if embeddings is not None:
            np.save(OUT_EMBEDDINGS, embeddings)
            print(f"      → {embeddings.shape} embeddings guardados en {OUT_EMBEDDINGS}")
    else:
        print("\n[6/7] Embeddings desactivados (--no-embeddings).")

    # ── 7. GUARDADO ──
    print("\n[7/7] Guardando dataset maestro...")
    # Orden lógico de columnas
    cols_order = [
        "id", "titulo",
        "ministerio", "seccion", "clasificacion",
        "fecha_iso", "fase", "fecha_estimada",
        "categoria",
        "tipo", "estado", "n_paginas", "modelo_ocr",
        "n_chars", "n_palabras", "n_palabras_unicas", "ttr",
        "long_oracion_media",
        "densidad_militar", "densidad_juridica", "densidad_diplomatica",
        "tiene_horas", "n_horas_mencionadas",
        "n_personas_canonicas", "entidades_principales",
        "resumen", "palabras_clave", "actores",
        "entidades", "texto_completo",
    ]
    cols_final = [c for c in cols_order if c in df.columns]
    for c in df.columns:
        if c not in cols_final:
            cols_final.append(c)
    df = df[cols_final]
    df.to_csv(OUT_MASTER, index=False, encoding="utf-8-sig")
    print(f"      → {OUT_MASTER}")

    # ── REPORTE FINAL ──
    print("\n" + "=" * 70)
    print("  ✅ DATASET MAESTRO COMPLETADO")
    print("=" * 70)
    print(f"\n  Documentos: {len(df)}")
    print(f"  Columnas:   {len(df.columns)}")
    print(f"\n  📅 FASES:")
    for fase, n in df["fase"].value_counts().sort_index().items():
        print(f"     {fase}: {n}")
    print(f"\n  🏛️  MINISTERIOS:")
    for m, n in df["ministerio"].value_counts().items():
        if m:
            print(f"     {m}: {n}")
    print(f"\n  🔒 CLASIFICACIÓN:")
    for c, n in df["clasificacion"].value_counts().items():
        print(f"     {c}: {n}")
    print(f"\n  👤 ENTIDADES (top 10 más mencionadas):")
    todas = Counter()
    for ents_json in df["entidades"]:
        if ents_json:
            ents = json.loads(ents_json)
            for k, v in ents.items():
                if not k.startswith("otros::"):
                    todas[k] += v
    for ent, n in todas.most_common(10):
        print(f"     {ent}: {n}")
    print(f"\n  📊 FEATURES NUMÉRICAS (medias):")
    print(f"     Palabras/doc: {df['n_palabras'].mean():.0f}")
    print(f"     TTR (riqueza léxica): {df['ttr'].mean():.3f}")
    print(f"     Densidad militar: {df['densidad_militar'].mean():.3%}")
    print(f"     Densidad jurídica: {df['densidad_juridica'].mean():.3%}")
    print(f"     Densidad diplomática: {df['densidad_diplomatica'].mean():.3%}")
    print(f"\n  📁 ARCHIVOS GENERADOS:")
    print(f"     {OUT_MASTER}")
    print(f"     {OUT_CANONICAS}")
    if (OUTPUT_DIR / OUT_EVENTOS.name).exists():
        print(f"     {OUT_EVENTOS}")
    if not args.no_embeddings and (OUTPUT_DIR / OUT_EMBEDDINGS.name).exists():
        print(f"     {OUT_EMBEDDINGS}")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preprocesamiento maestro 23F")
    parser.add_argument("--input", "-i", required=True,
                        help="Ruta al CSV de entrada (ej: data/raw/dataset_23f.csv)")
    parser.add_argument("--no-spacy", action="store_true",
                        help="Forzar uso de regex en lugar de spaCy para NER")
    parser.add_argument("--no-embeddings", action="store_true",
                        help="No calcular embeddings (más rápido)")
    args = parser.parse_args()
    main(args)
