"""PDF de la factura mensual (documento simulado, proyecto educativo).

Se arma con los datos guardados de la factura (las líneas son una foto de cada clase), así
que el PDF no cambia aunque después cambien los precios de los tipos de clase. Usa las
fuentes estándar de PDF (Helvetica): solo admiten Latin-1, por eso _txt reemplaza los
pocos caracteres que no entran.
"""
from datetime import datetime

from fpdf import FPDF

VERDE = (110, 231, 183)      # --accent de la app
TINTA = (15, 17, 21)         # --bg de la app
GRIS = (110, 116, 124)
GRIS_CLARO = (243, 244, 246)
ROJO = (220, 38, 38)
_REEMPLAZOS = {"−": "-", "—": "-", "–": "-", "→": "->", "’": "'", "“": '"', "”": '"'}


def _txt(texto) -> str:
    texto = str(texto)
    for a, b in _REEMPLAZOS.items():
        texto = texto.replace(a, b)
    return texto.encode("latin-1", "replace").decode("latin-1")


def _plata(valor) -> str:
    entero, dec = f"{abs(valor):.2f}".split(".")
    con_puntos = f"{int(entero):,}".replace(",", ".")
    return f"{'-' if valor < 0 else ''}$ {con_puntos},{dec}"


class _Pdf(FPDF):
    def __init__(self, pie: str):
        super().__init__(format="A4")
        self.pie = pie
        self.set_auto_page_break(auto=True, margin=22)
        self.set_margins(16, 16, 16)

    def footer(self):
        self.set_y(-16)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*GRIS)
        self.cell(0, 4, _txt(self.pie), align="L")
        self.cell(0, 4, f"Página {self.page_no()}/{{nb}}", align="R")


def generar(f: dict, ahora: datetime, marca: str | None = None) -> bytes:
    """f = factura de app.facturas (factura() o mis_facturas()). marca = 'BORRADOR' o 'ANULADA'.
    Nombre y cédula salen de la factura: su foto fija si está confirmada."""
    pdf = _Pdf(f"Documento simulado con fines educativos · Just Show Up · generado el "
               f"{ahora.day:02d}/{ahora.month:02d}/{ahora.year} {ahora.hour:02d}:{ahora.minute:02d}")
    pdf.alias_nb_pages()
    pdf.set_title(_txt(f"Factura mensual {f.get('numero') or ''} - Just Show Up"))
    pdf.set_author("Just Show Up")
    pdf.add_page()
    ancho = pdf.w - pdf.l_margin - pdf.r_margin

    # banda de la marca
    pdf.set_fill_color(*VERDE)
    pdf.rect(0, 0, pdf.w, 34, style="F")
    pdf.set_xy(16, 9)
    pdf.set_text_color(*TINTA)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(110, 10, "Just Show Up")
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, _txt("Factura mensual"), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(110, 5, _txt("Clases presenciales"))
    pdf.cell(0, 5, _txt(f"N.º {f.get('numero') or 'sin número (borrador)'}"), align="R", new_x="LMARGIN", new_y="NEXT")

    if marca:
        pdf.set_xy(16, 40)
        pdf.set_text_color(*ROJO)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, _txt(marca), align="C", new_x="LMARGIN", new_y="NEXT")

    # datos generales
    if f.get("estado_factura") == "anulada":
        estado = "Anulada"
    elif f.get("estado_pago") == "pagado" and f.get("pago"):
        estado = f"Pagada el {f['pago']['fecha']} ({f['pago']['medio']})"
    elif f.get("estado_pago") == "sin_cargo":
        estado = "Sin cargo"
    elif f.get("vencida"):
        estado = "Vencida — deuda pendiente"
    else:
        estado = "Pendiente de pago"
    pdf.set_y(52 if marca else 44)
    datos = [("Alumna", f.get("alumna", "")), ("Cédula", f.get("cedula_txt") or "no registrada"),
             ("Período", f.get("mes_txt", "")), ("Confirmada el", f.get("confirmada_txt") or "-"),
             ("Clases", str(f.get("cantidad_clases", 0))),
             ("Vencimiento", f.get("vence_txt") or "-"),
             (f"Estado de pago al {ahora.day:02d}/{ahora.month:02d}/{ahora.year}", estado)]
    pdf.set_font("Helvetica", "", 9)
    for i, (etiqueta, valor) in enumerate(datos):
        x = 16 + (i % 2) * ancho / 2
        if i % 2 == 0 and i:
            pdf.ln(11)
        pdf.set_xy(x, pdf.get_y())
        pdf.set_text_color(*GRIS)
        pdf.cell(ancho / 2, 4.5, _txt(etiqueta.upper()))
        pdf.set_xy(x, pdf.get_y() + 4.5)
        pdf.set_text_color(*TINTA)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(ancho / 2, 6, _txt(valor))
        pdf.set_font("Helvetica", "", 9)
        pdf.set_y(pdf.get_y() - 4.5)
    pdf.ln(18)

    # tabla de clases
    cols = [("Fecha", 34, "L"), ("Hora", 16, "L"), ("Clase", 42, "L"), ("Precio original", 30, "R"),
            ("Ajuste", 28, "R"), ("Importe", ancho - 150, "R")]
    pdf.set_fill_color(*TINTA)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8.5)
    for titulo, w, al in cols:
        pdf.cell(w, 8, _txt(titulo), fill=True, align=al)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for n, l in enumerate(f.get("lineas", [])):
        if pdf.will_page_break(6.4):
            pdf.add_page()
        pdf.set_fill_color(*(GRIS_CLARO if n % 2 else (255, 255, 255)))
        pdf.set_text_color(*TINTA)
        if l["ajuste_tipo"] == "bonificada":
            ajuste = "Bonificada"
        elif l["ajuste_tipo"] == "importe":
            ajuste = _plata(l["ajuste"])
        else:
            ajuste = "-"
        valores = [l.get("fecha_corta") or l["fecha_txt"], l["hora"], l["tipo"], _plata(l["precio_original"]),
                   ajuste, _plata(l["importe_final"])]
        for (_, w, al), v in zip(cols, valores):
            pdf.cell(w, 6.4, _txt(v), fill=True, align=al)
        pdf.ln()

    # totales
    pdf.ln(4)
    x = 16 + ancho - 86
    filas = [("Subtotal (precios originales)", _plata(f["subtotal_original"]))]
    ajustes = f["total_lineas"] - f["subtotal_original"]
    if ajustes:
        filas.append(("Ajustes por clase", _plata(ajustes)))
    if f.get("descuento_tipo"):
        filas.append((f"Descuento general ({f['descuento_txt']})", _plata(-f["descuento_monto"])))
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*TINTA)
    for etiqueta, valor in filas:
        pdf.set_x(x)
        pdf.cell(56, 6.5, _txt(etiqueta))
        pdf.cell(30, 6.5, _txt(valor), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(x)
    pdf.set_fill_color(*VERDE)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(56, 10, "  TOTAL", fill=True)
    pdf.cell(30, 10, _txt(_plata(f["total"])) + "  ", fill=True, align="R", new_x="LMARGIN", new_y="NEXT")

    if f.get("reemplaza") or f.get("estado_factura") == "anulada":
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*(ROJO if f.get("estado_factura") == "anulada" else GRIS))
        if f.get("estado_factura") == "anulada":
            pdf.multi_cell(0, 5, _txt("Esta factura fue anulada" + (f" y reemplazada por la factura {f['reemplazada_por']}."
                                                                    if f.get("reemplazada_por") else ".")))
        if f.get("reemplaza"):
            pdf.set_text_color(*GRIS)
            pdf.multi_cell(0, 5, _txt(f"Esta factura reemplaza a la factura {f['reemplaza']}."))
    return bytes(pdf.output())


def nombre_archivo(f: dict) -> str:
    base = f.get("numero") or f"borrador-{f['id']}"
    return "factura-" + "".join(c for c in base if c.isalnum() or c in "-_") + ".pdf"
