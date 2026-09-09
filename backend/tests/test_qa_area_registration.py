"""Alta del área `area/qa` y coherencia del plan de QA/evaluación (SPEC-026, SPEC-027).

Dar de alta un área es tocar cuatro sitios —validador, seed del Project, gobernanza
y backlog— y hacerlo en tres deja el validador rechazando specs perfectamente
válidas, o el seed sin la etiqueta que los issues van a pedir. Ya se probó así para
`area/evaluation` en T9.6; esto es lo mismo para `area/qa`.

Los últimos casos van más allá del alta y cubren un fallo que este mismo plan estuvo
a punto de cometer: **SPEC-026 y SPEC-027 nacieron con las épicas E10 y E11, que ya
estaban ocupadas** por SPEC-021 y SPEC-022. Lo cazó `validate_specs.py`, que
comprueba duplicados de tarea; los IDs de épica, en cambio, no los comprueba nadie
—dos specs pueden declarar la misma y el validador pasa—, y una épica duplicada
manda las tareas de dos specs al mismo sitio del Project.
"""
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
VALIDADOR = REPO_DIR / "scripts" / "validate_specs.py"
SEED = REPO_DIR / "scripts" / "seed_github_project.py"
GOBERNANZA = REPO_DIR / "docs" / "governance" / "GOVERNANCE.md"
BACKLOG = REPO_DIR / "docs" / "backlog" / "security-hardening-backlog.md"
SPECS_DIR = REPO_DIR / "docs" / "specs"

AREA = "area/qa"


# ── El alta, en los cuatro sitios ────────────────────────────────────────────

@pytest.mark.parametrize("nombre,ruta", [
    ("validador", VALIDADOR),
    ("seed del Project", SEED),
    ("gobernanza", GOBERNANZA),
    ("backlog", BACKLOG),
])
def test_el_area_esta_dada_de_alta(nombre, ruta):
    assert AREA in ruta.read_text(encoding="utf-8"), f"{AREA} no aparece en el {nombre}"


def test_el_validador_acepta_specs_del_area():
    """No basta con que la cadena aparezca: tiene que estar en la lista que decide."""
    from importlib import util

    spec = util.spec_from_file_location("validate_specs_qa", VALIDADOR)
    modulo = util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert AREA in modulo.ALLOWED_AREAS


def test_el_seed_declara_la_etiqueta_con_color():
    """El seed crea las labels; una que falte deja los issues sin su área."""
    texto = SEED.read_text(encoding="utf-8")
    assert re.search(rf'\("{re.escape(AREA)}",\s*"[0-9a-f]{{6}}",', texto), (
        f"{AREA} no está en LABELS con color válido"
    )


# ── Que las specs del plan sean coherentes entre sí ──────────────────────────

def _bloques_sync():
    """(spec, dict) del bloque `sdd-sync` de cada spec que lo tenga."""
    for ruta in sorted(SPECS_DIR.glob("SPEC-*.md")):
        texto = ruta.read_text(encoding="utf-8")
        for m in re.finditer(r"```ya?ml\s*\n(.*?)```", texto, re.DOTALL):
            cuerpo = m.group(1)
            if "sdd-sync" in cuerpo.splitlines()[0]:
                yield ruta.name, yaml.safe_load(cuerpo)
                break


@pytest.mark.parametrize("spec_name,epica", [
    ("SPEC-026-qa-test-strategy.md", "E14"),
    ("SPEC-027-model-evaluation-deepeval.md", "E15"),
])
def test_las_epicas_nuevas_no_pisan_a_ninguna_existente(spec_name, epica):
    """El fallo que estuvo a punto de colarse: estas dos specs nacieron con E10 y
    E11, que ya eran de SPEC-021 y SPEC-022.

    Lo cazó `validate_specs.py`, pero de rebote —comprueba duplicados de **tarea**,
    no de épica— y solo porque los IDs de tarea heredan el número de la épica. Si
    las tareas hubieran empezado en otro número, la colisión habría llegado al
    Project.

    Y no se puede exigir que ninguna épica se comparta, porque **aquí se comparten a
    propósito**: E2 la declaran SPEC-002 (Superseded), SPEC-016 y SPEC-024, y E1 la
    comparten SPEC-001 y SPEC-015 por la adopción retroactiva de ADR-0007. Una
    épica puede tener más de una spec; lo que no puede es que una spec nueva se
    ponga encima de otra sin querer. Eso es lo que se fija.
    """
    duenos = [s for s, datos in _bloques_sync() if datos["epic"]["id"] == epica]
    assert duenos == [spec_name], (
        f"{epica} la declara además {[d for d in duenos if d != spec_name]}"
    )


def test_los_ids_de_tarea_pertenecen_a_su_epica():
    """`T14.3` en la épica `E15` es un despiste que nadie ve hasta el Project."""
    descuadres = []
    for spec, datos in _bloques_sync():
        numero = datos["epic"]["id"][1:]
        for tarea in datos.get("tasks") or []:
            if not tarea["id"].startswith(f"T{numero}."):
                descuadres.append(f"{spec}: {tarea['id']} en épica {datos['epic']['id']}")
    assert not descuadres, descuadres


@pytest.mark.parametrize("spec_name", [
    "SPEC-026-qa-test-strategy.md",
    "SPEC-027-model-evaluation-deepeval.md",
])
def test_las_dependencias_apuntan_a_tareas_que_existen(spec_name):
    """Una dependencia hacia una tarea inexistente deja el orden sin sentido."""
    datos = dict(_bloques_sync())[spec_name]
    ids = {t["id"] for t in datos["tasks"]}
    huerfanas = [
        f"{t['id']} → {dep}"
        for t in datos["tasks"]
        for dep in (t.get("depends_on") or [])
        if not dep.startswith("#") and dep not in ids
    ]
    assert not huerfanas, huerfanas


@pytest.mark.parametrize("spec_name", [
    "SPEC-026-qa-test-strategy.md",
    "SPEC-027-model-evaluation-deepeval.md",
])
def test_cada_criterio_de_aceptacion_lo_cubre_alguna_tarea(spec_name):
    """Un AC que ninguna tarea reclama no lo va a implementar nadie.

    `validate_specs.py` comprueba lo contrario —que un `acceptance` apunte a un AC
    que existe—, pero no que todos los AC estén cubiertos. Un AC huérfano es una
    promesa de la spec que el backlog no recoge.
    """
    ruta = SPECS_DIR / spec_name
    declarados = set(re.findall(r"\*\*(AC\d+)\*\*", ruta.read_text(encoding="utf-8")))
    datos = dict(_bloques_sync())[spec_name]
    cubiertos = {ac for t in datos["tasks"] for ac in (t.get("acceptance") or [])}
    assert declarados <= cubiertos, f"AC sin tarea que los cubra: {sorted(declarados - cubiertos)}"


def test_el_adr_de_deepeval_existe_y_lo_enlaza_su_spec():
    adr = REPO_DIR / "docs" / "adr" / "0010-adopt-deepeval-as-metrics-library.md"
    assert adr.is_file()
    spec = (SPECS_DIR / "SPEC-027-model-evaluation-deepeval.md").read_text(encoding="utf-8")
    assert "0010-adopt-deepeval-as-metrics-library" in spec
