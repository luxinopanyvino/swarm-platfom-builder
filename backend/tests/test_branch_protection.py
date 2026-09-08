"""Protección de `develop` (SPEC-020 / T6.1 / AC1).

AC1 pide dos cosas: que la CI ejecute cuatro comprobaciones y que **una PR en rojo
no se pueda mergear**. La primera mitad es código y se prueba aquí. La segunda es
configuración del servidor de GitHub: ningún test que corra dentro del repositorio
puede saber si está aplicada, y fingir lo contrario sería peor que no probar nada.

Lo que sí se puede probar es que la regla, **escrita como artefacto**
(`.github/rulesets/develop.json`), siga describiendo el workflow que existe. El
fallo que persiguen estos casos es concreto y silencioso: GitHub identifica un
check obligatorio por el `name:` visible del job, así que renombrar un job deja el
check requerido apuntando a algo que ya no existe —la PR se queda esperando para
siempre— o, si se limpia el ruleset sin pensar, el gate deja de cubrir los tests y
todo sigue en verde. Al cruzar las dos fuentes, ese renombrado falla en la misma PR
que lo introduce.
"""
import json
from pathlib import Path

import pytest
import yaml

REPO_DIR = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_DIR / ".github" / "workflows" / "ci.yml"
EDD_WORKFLOW = REPO_DIR / ".github" / "workflows" / "edd-gate.yml"
RULESET = REPO_DIR / ".github" / "rulesets" / "develop.json"
DOC = REPO_DIR / "docs" / "governance" / "branch-protection.md"

RAMA = "develop"

#: Los cuatro elementos que AC1 nombra, con el fragmento del job que los delata.
#: Se comprueba lo que el job **hace**, no cómo se llama: un job renombrado sigue
#: valiendo, un job vaciado no.
ELEMENTOS_AC1 = {
    "pytest backend": "pytest",
    "build frontend": "npm run build",
    "validación de specs": "validate_specs.py",
    "escaneo de secretos": "gitleaks",
}


@pytest.fixture(scope="module")
def ci():
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ruleset():
    return json.loads(RULESET.read_text(encoding="utf-8"))


def _reglas(ruleset, tipo):
    return [r for r in ruleset["rules"] if r["type"] == tipo]


def _nombres_de_job(workflow):
    """El nombre visible de cada job: el `name:`, y si no lo tiene, su id.

    Es exactamente la cadena con la que GitHub casa un check obligatorio.
    """
    return {
        job_id: (cuerpo or {}).get("name", job_id)
        for job_id, cuerpo in workflow["jobs"].items()
    }


# ── Primera mitad de AC1: la CI corre en las PR a develop y hace las cuatro cosas ──

def test_la_ci_corre_en_las_pr_a_develop(ci):
    # `on` sin comillas lo lee YAML 1.1 como el booleano True; de ahí el fallback.
    disparadores = ci.get("on", ci.get(True))
    ramas = disparadores["pull_request"]["branches"]
    assert RAMA in ramas, f"la CI no se dispara en PR a {RAMA}: {ramas}"


@pytest.mark.parametrize("elemento,huella", sorted(ELEMENTOS_AC1.items()))
def test_la_ci_ejecuta_los_cuatro_elementos_de_ac1(elemento, huella):
    texto = CI_WORKFLOW.read_text(encoding="utf-8")
    assert huella in texto, f"AC1 exige {elemento} y no encuentro '{huella}' en ci.yml"


# ── Segunda mitad: la regla, escrita, describe ese mismo workflow ──────────────

def test_el_ruleset_apunta_a_develop(ruleset):
    incluidas = ruleset["conditions"]["ref_name"]["include"]
    assert incluidas == [f"refs/heads/{RAMA}"], incluidas


def test_el_ruleset_esta_activo_no_en_modo_informativo(ruleset):
    """`evaluate` informa y deja mergear: es exactamente lo que AC1 no quiere."""
    assert ruleset["enforcement"] == "active"


def test_nadie_salta_la_regla(ruleset):
    assert ruleset["bypass_actors"] == [], (
        "un bypass permanente convierte el gate obligatorio en una sugerencia"
    )


def test_cada_check_obligatorio_es_un_job_que_existe(ci, ruleset):
    """El renombrado silencioso. Si esto falla, actualiza el JSON *y* reimporta."""
    nombres = set(_nombres_de_job(ci).values())
    contextos = {
        c["context"]
        for c in _reglas(ruleset, "required_status_checks")[0]["parameters"]["required_status_checks"]
    }
    huerfanos = contextos - nombres
    assert not huerfanos, (
        f"el ruleset exige checks que ningún job de ci.yml produce: {sorted(huerfanos)}. "
        "Un check requerido que no llega a ejecutarse bloquea la PR para siempre."
    )


@pytest.mark.parametrize("job_id", sorted(ELEMENTOS_AC1))
def test_los_elementos_de_ac1_son_checks_obligatorios(ci, ruleset, job_id):
    """No basta con que el job corra: si no es obligatorio, la PR mergea en rojo."""
    contextos = {
        c["context"]
        for c in _reglas(ruleset, "required_status_checks")[0]["parameters"]["required_status_checks"]
    }
    nombres = _nombres_de_job(ci)
    # Localiza el job por su huella en el YAML y exige que su nombre sea requerido.
    huella = ELEMENTOS_AC1[job_id]
    culpables = [
        nombre
        for jid, nombre in nombres.items()
        if huella in yaml.safe_dump(ci["jobs"][jid], allow_unicode=True)
    ]
    assert culpables, f"ningún job de ci.yml ejecuta {huella}"
    assert any(nombre in contextos for nombre in culpables), (
        f"el job que ejecuta {huella} ({culpables}) no está entre los checks obligatorios"
    )


def test_el_gate_edd_no_es_obligatorio(ruleset):
    """Tiene filtro de rutas: obligarlo bloquea toda PR que no toque evaluación.

    Un check requerido que no se dispara se queda en *Expected* indefinidamente, así
    que marcarlo no endurece nada — rompe el resto de las PR.
    """
    edd = yaml.safe_load(EDD_WORKFLOW.read_text(encoding="utf-8"))
    con_rutas = {
        (cuerpo or {}).get("name", jid)
        for jid, cuerpo in edd["jobs"].items()
    }
    contextos = {
        c["context"]
        for c in _reglas(ruleset, "required_status_checks")[0]["parameters"]["required_status_checks"]
    }
    assert not (con_rutas & contextos), (
        f"{sorted(con_rutas & contextos)} pertenece a un workflow con filtro de rutas"
    )


def test_la_regla_prohibe_el_push_directo_y_el_force_push(ruleset):
    tipos = {r["type"] for r in ruleset["rules"]}
    assert "pull_request" in tipos, "sin esta regla se puede empujar directo a develop"
    assert "non_fast_forward" in tipos, "sin esta regla se puede reescribir develop"
    assert "deletion" in tipos, "sin esta regla se puede borrar develop"


def test_la_rama_debe_estar_al_dia_antes_de_mergear(ruleset):
    """Dos PR verdes contra una base vieja pueden romper develop al juntarse."""
    parametros = _reglas(ruleset, "required_status_checks")[0]["parameters"]
    assert parametros["strict_required_status_checks_policy"] is True


def test_las_areas_de_codeowners_piden_a_su_owner(ruleset):
    parametros = _reglas(ruleset, "pull_request")[0]["parameters"]
    assert parametros["require_code_owner_review"] is True


def test_el_documento_explica_la_desviacion_del_numero_de_aprobaciones(ruleset):
    """GOVERNANCE §4 pide 1 aprobación y el ruleset lleva otro número.

    La desviación tiene motivo (nadie aprueba su propia PR, y aquí hay un solo
    mantenedor), pero una desviación **sin explicar** entre la política escrita y la
    regla aplicada es la forma más habitual de que una política deje de ser cierta.
    """
    aprobaciones = _reglas(ruleset, "pull_request")[0]["parameters"]["required_approving_review_count"]
    if aprobaciones >= 1:
        return  # coincide con §4: no hay nada que explicar
    texto = DOC.read_text(encoding="utf-8")
    assert "required_approving_review_count" in texto, (
        "el ruleset se desvía de GOVERNANCE §4 y branch-protection.md no lo justifica"
    )


# ── El documento y el artefacto se apuntan mutuamente ──────────────────────────

def test_el_documento_nombra_el_fichero_que_hay_que_importar():
    assert ".github/rulesets/develop.json" in DOC.read_text(encoding="utf-8")


def test_la_gobernanza_enlaza_el_documento():
    gobernanza = (REPO_DIR / "docs" / "governance" / "GOVERNANCE.md").read_text(encoding="utf-8")
    assert "branch-protection.md" in gobernanza, (
        "una regla que no se enlaza desde GOVERNANCE no la encuentra quien la necesita"
    )
