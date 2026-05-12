"""
servidor.py v2.0 — API Flask para procesar PDFs de laboratorio
==============================================================
Versión mejorada: más inteligente, mejor detección de rangos,
más robusta y con estructura limpia.
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
            for i, page in enumerate(pdf.pages, 1):
                texto = page.extract_text() or ""
                tablas = page.extract_tables() or []
                paginas.append({"pagina": i, "texto": texto.strip(), "tablas": tablas})
    except Exception:
        try:
            reader = PdfReader(ruta)
            for i, page in enumerate(reader.pages, 1):
                texto = page.extract_text() or ""
                paginas.append({"pagina": i, "texto": texto.strip(), "tablas": []})
        except Exception as e:
            raise RuntimeError(f"No se pudo leer el PDF: {str(e)}")
    return paginas


# =====================================================
# 2. EXTRACCIÓN DE DATOS DEL PACIENTE (MEJORADA)
# =====================================================
PATRONES_PACIENTE = {
    "folio": [
        r"Folio[:\s#]+([A-Z0-9\-]+)",
        r"N[úu]mero de orden[:\s]+([A-Z0-9\-]+)",
        r"Orden[:\s#]+([A-Z0-9\-]+)",
    ],
    "paciente_id": [
        r"Paciente[:\s]+(\d+)\s*[-–]",
        r"ID[:\s]+(\d+)",
        r"Expediente[:\s]+(\d+)",
    ],
    "paciente_nombre": [
        r"Paciente[:\s]+\d+\s*[-–]\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ ]{3,60})",
        r"Nombre[:\s]+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ ,\.]{5,60})",
    ],
    "sexo": [r"Sexo[:\s]+(Masculino|Femenino)"],
    "edad": [r"Edad[:\s]+(\d{1,3})\s*a[ñn]os?"],
    "fecha_nacimiento": [r"(?:Fecha de nacimiento|DOB|F\.Nac)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})"],
    "medico": [r"M[eé]dico[:\s]+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ\. ]+?)(?:\n|$)"],
    "laboratorio": [r"((?:LABORATORIO|LAB\.?)[\w\s]+?)(?:\n|$)"],
    "fecha_muestra": [r"Fecha de (?:toma de )?muestra[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})"],
    "fecha_reporte": [r"Fecha de (?:reporte|emisi[oó]n)[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})"],
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
        datos.setdefault(campo, "")
    return datos


# =====================================================
# 3. DETECTOR DE ANALITOS MEJORADO (v2.0)
# =====================================================
def extraer_analitos_v2(texto_completo, paginas):
    resultados = []
    nombres_vistos = set()

    # Primero intentamos con tablas (más preciso)
    for pagina in paginas:
        for tabla in pagina.get("tablas", []):
            for fila in tabla:
                if len(fila) >= 3:
                    nombre = str(fila[0] or "").strip()
                    valor = str(fila[1] or "").strip()
                    if nombre and re.match(r'^[A-ZÁÉÍÓÚÑ]', nombre) and re.match(r'[\d<>]', valor):
                        analito = procesar_fila(nombre, valor, fila)
                        if analito and analito["analito"] not in nombres_vistos:
                            nombres_vistos.add(analito["analito"])
                            resultados.append(analito)

    # Luego método línea por línea mejorado
    for linea in texto_completo.split("\n"):
        linea = linea.strip()
        if len(linea) < 6:
            continue
        analito = detectar_analito_linea(linea)
        if analito and analito["analito"] not in nombres_vistos:
            nombres_vistos.add(analito["analito"])
            resultados.append(analito)

    return resultados


def detectar_analito_linea(linea):
    patron = r"^([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ0-9 \(\)\-/\.]{2,50}?)\s+([<>]?\d+(?:[.,]\d+)?)\s*([a-zA-Z/%µ]+)?"
    m = re.match(patron, linea)
    if not m:
        return None

    nombre = m.group(1).strip()
    if len(nombre) < 4 or any(x in nombre.lower() for x in ["nivel", "rango", "referencia", "nota"]):
        return None

    valor_raw = m.group(2).strip()
    unidad = m.group(3) or ""
    valor_num, prefijo = parsear_valor(valor_raw)

    return {
        "analito": nombre.upper(),
        "valor_raw": valor_raw,
        "valor_numerico": valor_num,
        "prefijo": prefijo,
        "unidad": unidad,
        "ref_low": None,
        "ref_high": None,
        "estatus": "sin_referencia",
        "metodo_deteccion": "regex_mejorado"
    }


def procesar_fila(nombre, valor, fila):
    valor_num, prefijo = parsear_valor(valor)
    return {
        "analito": nombre.upper(),
        "valor_raw": valor,
        "valor_numerico": valor_num,
        "prefijo": prefijo,
        "unidad": fila[2] if len(fila) > 2 else "",
        "ref_low": None,
        "ref_high": None,
        "estatus": "sin_referencia",
        "metodo_deteccion": "tabla"
    }


# =====================================================
# 4. ENSAMBLAR RESULTADO
# =====================================================
def ensamblar(nombre_archivo, paginas, paciente, analitos):
    return {
        "documento": {
            "archivo": nombre_archivo,
            "total_paginas": len(paginas),
            "fecha_extraccion": datetime.now().isoformat(),
        },
        "paciente": paciente,
        "tipo_estudio": [],
        "resultados": analitos,
        "resumen": {
            "total_analitos": len(analitos),
            "normales": 0,
            "altos": 0,
            "bajos": 0,
            "sin_referencia": len([a for a in analitos if a.get("estatus") == "sin_referencia"]),
            "alertas": []
        }
    }


# =====================================================
# 5. ENDPOINTS
# =====================================================
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "mensaje": "API de laboratorio v2.0 activa"}), 200


@app.route("/procesar-pdf", methods=["POST"])
def procesar_pdf():
    if "pdf" not in request.files:
        return jsonify({"error": "No se recibió PDF"}), 400

    archivo = request.files["pdf"]
    ruta_tmp = f"/tmp/{archivo.filename}"
    archivo.save(ruta_tmp)

    try:
        paginas = extraer_texto(ruta_tmp)
        texto_completo = "\n".join(p["texto"] for p in paginas)

        paciente = extraer_paciente(texto_completo)
        analitos = extraer_analitos_v2(texto_completo, paginas)

        registro = ensamblar(archivo.filename, paginas, paciente, analitos)
        return jsonify(registro), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(ruta_tmp):
            os.remove(ruta_tmp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🔬 Servidor v2.0 iniciando en puerto {port}...")
    app.run(host="0.0.0.0", port=port)