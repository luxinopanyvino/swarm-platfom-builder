"""SDD formalizado y **vivo** (SPEC-020 / T6.6 / AC6).

AC6 pide tres cosas —specs con DoR/DoD, CODEOWNERS activo y el pipeline de autoría
documentado en ADR-0007— y la sección 4 de la spec añade el matiz que da sentido a
esta tarea: *«ya materializado …; el AC exige que **se mantenga**»*. Los tres
documentos existían antes de esta suite; lo que no existía era nada que los atara a
la realidad.

Y la realidad ya se había ido por su lado. La regla de CODEOWNERS que cubría el
riesgo de SSRF apuntaba a `adapters/scraper.py`, un fichero borrado en 71e3923: una
regla muerta no protege nada y **GitHub no avisa**, así que el área siguió figurando
como cubierta mientras el código que lleva ese riesgo —el guardián de egress y el
catálogo de herramientas— cambiaba sin revisor de área asignado. Ese es exactamente
el fallo que persiguen estos casos, y el motivo de que probar documentación aquí no
sea formalismo: una política que ya no coincide con el repositorio es peor que no
tenerla, porque genera confianza infundada.
"""
import re
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parents[2]
GOBERNANZA = REPO_DIR / "docs" / "governance" / "GOVERNANCE.md"
CODEOWNERS = REPO_DIR / ".github" / "CODEOWNERS"
ADR_SPECKIT = REPO_DIR / "docs" / "adr" / "0007-adopt-spec-kit-authoring-layer.md"
TEMPLATE = REPO_DIR / "docs" / "specs" / "TEMPLATE.md"
SKILLS_DIR = REPO_DIR / ".claude" / "skills"
COMANDOS_DIR = REPO_DIR / ".claude" / "commands"

#: Las superficies que la propia gobernanza trata como sensibles. No es «toda ruta
#: importante» —eso sería una lista imposible de mantener— sino las que un cambio
#: sin revisor de área convierte en un agujero: la puerta de egress (SPEC-024) y el
#: catálogo de herramientas que la usa.
SUPERFICIES_SENSIBLES = (
    "backend/app/platform/egress.py",
    "backend/app/platform/capabilities/tools.py",
    "backend/app/core/security.py",
)


def _reglas_codeowners():
    """(patrón, [owners]) de cada regla, ignorando comentarios y líneas vacías."""
    reglas = []
    for numero, linea in enumerate(CODEOWNERS.read_text(encoding="utf-8").splitlines(), 1):
        limpia = linea.split("#", 1)[0].strip()
        if not limpia:
            continue
        partes = limpia.split()
        reglas.append((numero, partes[0], partes[1:]))
    return reglas


def _seccion(texto: str, titulo: str) -> str:
    """El cuerpo de una sección `## <titulo>…` hasta el siguiente `## `."""
    patron = rf"^## {re.escape(titulo)}.*?$(.*?)(?=^## |\Z)"
    m = re.search(patron, texto, re.MULTILINE | re.DOTALL)
    assert m, f"GOVERNANCE.md no tiene la sección «{titulo}»"
    return m.group(1)


# ── AC6, primera pata: las specs tienen DoR y DoD, y no vacíos ────────────────

@pytest.mark.parametrize("seccion", ["5. Definition of Ready (DoR)", "6. Definition of Done (DoD)"])
def test_la_gobernanza_declara_dor_y_dod_con_criterios(seccion):
    """Una sección con título y sin criterios cumple la letra y ninguna función."""
    cuerpo = _seccion(GOBERNANZA.read_text(encoding="utf-8"), seccion)
    criterios = re.findall(r"^- \[[ x]\] ", cuerpo, re.MULTILINE)
    assert len(criterios) >= 4, f"«{seccion}» solo declara {len(criterios)} criterios"


def test_la_plantilla_de_spec_existe_para_que_specify_tenga_de_donde_partir():
    assert TEMPLATE.is_file(), "ADR-0007 mapea /speckit-specify sobre TEMPLATE.md"


# ── AC6, segunda pata: CODEOWNERS activo (no solo presente) ───────────────────

def test_codeowners_tiene_reglas():
    assert _reglas_codeowners(), "un CODEOWNERS sin reglas no es «CODEOWNERS activo»"


def test_ninguna_regla_se_quedo_sin_owner():
    huerfanas = [(n, p) for n, p, owners in _reglas_codeowners() if not owners]
    assert not huerfanas, f"reglas sin revisor: {huerfanas}"


def test_no_quedan_placeholders_sin_sustituir():
    sospechosos = [
        (n, o)
        for n, _, owners in _reglas_codeowners()
        for o in owners
        if not re.fullmatch(r"@[A-Za-z0-9][A-Za-z0-9-]*(/[A-Za-z0-9._-]+)?", o)
    ]
    assert not sospechosos, f"owners que no son handles ni equipos de GitHub: {sospechosos}"


def test_ninguna_regla_apunta_a_algo_que_ya_no_existe():
    """El fallo real de T6.6: la regla del scraper sobrevivió al fichero.

    GitHub acepta una ruta inexistente sin rechistar y la deja ahí, verde, sin
    proteger nada. Si esto falla, la pregunta no es «¿borro la regla?» sino «¿a
    dónde se ha mudado el riesgo que cubría?».
    """
    muertas = []
    for numero, patron, _ in _reglas_codeowners():
        if patron == "*":
            continue
        relativo = patron.strip("/")
        if any(c in relativo for c in "*?["):
            if not list(REPO_DIR.glob(relativo)):
                muertas.append((numero, patron))
        elif not (REPO_DIR / relativo).exists():
            muertas.append((numero, patron))
    assert not muertas, (
        f"CODEOWNERS protege rutas inexistentes: {muertas}. Una regla muerta no "
        "protege nada y GitHub no avisa."
    )


@pytest.mark.parametrize("ruta", SUPERFICIES_SENSIBLES)
def test_las_superficies_sensibles_tienen_revisor_de_area(ruta):
    """La regla por defecto `*` cubre a todos, pero no expresa intención.

    Que el egress figure explícitamente es lo que hace que su revisión se pida a
    propósito el día que haya más de un revisor, en vez de por descarte.
    """
    patrones = {p.strip("/") for _, p, _ in _reglas_codeowners() if p != "*"}
    cubierta = any(ruta == p or ruta.startswith(p.rstrip("/") + "/") for p in patrones)
    assert cubierta, f"{ruta} no tiene regla propia en CODEOWNERS"


# ── AC6, tercera pata: el pipeline de autoría, documentado y en vigor ─────────

def test_el_adr_del_pipeline_existe():
    assert ADR_SPECKIT.is_file()


def test_el_adr_del_pipeline_no_se_declara_provisional():
    """La gobernanza lo invoca como norma (§5): no puede decir de sí mismo «Propuesto».

    No se exige aquí que *todos* los ADR estén aceptados —eso es decisión del
    maintainer (§2)—, solo que el que AC6 nombra no se contradiga con el uso que la
    propia gobernanza le da.
    """
    estado = re.search(r"^\s*-\s*\*\*Estado:\*\*\s*(\S+)", ADR_SPECKIT.read_text(encoding="utf-8"),
                       re.MULTILINE)
    assert estado, "ADR-0007 no declara Estado"
    assert estado.group(1).lower().startswith("aceptado"), (
        f"ADR-0007 está en «{estado.group(1)}» y GOVERNANCE §5 ya lo aplica como norma"
    )


def test_la_dor_enlaza_el_pipeline_de_autoria():
    dor = _seccion(GOBERNANZA.read_text(encoding="utf-8"), "5. Definition of Ready (DoR)")
    assert "0007-adopt-spec-kit-authoring-layer" in dor, (
        "AC6 pide el pipeline documentado *y* conectado a la DoR"
    )


@pytest.mark.parametrize("skill", ["speckit-specify", "speckit-clarify",
                                   "speckit-checklist", "speckit-analyze"])
def test_las_skills_que_el_adr_mapea_existen(skill):
    """La tabla del ADR es un mapeo, no una intención: si la skill no está, miente."""
    assert (SKILLS_DIR / skill).is_dir(), f"ADR-0007 mapea /{skill} y no existe"


@pytest.mark.parametrize("comando", ["resolve-task", "sdd-sync"])
def test_los_comandos_que_el_adr_mapea_existen(comando):
    assert (COMANDOS_DIR / f"{comando}.md").is_file(), f"ADR-0007 mapea /{comando}"
