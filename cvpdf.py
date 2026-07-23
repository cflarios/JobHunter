"""Renderiza un CV estructurado (dict de cv.build_cv) a un PDF limpio y ATS-friendly.

Usa fpdf2 con DejaVu Sans (Unicode, para acentos españoles). Layout de una sola
columna, compacto, pensado para caber en ~2 páginas. Devuelve los bytes del PDF.
"""
import os

from fpdf import FPDF, XPos, YPos

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_REG = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_BOLD = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")

ACCENT = (37, 99, 235)     # azul
INK = (23, 30, 46)         # texto principal
MUTED = (105, 116, 140)    # texto secundario
RULE = (210, 216, 228)     # líneas


def _s(v):
    """Normaliza a texto seguro (None → '')."""
    if v is None:
        return ""
    return str(v).strip()


def _clean_list(v):
    if isinstance(v, str):
        v = [p.strip() for p in v.split(",")]
    if not isinstance(v, list):
        return []
    return [_s(x) for x in v if _s(x)]


class _CV(FPDF):
    def __init__(self, scale=1.0):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.scale = scale
        self.set_auto_page_break(auto=True, margin=14)
        self.set_margins(16, 14, 16)
        self.add_font("DejaVu", "", _REG)
        self.add_font("DejaVu", "B", _BOLD)
        self.add_page()

    def z(self, v):
        """Escala tamaños de fuente / alturas para el ajuste a 2 páginas."""
        return v * self.scale

    # -- primitivas -------------------------------------------------------- #
    def _text(self, txt, size=9.5, bold=False, color=INK, gap=1.2, lh=4.6):
        self.set_font("DejaVu", "B" if bold else "", self.z(size))
        self.set_text_color(*color)
        self.multi_cell(0, self.z(lh), _s(txt), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if gap:
            self.ln(self.z(gap))

    def section(self, title):
        if self.get_y() > 8:
            self.ln(self.z(2.5))
        self.set_font("DejaVu", "B", self.z(10.5))
        self.set_text_color(*ACCENT)
        self.cell(0, self.z(5), title.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y = self.get_y() + 0.6
        self.set_draw_color(*RULE)
        self.set_line_width(0.3)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(self.z(2.4))

    def bullet(self, txt):
        self.set_font("DejaVu", "", self.z(9.3))
        self.set_text_color(*INK)
        x0 = self.get_x()
        self.cell(4, self.z(4.4), "•")
        self.set_x(x0 + 4)
        self.multi_cell(self.w - self.r_margin - (x0 + 4), self.z(4.4), _s(txt),
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _build(cv, scale=1.0):
    """Construye el PDF a una escala dada. Devuelve el objeto FPDF."""
    cv = cv or {}
    pdf = _CV(scale=scale)

    # -- Cabecera ---------------------------------------------------------- #
    name = _s(cv.get("name")) or "Currículum"
    pdf.set_font("DejaVu", "B", pdf.z(19))
    pdf.set_text_color(*INK)
    pdf.cell(0, pdf.z(9), name, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    headline = _s(cv.get("headline"))
    if headline:
        pdf.set_font("DejaVu", "B", pdf.z(10.5))
        pdf.set_text_color(*ACCENT)
        pdf.cell(0, pdf.z(5.5), headline, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    contact = cv.get("contact") or {}
    bits = [_s(contact.get("email")), _s(contact.get("phone")),
            _s(contact.get("location"))]
    bits += _clean_list(contact.get("links"))
    bits = [b for b in bits if b]
    if bits:
        pdf.ln(0.6)
        pdf.set_font("DejaVu", "", pdf.z(8.6))
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(0, pdf.z(4.2), "  ·  ".join(bits),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(pdf.z(1.5))

    # -- Resumen ----------------------------------------------------------- #
    if _s(cv.get("summary")):
        pdf.section("Resumen profesional")
        pdf._text(cv.get("summary"), size=9.4, color=INK, gap=0.5)

    # -- Experiencia ------------------------------------------------------- #
    exp = cv.get("experience") or []
    if isinstance(exp, list) and any(isinstance(e, dict) for e in exp):
        pdf.section("Experiencia")
        for e in exp:
            if not isinstance(e, dict):
                continue
            title = _s(e.get("title"))
            company = _s(e.get("company"))
            head = " — ".join([x for x in (title, company) if x]) or company or title
            pdf.set_font("DejaVu", "B", pdf.z(9.8))
            pdf.set_text_color(*INK)
            pdf.multi_cell(0, pdf.z(4.7), head, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            meta = "  ·  ".join([x for x in (_s(e.get("period")), _s(e.get("location"))) if x])
            if meta:
                pdf.set_font("DejaVu", "", pdf.z(8.4))
                pdf.set_text_color(*MUTED)
                pdf.multi_cell(0, pdf.z(4.0), meta, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(pdf.z(0.6))
            for b in _clean_list(e.get("bullets")):
                pdf.bullet(b)
            pdf.ln(pdf.z(1.8))

    # -- Habilidades ------------------------------------------------------- #
    skills = _clean_list(cv.get("skills"))
    if skills:
        pdf.section("Habilidades")
        pdf._text("  ·  ".join(skills), size=9.2, color=INK, gap=0.5)

    # -- Educación --------------------------------------------------------- #
    edu = cv.get("education") or []
    if isinstance(edu, list) and any(isinstance(x, dict) for x in edu):
        pdf.section("Educación")
        for x in edu:
            if not isinstance(x, dict):
                continue
            line = " — ".join([v for v in (_s(x.get("degree")), _s(x.get("institution"))) if v])
            pdf.set_font("DejaVu", "B", pdf.z(9.4))
            pdf.set_text_color(*INK)
            pdf.multi_cell(0, pdf.z(4.5), line or "—", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if _s(x.get("period")):
                pdf.set_font("DejaVu", "", pdf.z(8.4))
                pdf.set_text_color(*MUTED)
                pdf.multi_cell(0, pdf.z(4.0), _s(x.get("period")), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(pdf.z(1.2))

    # -- Certificaciones --------------------------------------------------- #
    certs = _clean_list(cv.get("certifications"))
    if certs:
        pdf.section("Certificaciones")
        for c in certs:
            pdf.bullet(c)
        pdf.ln(pdf.z(1.2))

    # -- Idiomas ----------------------------------------------------------- #
    langs = _clean_list(cv.get("languages"))
    if langs:
        pdf.section("Idiomas")
        pdf._text("  ·  ".join(langs), size=9.2, color=INK, gap=0)

    return pdf


def render(cv):
    """cv: dict con name/headline/contact/summary/skills/experience/education/
    certifications/languages. Devuelve bytes del PDF (máx. 2 páginas: si a escala
    normal se pasa, reintenta compactando)."""
    pdf = _build(cv, scale=1.0)
    if pdf.page_no() > 2:
        pdf = _build(cv, scale=0.86)   # segundo pase compacto para caber en 2 págs.
    return bytes(pdf.output())


if __name__ == "__main__":
    demo = {
        "name": "Cristian Fernández", "headline": "DevOps Engineer | SRE",
        "contact": {"email": "cristian@example.com", "location": "Colombia (remoto)",
                    "links": ["linkedin.com/in/cristian"]},
        "summary": "Ingeniero DevOps con 5 años automatizando infraestructura cloud.",
        "skills": ["Kubernetes", "Terraform", "AWS", "CI/CD", "Docker", "Python"],
        "experience": [{"title": "DevOps Engineer", "company": "ACME", "location": "Remoto",
                        "period": "2021–2026",
                        "bullets": ["Reduje el tiempo de despliegue un 60% con GitLab CI.",
                                    "Migré 30 servicios a Kubernetes."]}],
        "education": [{"degree": "Ing. de Sistemas", "institution": "Universidad X", "period": "2015–2019"}],
        "certifications": ["CKA — Certified Kubernetes Administrator"],
        "languages": ["Español (nativo)", "Inglés (profesional)"],
    }
    with open("/tmp/cv_demo.pdf", "wb") as f:
        f.write(render(demo))
    print("PDF demo escrito en /tmp/cv_demo.pdf")
