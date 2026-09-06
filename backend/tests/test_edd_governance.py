"""Gobernanza EDD (SPEC-014 / T9.6 / AC6).

AC6 pide dos cosas: que la **disciplina** esté documentada —cuándo y cómo añadir un
eval, DoR/DoD de evaluación, alcance limitado a los modelos de la plataforma— y que
el área `area/evaluation` esté dada de alta en validador, seed, gobernanza y backlog.

Probar documentación puede sonar a formalismo, y no lo es aquí por el mismo motivo
que en `test_data_retention.py`: **una política que no coincide con lo que hace el
código es peor que no tener política**, porque genera confianza infundada. Estos
casos cruzan el documento con la realidad — que las rutas que nombra existan, que el
modo del gate que declara sea el que tiene el fichero de umbrales, y que el alta del
área siga en los cuatro sitios y no en tres.
"""
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = ROOT_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

GOBERNANZA = REPO_DIR / "docs" / "governance" / "GOVERNANCE.md"
DISCIPLINA = REPO_DIR / "docs" / "governance" / "edd-discipline.md"
CODEOWNERS = REPO_DIR / ".github" / "CODEOWNERS"
VALIDADOR = REPO_DIR / "scripts" / "validate_specs.py"
SEED = REPO_DIR / "scripts" / "seed_github_project.py"
BACKLOG = REPO_DIR / "docs" / "backlog" / "security-hardening-backlog.md"
UMBRALES = ROOT_DIR / "evals" / "agent_behavior" / "thresholds.yaml"

AREA = "area/evaluation"


@pytest.fixture(scope="module")
def disciplina():
    return DISCIPLINA.read_text(encoding="utf-8")


# ── El alta del área, en los cuatro sitios que nombra AC6 ───────────────────

@pytest.mark.parametrize("nombre,ruta", [
    ("validador", VALIDADOR),
    ("seed del Project", SEED),
    ("gobernanza", GOBERNANZA),
    ("backlog", BACKLOG),
])
def test_el_area_esta_dada_de_alta(nombre, ruta):
    """Los cuatro sitios de AC6. Dar de alta un área en tres de cuatro deja el
    validador o el seed rechazando specs perfectamente válidas."""
    assert AREA in ruta.read_text(encoding="utf-8"), f"{AREA} no aparece en el {nombre}"


def test_el_validador_acepta_specs_del_area():
    """No basta con que la cadena aparezca: tiene que estar en la lista que decide."""
    from importlib import util

    spec = util.spec_from_file_location("validate_specs", VALIDADOR)
    modulo = util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert AREA in modulo.ALLOWED_AREAS


# ── La disciplina documentada ───────────────────────────────────────────────

def test_existe_el_documento_de_disciplina():
    assert DISCIPLINA.is_file(), "AC6 pide la disciplina documentada, no solo el área"


def test_la_gobernanza_apunta_al_documento():
    """Un documento que no se enlaza desde GOVERNANCE no lo lee nadie."""
    texto = GOBERNANZA.read_text(encoding="utf-8")
    assert "edd-discipline.md" in texto


@pytest.mark.parametrize("seccion", [
    "cuándo",          # cuándo hay que tocar los evals
    "cómo se añade",   # cómo se añade un eval
    "DoR de evaluación",
    "DoD de evaluación",
])
def test_la_disciplina_cubre_lo_que_pide_el_criterio(disciplina, seccion):
    assert seccion.lower() in disciplina.lower(), f"falta la parte de «{seccion}»"


def test_la_disciplina_fija_el_alcance(disciplina):
    """«Alcance limitado a modelos de la plataforma» (AC6): la frontera con el
    benchmark de modelos es lo que impide que los dos conjuntos de números se
    mezclen y dejen de significar nada."""
    assert "model_benchmark" in disciplina
    assert "foundation" in disciplina


def test_la_dor_y_la_dod_generales_enganchan_la_de_evaluacion():
    """Si la DoR/DoD de evaluación vive solo en su documento, se aplica cuando
    alguien se acuerda. Enganchada a §5 y §6, se aplica al leer la lista."""
    texto = GOBERNANZA.read_text(encoding="utf-8")
    dor = texto.split("## 5.")[1].split("## 6.")[0]
    dod = texto.split("## 6.")[1].split("## 7.")[0]
    assert "edd-discipline.md" in dor, "la DoR general no engancha la de evaluación"
    assert "edd-discipline.md" in dod, "la DoD general no engancha la de evaluación"


def test_la_dod_exige_bajar_el_umbral_en_la_misma_pr():
    """Es la exigencia que evita el fallo silencioso del gate: aceptar una
    regresión y relajar el umbral después, en un commit que nadie relaciona."""
    texto = GOBERNANZA.read_text(encoding="utf-8")
    dod = texto.split("## 6.")[1].split("## 7.")[0]
    assert "misma PR" in dod


def test_la_disciplina_exige_conjunto_golden_para_un_agente_nuevo(disciplina):
    """Sin él, el gate no lo vigila y su comportamiento no lo defiende nada — y eso
    no se nota hasta que regresa."""
    assert re.search(r"agente nuevo.{0,120}golden", disciplina, re.IGNORECASE | re.DOTALL)


# ── Que el documento diga la verdad ─────────────────────────────────────────

def test_el_modo_que_declara_el_documento_es_el_que_tiene_el_gate(disciplina):
    """El caso que convierte esto en algo más que prosa: si alguien endurece el
    gate y no actualiza el documento, la gobernanza miente sobre lo que exige."""
    umbrales = yaml.safe_load(UMBRALES.read_text(encoding="utf-8"))
    enforce = bool(umbrales.get("enforce", False))
    dice_aviso = "modo aviso" in disciplina.lower()
    assert dice_aviso is (not enforce), (
        f"thresholds.yaml tiene enforce={enforce} pero el documento "
        f"{'dice' if dice_aviso else 'no dice'} que corre en modo aviso"
    )


@pytest.mark.parametrize("ruta", [
    "backend/evals/agent_behavior/thresholds.yaml",
    "backend/evals/agent_behavior/datasets",
    "backend/app/modules/agents/adapters",
    "backend/app/platform/llm.py",
    "backend/app/platform/engine",
    ".github/workflows/edd-gate.yml",
])
def test_cada_ruta_que_nombra_la_disciplina_existe(disciplina, ruta):
    """Documentar una ruta que no existe manda a quien la siga a un sitio que no
    está. Es el mismo defecto que tenían las rutas del AC de T9.5."""
    if ruta not in disciplina:
        pytest.skip(f"la disciplina no menciona {ruta}")
    assert (REPO_DIR / ruta).exists(), f"la disciplina nombra {ruta}, que no existe"


def test_los_evals_tienen_dueno():
    """Relajar un umbral relaja la garantía de comportamiento: debe revisarlo
    alguien, igual que un cambio de seguridad."""
    texto = CODEOWNERS.read_text(encoding="utf-8")
    lineas = [l for l in texto.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert any(l.startswith("/backend/evals/") for l in lineas), (
        "backend/evals/ no tiene dueño declarado"
    )
    assert any("edd-gate.yml" in l for l in lineas), (
        "el workflow del gate no tiene dueño: se podría desactivar sin revisión"
    )
