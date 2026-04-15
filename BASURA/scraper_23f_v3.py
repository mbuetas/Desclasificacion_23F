"""
Scraper 23F - Versión 3 (Dataset Enriquecido)
==============================================
Dos modos de uso:

  MODO 1 (ONLINE):  Scraping completo desde RTVE + enriquecimiento desde La Moncloa
      python scraper_23f_v3.py --full

  MODO 2 (OFFLINE): Enriquece tu CSV existente sin hacer scraping
      python scraper_23f_v3.py --enrich dataset_23f.csv

El Modo 2 es el recomendado si ya tienes el CSV de la v2: corrige fechas,
añade ministerio/sección/clasificación, limpia actores y recategoriza.

Requisitos:
    pip install requests beautifulsoup4 pandas tqdm
"""

import argparse
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup
# tqdm is optional — fallback to simple print if not available
try:
    from tqdm import tqdm
except ImportError:
    class tqdm:
        def __init__(self, iterable=None, **kwargs):
            self.iterable = iterable
            self.desc = kwargs.get("desc", "")
            self.n = 0
        def __iter__(self):
            for item in self.iterable:
                self.n += 1
                yield item
        def update(self, n=1):
            self.n += n
        def close(self):
            pass

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────
BASE_URL_RTVE = "https://23fbuscador.rtve.es"
BASE_URL_MONCLOA = "https://www.lamoncloa.gob.es/consejodeministros/paginas/desclasificacion-documentos-23f.aspx"
ID_INICIO = 1859
OUTPUT_CSV = "dataset_23f_v3.csv"
DELAY = 1.0
# ──────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

session = requests.Session()
session.headers.update(HEADERS)


# ═══════════════════════════════════════════════════════════════════════════════
# PARTE A — CATÁLOGO DE LA MONCLOA (metadatos estructurales)
# ═══════════════════════════════════════════════════════════════════════════════

# Catálogo completo extraído de la web de La Moncloa el 13/04/2026.
# Cada entrada tiene: título (para match con el CSV), ministerio, sección,
# clasificación de seguridad, URL del PDF y fecha exacta del título.
#
# Este catálogo es ESTÁTICO: no hace falta scraping.

CATALOGO_MONCLOA = [
    # ── MINISTERIO DEL INTERIOR — Guardia Civil ──
    {
        "titulo_match": "Transcripción de conversación telefónica de (presuntamente) García Carres y Tejero",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "interior/guardia-civil/23F_1._Conversacion_telefonica_GARCIA_CARRES_y_Tcol._TEJERO.pdf",
    },
    {
        "titulo_match": "Transcripción de conversación telefónica de García Carres con otra persona",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "interior/guardia-civil/23F_2._Conversacion_telefonica_GARCIA_CARRES.pdf",
    },
    {
        "titulo_match": "Conversaciones telefónicas de (presuntamente) la unidad militar El Pardo",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-24",
        "url_pdf": "interior/guardia-civil/23F_3._Conversaciones_telefonicas_unidad_militar_El_Pardo.pdf",
    },
    {
        "titulo_match": "Documentación con una presunta planificación del golpe, manuscrita",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1980-01-01",
        "url_pdf": "interior/guardia-civil/23F_4._Documento_planificacion_del_golpe.pdf",
    },
    {
        "titulo_match": "Documento manuscrito de posible planificación del golpe",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": None,
        "url_pdf": "interior/guardia-civil/23F_5._Documento_manuscrito_planificacion_del_golpe.pdf",
    },
    {
        "titulo_match": "Transcripción de cintas grabadas con conversaciones telefónicas con varias personas intervenidas a la esposa de Tejero",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "interior/guardia-civil/23F6TR_1.PDF",
    },
    {
        "titulo_match": "Nota del EM de la Guardia Civil con una secuencia parcial de los hechos del asalto al Congreso",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "interior/guardia-civil/23F_7._Notas_Informativas_2_Seccion_EM_desarrollo_hechos.pdf",
    },
    {
        "titulo_match": "Télex interiores y de agencias recibidos en 2ª sección EM el día 23-F",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "interior/guardia-civil/23F_8._Telex_interiores_y_de_Agencias_recibidos_en_2_Seccion_EM.pdf",
    },
    {
        "titulo_match": "Oficio zona País Vasco que expresa una comunicación del teniente coronel Tejero",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1975-01-01",
        "url_pdf": "interior/guardia-civil/23F_9._Oficio_dimanante_Zona_del_Pais_Vasco_disposiciones_sobre_Tejero.pdf",
    },
    {
        "titulo_match": "Nota sobre la comparecencia del teniente coronel Tejero informando sobre una reunión en la cafetería Galaxia",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1978-01-01",
        "url_pdf": "interior/guardia-civil/23F_10._Nota_comparecencia_Tejero_Galaxia.pdf",
    },
    {
        "titulo_match": "Nota Informativa sobre la repercusión en prensa del arresto de Tejero en 1978",
        "ministerio": "Interior",
        "seccion": "Guardia Civil",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1978-01-01",
        "url_pdf": "interior/guardia-civil/23F_11._Nota_Informativa_repercusion_prensa_arresto_Tejero_antes_1981.pdf",
    },

    # ── MINISTERIO DEL INTERIOR — Dirección General de la Policía ──
    {
        "titulo_match": "Situación actual en las distintas regiones policiales",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": None,  # hay 3 fechas: 24, 25, 26 feb — se matchean individualmente
        "url_pdf": None,
    },
    {
        "titulo_match": "Fuerza Nueva. El divorcio del Rey con el Ejército",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-03-12",
        "url_pdf": "interior/policia/12-03-81_NOTA_INFORMATIVA_SOBRE_FUERZA_NUEVA.pdf",
    },
    {
        "titulo_match": "Ayudas a los implicados en el 23-F. Desde altas instancias castrenses",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-03-18",
        "url_pdf": "interior/policia/18-03-81_NOTA_INFORMATIVA_SOBRE_LA_AYUDA_A_LOS_IMPLICADOS_23F.pdf",
    },
    {
        "titulo_match": "Operación Ariete",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-03-18",
        "url_pdf": "interior/policia/18-03-81_NOTA_INFORMATIVA_SOBRE_LA_OPERACION_ARIETE.pdf",
    },
    {
        "titulo_match": "Bloqueada una cuenta de la asociación de mujeres de militares y policías",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-03-27",
        "url_pdf": "interior/policia/27-03-81_NOTA_INFORMATIVA_SOBRE_BLOQUEO_DE_CUENTA_DE_ASOC_DE_MUJERES_DE_MILITARES.pdf",
    },
    {
        "titulo_match": "Partido Comunista de España PCE. Intento de la extrema derecha",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-05-11",
        "url_pdf": "interior/policia/11-05-81_NOTA_INFORMATIVA_SOBRE_EL_PCE.pdf",
    },
    {
        "titulo_match": "Campaña de formación del PSOE sobre involución",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-05-13",
        "url_pdf": "interior/policia/13-05_1.PDF",
    },
    {
        "titulo_match": "Apoyo económico a los implicados en el 23-F",
        "ministerio": "Interior",
        "seccion": "Policía",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1983-05-10",
        "url_pdf": "interior/policia/10-05-83_NOTA_INFORMATIVA_SOBRE_APOYO_ECONOMICO_A_LOS_IMPLICADOS.pdf",
    },

    # ── MINISTERIO DEL INTERIOR — Otra documentación ──
    {
        "titulo_match": "Policía Nacional. Informe de situación. Marca: reservado-confidencial",
        "ministerio": "Interior",
        "seccion": "Archivo Interior",
        "clasificacion": "RESERVADO",
        "fecha_titulo": "1981-11-12",
        "url_pdf": "interior/archivo/1_PN_Informe_Situacion_12-11-81_desp.pdf",
    },
    {
        "titulo_match": "Índices de subversión en las FAS",
        "ministerio": "Interior",
        "seccion": "Archivo Interior",
        "clasificacion": "SECRETO",
        "fecha_titulo": "1981-12-01",
        "url_pdf": "interior/archivo/2_Indices_de_subversion_en_las_FAS_DIC_1981.pdf",
    },
    {
        "titulo_match": "acotaciones al desarrollo del juicio",
        "ministerio": "Interior",
        "seccion": "Archivo Interior",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-01-01",
        "url_pdf": "interior/archivo/3_Juicio_del_23-F_desp.pdf",
    },
    {
        "titulo_match": "Campaña contra S.M",
        "ministerio": "Interior",
        "seccion": "Archivo Interior",
        "clasificacion": "sin_marca",
        "fecha_titulo": None,
        "url_pdf": "interior/archivo/4_campana_contra_SM.pdf",
    },
    {
        "titulo_match": "Involucionismo político provocado por posible golpe militar",
        "ministerio": "Interior",
        "seccion": "Archivo Interior",
        "clasificacion": "sin_marca",
        "fecha_titulo": None,
        "url_pdf": "interior/archivo/5_INVOLUCIONISMO_POLITICO_PROVOCADO_POSIBLE_GOLPE_MILITAR_desp.pdf",
    },
    {
        "titulo_match": "Posible golpe de estado",
        "ministerio": "Interior",
        "seccion": "Archivo Interior",
        "clasificacion": "sin_marca",
        "fecha_titulo": None,
        "url_pdf": "interior/archivo/6_POSIBLE_GOLPE_DE_ESTADO_desp.pdf",
    },
    {
        "titulo_match": "Notas de 1983 sobre",
        "ministerio": "Interior",
        "seccion": "Archivo Interior",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1983-01-01",
        "url_pdf": "interior/archivo/7_Notas_1983_desp.pdf",
    },

    # ── MINISTERIO DE DEFENSA — CNI ──
    {
        "titulo_match": "Guión que sirvió de base para la reunión de S.M. el Rey",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-12-14",
        "url_pdf": "defensa/cni/Documento_1_R.pdf",
    },
    {
        "titulo_match": "Relación CESID (Dirección) - General Jefe del Estado Mayor",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "defensa/cni/Documento_2_R.pdf",
    },
    {
        "titulo_match": "Relación CESID (Dirección) - PREJUJEM",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "defensa/cni/Documento_3_R.pdf",
    },
    {
        "titulo_match": "Resumen de la actuación del Departamento de Defensa Interna",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "defensa/cni/Documento_4_R.pdf",
    },
    {
        "titulo_match": "Actitud del CESID ante la situación provocada por los incidentes en el Congreso",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "defensa/cni/Documento_5_R.pdf",
    },
    {
        "titulo_match": "Informe sobre la participación de miembros de la AOME",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "defensa/cni/Documento_6_R.pdf",
    },
    {
        "titulo_match": "Investigación y declaraciones personal AOME por JDDI",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-04-09",
        "url_pdf": "defensa/cni/Documento_7_R.pdf",
    },
    {
        "titulo_match": "Carta de José Cortina Prieto para Emilio Manglano",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-10-03",
        "url_pdf": "defensa/cni/Documento_8_R.pdf",
    },
    {
        "titulo_match": "Comisiones militares (10 de marzo",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-03-10",
        "url_pdf": "defensa/cni/Documento_9_R.pdf",
    },
    {
        "titulo_match": "Comisiones militares en la vista de la causa",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-03-01",
        "url_pdf": "defensa/cni/Documento_10_R.pdf",
    },
    {
        "titulo_match": "Ambiente en los cuarteles",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-02-25",
        "url_pdf": "defensa/cni/Documento_11_R.pdf",
    },
    {
        "titulo_match": "anunciada libertad provisional de algunos procesados",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-04-19",
        "url_pdf": "defensa/cni/Documento_12_R.pdf",
    },
    {
        "titulo_match": "Reunión sobre acontecimientos recientes",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-03-08",
        "url_pdf": "defensa/cni/Documento_13_R.pdf",
    },
    {
        "titulo_match": "Algunos datos para una crónica de un golpe anunciado",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": None,
        "url_pdf": "defensa/cni/Documento_14_R.pdf",
    },
    {
        "titulo_match": "Incidente entre la defensa del Teniente General Milans del Bosch y la prensa",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-04-28",
        "url_pdf": "defensa/cni/Documento_15_R.pdf",
    },
    {
        "titulo_match": "Comentarios sobre la sentencia del 23-F",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-06-06",
        "url_pdf": "defensa/cni/Documento_16_R.pdf",
    },
    {
        "titulo_match": "papel de la JUJEM en la crisis político-militar",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "defensa/cni/Documento_17_R.pdf",
    },
    {
        "titulo_match": "Reacciones ante la sentencia 23-F",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-06-04",
        "url_pdf": "defensa/cni/Documento_18_R.pdf",
    },
    {
        "titulo_match": "Estado de opinión sobre las sentencias",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-06-07",
        "url_pdf": "defensa/cni/Documento_19_R.pdf",
    },
    {
        "titulo_match": "Relato de los sucesos de los días 23 y 24 de febrero",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": "defensa/cni/Documento_20_R.pdf",
    },
    {
        "titulo_match": "Semestral de la amenaza interior",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-10",
        "url_pdf": "defensa/cni/Documento_72_R.pdf",
    },
    {
        "titulo_match": "Relaciones entre algunos militares y paisanos armados",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-04-14",
        "url_pdf": "defensa/cni/Documento_73_R.pdf",
    },
    {
        "titulo_match": "Referencias en los medios de comunicación social a personal del centro",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-04-30",
        "url_pdf": "defensa/cni/Documento_74_R.pdf",
    },
    {
        "titulo_match": "Procesamiento de un jefe",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-05-22",
        "url_pdf": "defensa/cni/Documento_75_R.pdf",
    },
    {
        "titulo_match": "Sánchez Valiente en Roma",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-06-30",
        "url_pdf": "defensa/cni/Documento_76_R.pdf",
    },
    {
        "titulo_match": "Capitán Sánchez Valiente (2 de junio",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-06-02",
        "url_pdf": "defensa/cni/Documento_77_R.pdf",
    },
    {
        "titulo_match": "Traslado de un escrito",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": None,
        "url_pdf": "defensa/cni/Documento_78_R.pdf",
    },
    {
        "titulo_match": "Interesando comparecencia del capitán de la Guardia Civil José Ramón Tostón",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-01-05",
        "url_pdf": "defensa/cni/Documento_79_R.pdf",
    },
    {
        "titulo_match": "Solicitando datos sobre el Cte. Cortina y equipos de transmisiones",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-01-08",
        "url_pdf": "defensa/cni/Documento_80_R.pdf",
    },
    {
        "titulo_match": "Certificación solicitada para su unión a la causa 2/81 del CSJM",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-01-12",
        "url_pdf": "defensa/cni/Documento_81_R.pdf",
    },
    {
        "titulo_match": "Comparecimiento en Consejo Supremo un oficial y dos guardias",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-01-12",
        "url_pdf": "defensa/cni/Documento_82_R.pdf",
    },
    {
        "titulo_match": "entrevistas de S.M el Rey con militares implicados",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-02-05",
        "url_pdf": "defensa/cni/Documento_83_R.pdf",
    },
    {
        "titulo_match": "Revisión de la sentencia dictada en la causa 2/81",
        "ministerio": "Defensa",
        "seccion": "CNI",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1987-10-19",
        "url_pdf": "defensa/cni/Documento_84_R.pdf",
    },

    # ── MINISTERIO DE DEFENSA — Archivo (clasificación desde título) ──
    {
        "titulo_match": "parte por abandono de destino del Cap. Sánchez Valiente",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Causa 94/81",
        "clasificacion": "RESERVADO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Causa_9481_Reservado_parte_por_abandono_de_destino_del_Cap_Sanchez_Valiente.pdf",
    },
    {
        "titulo_match": "Hoja de servicios del Cap. Sánchez Valiente",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Causa 94/81",
        "clasificacion": "RESERVADO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Causa_9481_Reservado_Hoja_de_servicios_del_Cap_Sanchez_Valiente.pdf",
    },
    {
        "titulo_match": "Informe de Asesoría Jurídica General sobre su situación administrativa",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Causa 94/81",
        "clasificacion": "RESERVADO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Causa_9481_reservado_Informe_de_Asesoria_Juridica_General.pdf",
    },
    {
        "titulo_match": "comunicando sanción a consejeros del Consejo Supremo",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Carpeta 21801",
        "clasificacion": "SECRETO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Carpeta_21801_Secreto_comunicando_sancion_a_consejeros.pdf",
    },
    {
        "titulo_match": "comunicando levantamiento de incomunicación de Tejero",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Carpeta 21802",
        "clasificacion": "SECRETO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Carpeta_21802_Secreto_comunicando_levantamiento_de_incomunicacion_de_Tejero.pdf",
    },
    {
        "titulo_match": "telex dando instrucciones sobre medidas de seguridad con las visitas a Tejero",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Carpeta 21802",
        "clasificacion": "SECRETO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Carpeta_21802_Secreto_copita_de_telex.pdf",
    },
    {
        "titulo_match": "recurso de queja de Milans del Bosch sobre su detención",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Carpeta 21804",
        "clasificacion": "RESERVADO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Carpeta_21804_Reservado_dacion_en_cuenta_de_recurso_de_queja.pdf",
    },
    {
        "titulo_match": "circunstancias de la detención de Milans del Bosch",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Carpeta 21804",
        "clasificacion": "SECRETO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Carpeta_21804_Secreto_informacion_sobre_circunstancias.pdf",
    },
    {
        "titulo_match": "distribución de los procesados por la Causa 2/81 en diferentes Unidades",
        "ministerio": "Defensa",
        "seccion": "Archivo Defensa - Carpeta 21804",
        "clasificacion": "SECRETO",
        "fecha_titulo": None,
        "url_pdf": "defensa/Carpeta_21804_Secreto_distribucion_de_los_procesados.pdf",
    },

    # ── MINISTERIO DE ASUNTOS EXTERIORES ──
    {
        "titulo_match": "AGMAE_R39017",
        "ministerio": "Exteriores",
        "seccion": "Subsecretaría - Expediente EEUU",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": None,
    },
    {
        "titulo_match": "AGMAE_R40201",
        "ministerio": "Exteriores",
        "seccion": "DG Asuntos Consulares",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": None,
    },
    {
        "titulo_match": "AGA-83-07633",
        "ministerio": "Exteriores",
        "seccion": "DG Iberoamérica",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": None,
    },
    {
        "titulo_match": "AGA-83-08764",
        "ministerio": "Exteriores",
        "seccion": "DG Iberoamérica - Juicio",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1982-01-01",
        "url_pdf": None,
    },
    {
        "titulo_match": "AGA-83-09301",
        "ministerio": "Exteriores",
        "seccion": "DG Política Exterior Europa",
        "clasificacion": "sin_marca",
        "fecha_titulo": "1981-02-23",
        "url_pdf": None,
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# PARTE B — FUNCIONES DE ENRIQUECIMIENTO
# ═══════════════════════════════════════════════════════════════════════════════


def extraer_fecha_titulo(titulo: str) -> str | None:
    """
    Extrae la fecha EXACTA del paréntesis del título.
    Ej: "Vista oral 2/81 ... (19 de febrero de 1982)" → "1982-02-19"
    Prioriza el formato entre paréntesis que es la fecha del documento.
    """
    titulo = str(titulo)

    # 1. Buscar fecha entre paréntesis — es la más fiable
    parentesis = re.findall(r'\(([^)]+)\)', titulo)
    for p in parentesis:
        m = re.search(
            r'(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|'
            r'agosto|septiembre|octubre|noviembre|diciembre)\s+de[l]?\s+(\d{4})',
            p, re.IGNORECASE
        )
        if m:
            dia = int(m.group(1))
            mes = MESES[m.group(2).lower()]
            año = int(m.group(3))
            try:
                return datetime(año, mes, dia).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 2. Buscar "24 de febrero del 1981" (con "del")
    m = re.search(
        r'(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|'
        r'agosto|septiembre|octubre|noviembre|diciembre)\s+de[l]?\s+(\d{4})',
        titulo, re.IGNORECASE
    )
    if m:
        dia = int(m.group(1))
        mes = MESES[m.group(2).lower()]
        año = int(m.group(3))
        try:
            return datetime(año, mes, dia).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def extraer_clasificacion_titulo(titulo: str) -> str:
    """Extrae SECRETO/RESERVADO del prefijo del título."""
    titulo_upper = str(titulo).upper().strip()
    if titulo_upper.startswith("SECRETO"):
        return "SECRETO"
    if titulo_upper.startswith("RESERVADO"):
        return "RESERVADO"
    # Check for "Marca: reservado" pattern
    if re.search(r'marca:\s*reservado', titulo, re.IGNORECASE):
        return "RESERVADO"
    if re.search(r'marca:\s*secreto', titulo, re.IGNORECASE):
        return "SECRETO"
    return "sin_marca"


def match_moncloa(titulo: str) -> dict | None:
    """Busca la entrada del catálogo de Moncloa que mejor encaja con el título."""
    titulo_lower = str(titulo).lower()
    best_match = None
    best_len = 0

    for entry in CATALOGO_MONCLOA:
        pattern = entry["titulo_match"].lower()
        if pattern in titulo_lower and len(pattern) > best_len:
            best_match = entry
            best_len = len(pattern)

    return best_match


def limpiar_actores(actores_raw: str, titulo: str = "") -> str:
    """
    Limpia el campo de actores eliminando basura del LLM.
    - Elimina números sueltos, frases explicativas, entradas vacías
    - Deduplica
    - Normaliza nombres
    """
    if pd.isna(actores_raw) or not str(actores_raw).strip():
        return ""

    partes = str(actores_raw).split("|")
    limpios = []
    vistos = set()

    # Patrones de basura a eliminar
    basura = re.compile(
        r'^[\d\s]+$'                                 # solo números
        r'|^(nan|none|null)$'                        # nulos
        r'|\bno hay\b'                               # "no hay lugares..."
        r'|\bno se menciona\b'
        r'|\bsin más especificación\b'
        r'|\bprobablemente\b'
        r'|\bcontexto no claro\b'
        r'|\bposible\s+(área|dependencia|ubicación)\b'
        r'|\brefiriéndose\b'
        r'|\binstitución\b.*\bgubernativa\b'
        r'|\blugar o institución no mencionada\b',
        re.IGNORECASE
    )

    for parte in partes:
        actor = parte.strip()
        if not actor or len(actor) < 3:
            continue
        if basura.search(actor):
            continue
        # Normalizar
        actor_norm = actor.lower().strip()
        if actor_norm not in vistos:
            vistos.add(actor_norm)
            limpios.append(actor)

    return " | ".join(limpios)


def categorizar_v3(row: pd.Series) -> str:
    """Categorización mejorada usando ministerio, sección, fecha y contenido."""
    titulo = str(row.get("titulo", "")).lower()
    resumen = str(row.get("resumen", "")).lower()
    seccion = str(row.get("seccion", "")).lower()
    ministerio = str(row.get("ministerio", "")).lower()
    fecha = str(row.get("fecha_iso", ""))
    todo = titulo + " " + resumen

    # ── Vista oral del juicio → juicio_1982
    if "vista oral" in titulo:
        return "juicio_1982"
    if "información integrada" in titulo and "1982" in titulo:
        return "juicio_1982"

    # ── Documentos CNI sobre juicio
    juicio_kw = [
        "comisiones militares en la vista",
        "comisiones militares (10 de marzo",
        "comentarios sobre la sentencia",
        "reacciones ante la sentencia",
        "estado de opinión sobre las sentencias",
        "acotaciones al desarrollo del juicio",
        "ambiente en los cuarteles",
        "recurso de casación",
        "libertad de condenados",
        "petición de indulto",
    ]
    if any(kw in todo for kw in juicio_kw):
        return "juicio_1982"

    # ── Noche del 23F y días inmediatamente posteriores
    noche_kw = [
        "dentro del congreso", "asalto al congreso",
        "el día 23-f", "23-f informando",
        "esposa de tejero", "garcia carres y tejero",
        "garcia carres con otra persona",
        "el pardo (24 de febrero", "planificación del golpe",
        "secuencia parcial de los hechos",
        "ocupación del palacio del congreso",
        "situación actual en las distintas regiones",
        "relato de los sucesos de los días 23 y 24",
        "incidentes en el congreso",
        "actitud del cesid ante la situación",
        "actuación del departamento de defensa interna",
        "participación de miembros de la aome",
        "relación cesid", "prejujem",
        "guión que sirvió de base",
        "posible golpe de estado",
        "involucionismo político provocado",
        "papel de la jujem en la crisis",
        "campaña contra s.m",
        "manuscrito de posible planificación",
    ]
    if any(kw in todo for kw in noche_kw):
        return "noche_23f"

    # ── Exteriores: solidaridad y diplomacia
    if ministerio == "exteriores":
        if "juicio" in seccion.lower():
            return "reaccion_internacional_juicio"
        return "reaccion_internacional"

    # ── Carpeta 21800 (procesamientos)
    if "carpeta 21800" in seccion:
        return "procesamientos"
    if "carpeta 2180" in seccion:
        return "procesamientos"
    if "causa 94/81" in seccion:
        return "procesamientos"

    # ── Por fecha
    if fecha and fecha != "None" and fecha != "nan":
        try:
            dt = datetime.strptime(fecha[:10], "%Y-%m-%d")
            # 23-26 feb 1981 → noche_23f
            if dt.year == 1981 and dt.month == 2 and 23 <= dt.day <= 26:
                return "noche_23f"
            if dt.year == 1981:
                return "contexto_1981"
            if dt.year == 1982:
                return "juicio_1982"
            return f"otro_{dt.year}"
        except ValueError:
            pass

    return "sin_clasificar"


# ═══════════════════════════════════════════════════════════════════════════════
# PARTE C — SCRAPING RTVE (igual que v2 pero con enriquecimiento integrado)
# ═══════════════════════════════════════════════════════════════════════════════

def parsear_documento(html: str, doc_id: int) -> dict:
    """Extrae todos los campos de un documento dado su HTML."""
    soup = BeautifulSoup(html, "html.parser")

    doc = {
        "id": doc_id,
        "titulo": "",
        "tipo": "",
        "estado": "",
        "n_paginas": 0,
        "modelo_ocr": "",
        "resumen": "",
        "palabras_clave": "",
        "texto_completo": "",
        "fecha_iso": None,
        "año_doc": None,
        "mes_doc": None,
        "dia_doc": None,
        "actores": "",
        "id_anterior": None,
        "id_siguiente": None,
    }

    # ── TÍTULO ──
    h2 = soup.find("h2")
    if h2:
        doc["titulo"] = h2.get_text(strip=True)

    # ── METADATOS ──
    detail_grid = soup.find("div", class_="detail-grid")
    if detail_grid:
        for div in detail_grid.find_all("div"):
            texto = div.get_text(separator=" ", strip=True)
            if "Tipo:" in texto:
                doc["tipo"] = texto.replace("Tipo:", "").strip()
            elif "Estado:" in texto:
                doc["estado"] = texto.replace("Estado:", "").strip()
            elif "Páginas:" in texto:
                try:
                    doc["n_paginas"] = int(texto.replace("Páginas:", "").strip())
                except ValueError:
                    doc["n_paginas"] = texto.replace("Páginas:", "").strip()
            elif "Modelo:" in texto:
                doc["modelo_ocr"] = texto.replace("Modelo:", "").strip()

    # ── RESUMEN ──
    text_box = soup.find("p", class_="text-box")
    if text_box:
        doc["resumen"] = text_box.get_text(separator=" ", strip=True)

    # ── PALABRAS CLAVE ──
    kws = []
    for h3 in soup.find_all("h3"):
        if any(x in h3.get_text().lower() for x in ["palabra", "clave", "keyword", "tag"]):
            section = h3.find_parent("section") or h3.find_next_sibling()
            if section:
                tags = section.find_all(["span", "a", "li", "p"])
                for t in tags:
                    txt = t.get_text(strip=True)
                    if txt and txt not in ["Palabras clave", "Keywords"]:
                        kws.append(txt)
            break
    if not kws:
        tags = soup.find_all(
            ["span", "a"],
            class_=lambda x: x and any(k in x.lower() for k in ["tag", "keyword", "label", "badge", "chip"]),
        )
        kws = [t.get_text(strip=True) for t in tags if t.get_text(strip=True)]
    doc["palabras_clave"] = " | ".join(kws)

    # ── TEXTO COMPLETO ──
    pre = soup.find("pre")
    if pre:
        doc["texto_completo"] = pre.get_text(separator="\n", strip=True)
    else:
        for article in soup.find_all("article"):
            h3 = article.find("h3")
            if h3 and any(x in h3.get_text().lower() for x in ["texto", "transcri", "ocr", "completo"]):
                doc["texto_completo"] = article.get_text(separator="\n", strip=True)
                break

    # ── NAVEGACIÓN ──
    for link in soup.find_all("a", class_="nav-doc-button"):
        href = link.get("href", "")
        texto_link = link.get_text(strip=True).lower()
        m = re.search(r"/document/ocr/(\d+)", href)
        if m:
            id_nav = int(m.group(1))
            if "anterior" in texto_link:
                doc["id_anterior"] = id_nav
            elif "siguiente" in texto_link:
                doc["id_siguiente"] = id_nav

    return doc


def obtener_todos_los_ids() -> list[int]:
    """Navega por todos los documentos siguiendo Anterior/Siguiente."""
    print("\n[1/3] Descubriendo IDs...")
    ids_visitados = set()
    ids_por_visitar = {ID_INICIO}

    try:
        r = session.get(f"{BASE_URL_RTVE}/?page_size=25&page=1", timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for link in soup.find_all("a", href=re.compile(r"/document/ocr/\d+")):
            m = re.search(r"/document/ocr/(\d+)", link["href"])
            if m:
                ids_por_visitar.add(int(m.group(1)))
        print(f"  → {len(ids_por_visitar)} IDs en listado inicial")
    except Exception as e:
        print(f"  [!] No se pudo acceder al listado: {e}")

    pbar = tqdm(desc="  Descubriendo", unit=" docs")
    while ids_por_visitar:
        doc_id = ids_por_visitar.pop()
        if doc_id in ids_visitados:
            continue
        try:
            r = session.get(f"{BASE_URL_RTVE}/document/ocr/{doc_id}?page_size=25&page=1", timeout=15)
            if r.status_code == 200:
                ids_visitados.add(doc_id)
                soup = BeautifulSoup(r.text, "html.parser")
                for link in soup.find_all("a", class_="nav-doc-button"):
                    href = link.get("href", "")
                    m = re.search(r"/document/ocr/(\d+)", href)
                    if m:
                        nuevo_id = int(m.group(1))
                        if nuevo_id not in ids_visitados:
                            ids_por_visitar.add(nuevo_id)
                pbar.update(1)
            else:
                ids_visitados.add(doc_id)
        except Exception as e:
            print(f"\n  [ERROR] ID {doc_id}: {e}")
        time.sleep(DELAY * 0.5)

    pbar.close()
    print(f"  → Total IDs: {len(ids_visitados)}")
    return sorted(ids_visitados)


def scrape_documento(doc_id: int) -> dict | None:
    url = f"{BASE_URL_RTVE}/document/ocr/{doc_id}?page_size=25&page=1"
    try:
        r = session.get(url, timeout=15)
        if r.status_code == 200:
            return parsear_documento(r.text, doc_id)
        return None
    except Exception as e:
        print(f"\n  [ERROR] ID {doc_id}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PARTE D — PIPELINE DE ENRIQUECIMIENTO
# ═══════════════════════════════════════════════════════════════════════════════


def enriquecer_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica todas las mejoras al DataFrame."""
    print(f"\n  Enriqueciendo {len(df)} documentos...")

    # 1. FECHA: extraer del título (más fiable que del texto)
    print("  [1/5] Corrigiendo fechas...")
    for idx, row in df.iterrows():
        fecha_titulo = extraer_fecha_titulo(row["titulo"])
        if fecha_titulo:
            df.at[idx, "fecha_iso"] = fecha_titulo
            dt = datetime.strptime(fecha_titulo, "%Y-%m-%d")
            df.at[idx, "año_doc"] = dt.year
            df.at[idx, "mes_doc"] = dt.month
            df.at[idx, "dia_doc"] = dt.day

    # 2. MONCLOA: match con catálogo para ministerio/sección
    print("  [2/5] Asociando metadatos de La Moncloa...")
    df["ministerio"] = ""
    df["seccion"] = ""
    df["clasificacion"] = ""
    df["url_pdf_moncloa"] = ""

    for idx, row in df.iterrows():
        titulo = str(row["titulo"])

        # Match con catálogo
        moncloa = match_moncloa(titulo)
        if moncloa:
            df.at[idx, "ministerio"] = moncloa["ministerio"]
            df.at[idx, "seccion"] = moncloa["seccion"]
            if moncloa["url_pdf"]:
                df.at[idx, "url_pdf_moncloa"] = (
                    "https://www.lamoncloa.gob.es/consejodeministros/Documents/"
                    f"2026/desclasificacion-documentos-23F/{moncloa['url_pdf']}"
                )
            # Usar fecha de Moncloa si no tenemos fecha aún
            if pd.isna(df.at[idx, "fecha_iso"]) or df.at[idx, "fecha_iso"] is None:
                if moncloa["fecha_titulo"]:
                    df.at[idx, "fecha_iso"] = moncloa["fecha_titulo"]
                    dt = datetime.strptime(moncloa["fecha_titulo"], "%Y-%m-%d")
                    df.at[idx, "año_doc"] = dt.year
                    df.at[idx, "mes_doc"] = dt.month
                    df.at[idx, "dia_doc"] = dt.day

        # Clasificación de seguridad: primero del título, luego del catálogo
        clasif = extraer_clasificacion_titulo(titulo)
        if clasif == "sin_marca" and moncloa:
            clasif = moncloa.get("clasificacion", "sin_marca")
        df.at[idx, "clasificacion"] = clasif

        # Inferir ministerio para Vista oral (CNI)
        if not df.at[idx, "ministerio"] and "vista oral" in titulo.lower():
            df.at[idx, "ministerio"] = "Defensa"
            df.at[idx, "seccion"] = "CNI"

        # Inferir Carpeta 21800 para procesamientos
        if not df.at[idx, "ministerio"]:
            if any(kw in titulo.lower() for kw in [
                "comunicación procesamiento", "comunicación del procesamiento",
                "oficio dando cuenta toma de declaración",
                "traslado de peticiones de los abogados",
                "informe jurídico sobre recurso",
            ]):
                df.at[idx, "ministerio"] = "Defensa"
                df.at[idx, "seccion"] = "Archivo Defensa - Carpeta 21800"

    # 3. ACTORES: limpiar
    print("  [3/5] Limpiando actores...")
    df["actores"] = df.apply(
        lambda r: limpiar_actores(r.get("actores", ""), r.get("titulo", "")),
        axis=1,
    )

    # 4. CATEGORÍA: recategorizar con la nueva info
    print("  [4/5] Recategorizando...")
    df["categoria"] = df.apply(categorizar_v3, axis=1)

    # 5. ORDEN DE COLUMNAS
    print("  [5/5] Ordenando columnas...")
    cols_order = [
        "id", "titulo",
        "ministerio", "seccion", "clasificacion",
        "tipo", "estado", "n_paginas", "modelo_ocr",
        "fecha_iso", "año_doc", "mes_doc", "dia_doc",
        "categoria",
        "resumen", "palabras_clave", "actores",
        "texto_completo",
        "url_pdf_moncloa",
    ]
    # Solo incluir columnas que existan
    cols_final = [c for c in cols_order if c in df.columns]
    # Añadir columnas extra que no estén en el orden
    for c in df.columns:
        if c not in cols_final:
            cols_final.append(c)
    df = df[cols_final]

    return df


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════


def modo_enrich(csv_path: str):
    """Modo offline: enriquece un CSV existente."""
    print("=" * 60)
    print("  SCRAPER 23F v3 — MODO ENRIQUECIMIENTO")
    print("=" * 60)

    print(f"\n  Leyendo {csv_path}...")
    df = pd.read_csv(csv_path)
    print(f"  → {len(df)} documentos cargados")

    df = enriquecer_dataframe(df)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    # Reporte
    print(f"\n{'=' * 60}")
    print(f"  ✅ COMPLETADO: {len(df)} documentos enriquecidos")
    print(f"\n  Distribución por categoría:")
    print(df["categoria"].value_counts().to_string())
    print(f"\n  Distribución por ministerio:")
    print(df["ministerio"].value_counts().to_string())
    print(f"\n  Distribución por clasificación:")
    print(df["clasificacion"].value_counts().to_string())
    print(f"\n  Fechas exactas (día != 1): {(df['dia_doc'].dropna().astype(int) != 1).sum()}/{len(df)}")
    print(f"  Documentos con fecha: {df['fecha_iso'].notna().sum()}/{len(df)}")
    print(f"\n  💾 Guardado en: {OUTPUT_CSV}")
    print(f"{'=' * 60}")

    print(f"\n  Muestra (primeros 10):")
    print(df[["id", "titulo", "ministerio", "clasificacion", "fecha_iso", "categoria"]]
          .head(10).to_string(max_colwidth=60))


def modo_full():
    """Modo online: scraping completo + enriquecimiento."""
    print("=" * 60)
    print("  SCRAPER 23F v3 — MODO COMPLETO")
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
                pd.DataFrame(documentos).to_csv("dataset_23f_parcial.csv", index=False, encoding="utf-8-sig")
        time.sleep(DELAY)

    df = pd.DataFrame(documentos)
    df = df.drop(columns=["id_anterior", "id_siguiente"], errors="ignore")

    print(f"\n[3/3] Enriqueciendo dataset...")
    df = enriquecer_dataframe(df)
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n{'=' * 60}")
    print(f"  ✅ COMPLETADO: {len(df)} documentos")
    print(f"\n  Distribución por categoría:")
    print(df["categoria"].value_counts().to_string())
    print(f"\n  💾 Guardado en: {OUTPUT_CSV}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Scraper 23F v3")
    parser.add_argument("--full", action="store_true", help="Scraping completo desde RTVE")
    parser.add_argument("--enrich", type=str, help="Enriquece un CSV existente (sin scraping)")
    args = parser.parse_args()

    if args.enrich:
        modo_enrich(args.enrich)
    elif args.full:
        modo_full()
    else:
        print("Uso:")
        print("  python scraper_23f_v3.py --full              # Scraping completo")
        print("  python scraper_23f_v3.py --enrich datos.csv  # Enriquecer CSV existente")
        sys.exit(1)


if __name__ == "__main__":
    main()
