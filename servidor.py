"""
servidor.py v3.0 — API Flask para procesar PDFs de laboratorio
==============================================================
Versión mejorada y más robusta (basada en tu código original + mejoras importantes)
"""

import os
import re
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
from pypdf import PdfReader

app = Flask(__name__)
CORS(app)

# =====================================================
# FUNCIONES AUXILIARES
# =====================================================
def parsear_valor(raw):
    limpio = raw.replace(",", ".").replace(" ", "")
    prefijo = ""
    if limpio.startswith("<"):
        prefijo = "<"
        limpio = limpio[1:]
    elif limpio.startswith(">"):
        prefijo = ">"
        limpio = limpio[1:]
    try:
        return float(limpio), prefijo
    except:
        return None, prefijo


# =====================================================
# 1. EXTRACCIÓN DE TEXTO
# =====================================================
def extraer_texto(ruta):
    paginas = []
    try:
        with pdfplumber.open(ruta) as pdf:
            for i, p in enumerate(pdf.pages, 1):
                texto = (p.extract_text() or "").strip()
                paginas.append({"pagina": i, "texto": texto})
    except Exception as e_plumber:
        try:
            reader = PdfReader(ruta)
            for i, p in enumerate(reader.pages, 1):
                texto = (p.extract_text() or "").strip()
                paginas.append({"pagina": i, "texto": texto})
        except Exception as e_pypdf:
            raise RuntimeError(f"No se pudo leer el PDF: {str(e_pypdf)}")
    return paginas


# =====================================================
# 2. EXTRACCIÓN DE PACIENTE (mejorada)
# =====================================================
PATRONES_PACIENTE = {
    "folio": [
        r"Folio[:\s#]+([A-Z0-9\-]+)",
        r"N[úu]mero de orden[:\s]+([A-Z0-9\-]+)",
        r"Orden[:\s#]+([A-Z0-9\-]+)",
        r"No\.\s*([A-Z0-9\-]{5,})",
    ],
    "paciente_id": [
        r"Paciente[:\s]+(\d+)\s*[-–]",
        r"ID[:\s]+(\d+)",
        r"Expediente[:\s]+(\d+)",
        r"C[óo]digo paciente[:\s]+(\d+)",
    ],
    "paciente_nombre": [
        r"Paciente[:\s]+\d+\s*[-–]\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ ]{3,60})(?:\n|Fecha|Sexo|M[eé]dico|$)",
        r"Paciente[:\s]+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ ,\.]+?)(?:\n|Fecha|Sexo|M[eé]dico)",
        r"Nombre[:\s]+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ ,\.]+?)(?:\n|Fecha|Sexo)",
    ],
    "sexo": [
        r"Sexo[:\s]+(Masculino|Femenino|M\b|F\b|Male|Female)",
        r"\b(Masculino|Femenino)\b",
    ],
    "edad": [
        r"Edad[:\s]+(\d+)\s*a[ñn]os?",
        r"(\d{1,3})\s*a[ñn]os?\s+(?:de edad)?",
        r"Age[:\s]+(\d+)",
    ],
    "fecha_nacimiento": [
        r"(?:Fecha de nacimiento|DOB|F\.Nac)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        r"(?:nacido|born)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        r"\b(\d{2}/\d{2}/\d{4})\b(?=.*(?:1[89]\d{2}|20[0-2]\d))",
    ],
    "medico": [
        r"M[eé]dico[:\s]+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ\. ]+?)(?:\n|Fecha|C[oó]digo|$)",
        r"Dr\.?\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+ [A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
        r"Physician[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)",
        r"Ordering Physician[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)",
    ],
    "laboratorio": [
        r"((?:LABORATORIO|LAB\.?|LABORATORIOS)[\w\s]+?)(?:\n|Folio|Paciente)",
        r"([\w\s]+ LABORATORIO[\w\s]*)",
        r"^([A-ZÁÉÍÓÚÑ][\w\s,\.]+(?:LAB|CLINIC|DIAGN)[\w\s,\.]+)$",
    ],
    "fecha_muestra": [
        r"Fecha de toma de muestra[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        r"Fecha de recepci[oó]n[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        r"COLLECTED[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        r"Fecha(?:\s+de\s+\w+)?[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})",
    ],
    "fecha_reporte": [
        r"Fecha de reporte[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        r"REPORTED[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
        r"Fecha de emisi[oó]n[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
    ],
}

def extraer_paciente(texto):
    datos = {}
    for campo, patrones in PATRONES_PACIENTE.items():
        for patron in patrones:
            m = re.search(patron, texto, re.IGNORECASE | re.MULTILINE)
            if m:
                valor = m.group(1).strip()
                if campo == "paciente_nombre":
                    valor = re.sub(r'\s+', ' ', valor).title()
                datos[campo] = valor
                break
        if campo not in datos:
            datos[campo] = ""
    return datos


# =====================================================
# 3. DETECTOR DE ANALITOS (ROBUSTO + MEJORADO)
# =====================================================

UNIDADES = (
    r"mg/dL|mg/L|g/dL|g/L|mEq/L|mmol/L|µmol/L|umol/L|nmol/L|pmol/L|"
    r"UI/L|U/L|IU/L|mUI/L|mIU/L|IU/mL|ng/mL|ng/dL|pg/mL|µg/dL|ug/dL|"
    r"µg/mL|ug/mL|%|fl|fL|pg|10\^3/µL|10\^6/µL|10\^3/uL|10\^6/uL|"
    r"K/µL|M/µL|K/uL|M/uL|cells/µL|cells/uL|copies/mL|titre|titer|"
    r"mOsm/kg|mm/h|mm/hr|seg|s\b|min\b|bpm|mmHg"
)

PALABRAS_RUIDO = {
    "folio","paciente","sexo","edad","médico","medico","fecha","código","codigo",
    "método","metodo","referencias","referencia","importante","nuestros","estudios",
    "cuentan","autenticador","responsable","sanitario","certificó","certfico",
    "laboratorio","sauces","matríz","matriz","trujano","llano","calle","oaxaca",
    "biomedicina","molecular","especialidad","hematología","hematologia",
    "microbiología","microbiologia","instituto","anton","leeuwenhoek","página","pagina",
    "pag","note","result","range","status","method","reference","normal","alto",
    "bajo","limite","deseable","optimo","óptimo","riesgo","medio","sin",
    "moderado","nivel","niveles","menos","mayor","menor","igual","adelante",
    "ingesta","técnica","tecnica","muestra","reporte","emision","emisión",
    "quest","diagnostics","specimen","requisition","collected","received",
    "reported","performing","laboratory","director","page","end","report",
    "final","information","client","ordering","physician","dob","age","sex",
    "de","del","al","la","el","los","las","para","que","con","por","un","una",
    "optimal","moderate","high","low","desirable","target","category","risk",
    "based","patients","treatment","depending","above","below","within","outside",
}

PREFIJOS_RUIDO = (
    "menos de ", "mayor de ", "menor de ", "de 1 ", "de 3 ", "de 0 ",
    "a desirable", "risk cat", "reference", "método:", "metodo:",
    "niveles de ", "nivel de ", "nota:", "note:", "importante:",
    "150 -", "200 -", "500 ", "130 ", "110 ", "100 -",
    "riesgo ", "moderate ", "optimal ", "high >", "low <",
)

RE_ANALITO_LINEA = re.compile(
    r"^"
    r"(?P<nombre>[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ0-9 \(\)\-/\.]{2,45}?)\s+"
    r"(?P<valor>[<>]?[\d]+(?:[.,]\d+)?)\s*"
    r"(?P<unidad>" + UNIDADES + r")"
    r"(?:\s+(?P<ref_low>[\d.,]+)\s*[-–]\s*(?P<ref_high>[\d.,]+))?"
    r"(?:\s+(?P<ref_texto>[^\n]{0,60}))?",
    re.IGNORECASE
)

RE_ANALITO_SIN_UNIDAD = re.compile(
    r"^(?P<nombre>[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ0-9 \(\)\-/\.]{2,45}?)\s+"
    r"(?P<valor>[<>]?\d+(?:[.,]\d+)?)\s*"
    r"(?P<ref_low>\d+(?:[.,]\d+)?)\s*[-–]\s*(?P<ref_high>\d+(?:[.,]\d+)?)",
    re.IGNORECASE
)

def limpiar_nombre(nombre):
    nombre = nombre.strip()
    nombre = re.sub(r'\s+', ' ', nombre)
    return nombre

def nombre_es_ruido(nombre):
    nombre_lower = nombre.lower().strip()
    if len(nombre_lower) < 3:
        return True
    for pref in PREFIJOS_RUIDO:
        if nombre_lower.startswith(pref.lower()):
            return True
    if re.match(r"^[\d<>]", nombre.strip()):
        return True
    palabras = nombre_lower.split()
    if not palabras:
        return True
    ruido = sum(1 for p in palabras if p in PALABRAS_RUIDO)
    return ruido >= len(palabras)

def calcular_estatus(valor_num, ref_low, ref_high, prefijo):
    if valor_num is None:
        return "sin_valor"
    try:
        if ref_low is not None and ref_high is not None:
            lo = float(str(ref_low).replace(",","."))
            hi = float(str(ref_high).replace(",","."))
            if valor_num < lo:   return "bajo"
            if valor_num > hi:   return "alto"
            return "normal"
    except (ValueError, TypeError):
        pass
    return "sin_referencia"

def extraer_analitos(texto):
    resultados = []
    nombres_vistos = set()
    lineas = texto.split("\n")

    for linea in lineas:
        linea = linea.strip()
        if len(linea) < 5:
            continue

        analito = None

        linea_lower = linea.lower()
        _ini = linea.lower()[:30]
        es_rango_referencia = (
            bool(re.match(r"^[0-9]", _ini)) or
            bool(re.match(r"^(menor |mayor |menos |de [0-9]|less |greater )", _ini)) or
            bool(re.match(r"^(hombres|mujeres|male|female)", _ini)) or
            bool(re.match(r"^(sin riesgo|moderado |moderate |optimal |high >|low <)", _ini))
        )
        if es_rango_referencia:
            continue

        m = RE_ANALITO_LINEA.match(linea)
        if m:
            nombre = limpiar_nombre(m.group("nombre"))
            if not nombre_es_ruido(nombre):
                valor_num, prefijo = parsear_valor(m.group("valor"))
                ref_low  = m.group("ref_low")
                ref_high = m.group("ref_high")
                analito = {
                    "analito":        nombre,
                    "valor_raw":      m.group("valor").strip(),
                    "valor_numerico": valor_num,
                    "prefijo":        prefijo,
                    "unidad":         m.group("unidad").strip(),
                    "ref_low":        float(ref_low.replace(",",".")) if ref_low else None,
                    "ref_high":       float(ref_high.replace(",",".")) if ref_high else None,
                    "estatus":        calcular_estatus(valor_num, ref_low, ref_high, prefijo),
                    "metodo_deteccion": "regex_original"
                }

        if analito is None:
            m2 = RE_ANALITO_SIN_UNIDAD.match(linea)
            if m2:
                nombre = limpiar_nombre(m2.group("nombre"))
                if not nombre_es_ruido(nombre):
                    valor_num, prefijo = parsear_valor(m2.group("valor"))
                    ref_low  = m2.group("ref_low")
                    ref_high = m2.group("ref_high")
                    analito = {
                        "analito":        nombre,
                        "valor_raw":      m2.group("valor").strip(),
                        "valor_numerico": valor_num,
                        "prefijo":        prefijo,
                        "unidad":         "",
                        "ref_low":        float(ref_low.replace(",",".")) if ref_low else None,
                        "ref_high":       float(ref_high.replace(",",".")) if ref_high else None,
                        "estatus":        calcular_estatus(valor_num, ref_low, ref_high, prefijo),
                        "metodo_deteccion": "regex_original"
                    }

        if analito:
            nombre_lower = analito["analito"].lower()
            if nombre_lower in SINONIMOS_ANALITO:
                analito["analito"] = SINONIMOS_ANALITO[nombre_lower]
            clave = analito["analito"].lower()
            if clave not in nombres_vistos:
                nombres_vistos.add(clave)
                resultados.append(analito)

    return resultados


SINONIMOS_ANALITO = {
    "apolipoprotein b":    "APOLIPOPROTEINA B",
    "lipoprotein (a)":     "LIPOPROTEINA A",
    "lipoprotein a":       "LIPOPROTEINA A",
    "glucose":             "GLUCOSA",
    "cholesterol":         "COLESTEROL",
    "triglycerides":       "TRIGLICERIDOS",
    "hemoglobin":          "HEMOGLOBINA",
    "hematocrit":          "HEMATOCRITO",
    "platelets":           "PLAQUETAS",
    "creatinine":          "CREATININA",
    "uric acid":           "ACIDO URICO",
    "albumin":             "ALBUMINA",
    "tsh":                 "TSH",
    "t4 libre":            "T4 LIBRE",
    "t3 libre":            "T3 LIBRE",
}

def extraer_analitos_fallback(texto, ya_encontrados):
    import unicodedata
    def _norm(s):
        s = unicodedata.normalize("NFD", s.lower())
        return "".join(c for c in s if unicodedata.category(c) != "Mn")
    ya = {_norm(a["analito"]) for a in ya_encontrados}
    extras = []

    patrones_especiales = [
        ("INDICE ATEROGENICO",    r"[ÍI]NDICE ATEROG[EÉ]NICO\s+([0-9]+(?:[.,][0-9]+)?)", ""),
        ("LIPOPROTEINA A",        r"Lipoprotein\s*\(a\)\s+(<?\d+)\s*(?:<\d+\s*)?nmol/L", "nmol/L"),
        ("GLUCOSA",               r"GLUCOSA\s+([0-9]+(?:[.,][0-9]+)?)\s*mg/dL", "mg/dL"),
        ("HEMOGLOBINA",           r"HEMOGLOBINA\s+([0-9]+(?:[.,][0-9]+)?)\s*g/dL", "g/dL"),
        ("HEMATOCRITO",           r"HEMATOCRITO\s+([0-9]+(?:[.,][0-9]+)?)\s*%", "%"),
        ("CREATININA",            r"CREATININA\s+([0-9]+(?:[.,][0-9]+)?)\s*mg/dL", "mg/dL"),
        ("UREA",                  r"UREA\s+([0-9]+(?:[.,][0-9]+)?)\s*mg/dL", "mg/dL"),
        ("ACIDO URICO",           r"[ÁA]CIDO [ÚU]RICO\s+([0-9]+(?:[.,][0-9]+)?)\s*mg/dL", "mg/dL"),
        ("APOLIPOPROTEINA B",     r"Apolipoprotein B\s+([0-9]+(?:[.,][0-9]+)?)\s*mg/dL", "mg/dL"),
        ("TSH",                   r"TSH\s+([0-9]+(?:[.,][0-9]+)?)\s*(?:mUI/L|µUI/mL|uIU/mL)", "mUI/L"),
        ("T4 LIBRE",              r"T4\s+(?:LIBRE|libre|Free)\s+([0-9]+(?:[.,][0-9]+)?)\s*(?:ng/dL|pmol/L)", "ng/dL"),
        ("T3 LIBRE",              r"T3\s+(?:LIBRE|libre|Free)\s+([0-9]+(?:[.,][0-9]+)?)\s*(?:ng/dL|pmol/L)", "ng/dL"),
        ("VITAMINA D",            r"VITAMINA\s*D\s+([0-9]+(?:[.,][0-9]+)?)\s*(?:ng/mL|nmol/L)", "ng/mL"),
        ("INSULINA",              r"INSULINA\s+([0-9]+(?:[.,][0-9]+)?)\s*(?:µUI/mL|uIU/mL)", "µUI/mL"),
    ]

    for nombre, patron, unidad in patrones_especiales:
        if _norm(nombre) in ya:
            continue
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            valor_num, prefijo = parsear_valor(raw)
            extras.append({
                "analito":        nombre,
                "valor_raw":      raw,
                "valor_numerico": valor_num,
                "prefijo":        prefijo,
                "unidad":         unidad,
                "ref_low":        None,
                "ref_high":       None,
                "estatus":        "sin_referencia",
                "metodo_deteccion": "fallback"
            })
    return extras


# =====================================================
# 4. DETECTAR SECCIONES
# =====================================================
SECCIONES_CONOCIDAS = [
    "QUIMICA CLINICA", "QUÍMICA CLÍNICA", "PERFIL DE LIPIDOS", "PERFIL LIPÍDICO",
    "BIOMETRIA HEMATICA", "BIOMETRÍA HEMÁTICA", "CITOMETRIA HEMATICA",
    "HEMOGRAMA", "HEMATOLOGIA", "HEMATOLOGÍA",
    "FUNCION RENAL", "FUNCIÓN RENAL", "PERFIL RENAL",
    "FUNCION HEPATICA", "FUNCIÓN HEPÁTICA", "PERFIL HEPATICO",
    "GLUCOSA", "PERFIL TIROIDEO", "ELECTROLITOS", "ORINA", "URINALISIS",
    "COAGULACION", "COAGULACIÓN", "PRUEBAS ESPECIALES", "SUBROGADOS",
    "INMUNOLOGIA", "INMUNOLOGÍA", "SEROLOGIA", "SEROLOGÍA",
    "HORMONAS", "CULTIVO", "MICROBIOLOGIA", "MICROBIOLOGÍA",
]

def detectar_secciones(texto):
    encontradas = []
    for s in SECCIONES_CONOCIDAS:
        if s.upper() in texto.upper():
            encontradas.append(s)
    return list(dict.fromkeys(encontradas))


# =====================================================
# 5. ENSAMBLAR REGISTRO FINAL
# =====================================================
def ensamblar(nombre_archivo, paginas, paciente, analitos, secciones):
    normales = sum(1 for a in analitos if a["estatus"] == "normal")
    altos    = sum(1 for a in analitos if a["estatus"] == "alto")
    bajos    = sum(1 for a in analitos if a["estatus"] == "bajo")
    sin_ref  = sum(1 for a in analitos if a["estatus"] == "sin_referencia")

    return {
        "documento": {
            "archivo":          nombre_archivo,
            "total_paginas":    len(paginas),
            "fecha_extraccion": datetime.now().isoformat(),
        },
        "paciente":     paciente,
        "tipo_estudio": secciones,
        "resultados":   analitos,
        "resumen": {
            "total_analitos": len(analitos),
            "normales":       normales,
            "altos":          altos,
            "bajos":          bajos,
            "sin_referencia": sin_ref,
            "alertas":        [a["analito"] for a in analitos if a["estatus"] in ("alto", "bajo")],
        }
    }


# =====================================================
# 6. ENDPOINTS FLASK
# =====================================================
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "mensaje": "API de laboratorio v3.0 activa"}), 200


@app.route("/procesar-pdf", methods=["POST"])
def procesar_pdf():
    if "pdf" not in request.files:
        return jsonify({"error": "No se recibió ningún archivo PDF"}), 400

    archivo = request.files["pdf"]
    if not archivo.filename.lower().endswith(".pdf"):
        return jsonify({"error": "El archivo debe ser un PDF"}), 400

    ruta_tmp = f"/tmp/{archivo.filename}"
    archivo.save(ruta_tmp)

    try:
        paginas = extraer_texto(ruta_tmp)
        texto   = "\n".join(p["texto"] for p in paginas)

        if not texto.strip():
            return jsonify({
                "error": "El PDF no contiene texto extraíble. "
                         "Debe ser un PDF digital, no escaneado."
            }), 422

        paciente  = extraer_paciente(texto)
        secciones = detectar_secciones(texto)
        analitos  = extraer_analitos(texto)
        extras    = extraer_analitos_fallback(texto, analitos)
        analitos  = analitos + extras

        registro = ensamblar(archivo.filename, paginas, paciente, analitos, secciones)
        return jsonify(registro), 200

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(ruta_tmp):
            os.remove(ruta_tmp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🔬 Servidor v3.0 iniciando en puerto {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)