"""Gate EDD de regresión en CI (SPEC-014 / T9.5 / AC5).

AC5: cuando un PR toca perfiles, prompts o modelos de agentes, la CI corre la
suite de evals y **falla o avisa** si alguna métrica cae por debajo de su umbral.

Lo que hace útil a este gate no es que exista, sino que distinga dos cosas que se
parecen y no lo son:

* una **regresión** —una métrica baja— es una decisión de producto sobre cuánta
  confianza dan los datos, y hoy los golden son `handwritten`, así que avisa;
* una **medición rota** —un dataset que no carga, un caso que revienta, un umbral
  declarado para una métrica que no se computa— **rompe siempre**. Tragársela en
  modo aviso dejaría un gate verde que no mira nada, que es peor que no tenerlo.

Y una guarda que viene de un fallo real: la lista de rutas del AC de la spec
(`backend/app/agents/*.agent.md`, `shared/llm.py`) **ya estaba obsoleta** cuando se
escribió esta tarea — esos ficheros se movieron en T8.3/T8.4. Un gate que vigila
rutas que no existen da verde por no mirar nada, así que hay un test que comprueba
que cada ruta del workflow existe de verdad en el repo.
"""
import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = ROOT_DIR.parent
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "ci-secret-not-for-prod")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_edd_gate.db")

from evals.agent_behavior import gate, loader  # noqa: E402

UMBRALES = ROOT_DIR / "evals" / "agent_behavior" / "thresholds.yaml"
WORKFLOW = REPO_DIR / ".github" / "workflows" / "edd-gate.yml"


@pytest.fixture(scope="module")
def umbrales():
    return gate.cargar_umbrales(UMBRALES)


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


# ── Los umbrales declarados ─────────────────────────────────────────────────

def test_hay_umbrales_para_cada_agente_evaluable(umbrales):
    """Un agente con conjunto golden y sin umbral es un agente sin vigilar."""
    declarados = set(umbrales["agents"])
    con_golden = {
        p.stem.replace("-golden", "")
        for p in loader.DATASETS_DIR.glob("*-golden.jsonl")
    }
    assert declarados == con_golden, (
        f"declarados={sorted(declarados)} vs con conjunto golden={sorted(con_golden)}"
    )


@pytest.mark.asyncio
async def test_cada_umbral_vigila_una_metrica_que_de_verdad_se_computa(umbrales):
    """Declarar un umbral para una métrica que nadie calcula es un gate que cree
    vigilar algo y no vigila nada. El propio gate lo trata como medición rota."""
    resultado = await gate.evaluar(umbrales)
    assert resultado.errores == [], resultado.errores
    assert resultado.comparaciones, "el gate no comparó ninguna métrica"


def test_la_linea_base_no_esta_por_debajo_del_umbral(umbrales):
    """Si `baseline < min`, el gate nace rojo y nadie se fía de él."""
    culpables = []
    for agente, config in umbrales["agents"].items():
        for metrica, limite in config["metrics"].items():
            if isinstance(limite, dict) and "baseline" in limite:
                if float(limite["baseline"]) < float(limite["min"]):
                    culpables.append(f"{agente}.{metrica}")
    assert culpables == [], f"la línea base ya incumple el umbral en: {culpables}"


def test_los_umbrales_dicen_de_donde_sale_su_linea_base(umbrales):
    """Un número sin procedencia no se puede comparar con el de dentro de un mes;
    y mientras los golden sean `handwritten`, el gate mide la métrica, no el modelo."""
    tomada = umbrales.get("baseline_taken") or {}
    assert tomada.get("date") and tomada.get("mode") and tomada.get("provenance")


# ── Los dos desenlaces ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_el_gate_pasa_sobre_la_linea_base_actual(umbrales):
    resultado = await gate.evaluar(umbrales)
    assert resultado.regresiones == [], [
        f"{c.agent}.{c.metric}={c.obtenido}<{c.minimo}" for c in resultado.regresiones
    ]
    assert resultado.exit_code == gate.SALIDA_OK


@pytest.mark.asyncio
async def test_una_regresion_se_detecta():
    """El caso que justifica el gate: la métrica baja del umbral."""
    resultado = await gate.evaluar({
        "enforce": False,
        "agents": {"revisor": {"dataset": "revisor-golden",
                               "metrics": {"reviewer_calibration": {"min": 99}}}},
    })
    assert len(resultado.regresiones) == 1
    assert resultado.regresiones[0].metric == "reviewer_calibration"


@pytest.mark.asyncio
async def test_en_modo_aviso_una_regresion_no_rompe_pero_se_ve():
    """SPEC-014 §5: arranca avisando. Avisar no es callar."""
    umbrales = {
        "enforce": False,
        "agents": {"revisor": {"dataset": "revisor-golden",
                               "metrics": {"reviewer_calibration": {"min": 99}}}},
    }
    resultado = await gate.evaluar(umbrales)
    assert resultado.exit_code == gate.SALIDA_OK
    salida = gate.render(resultado, umbrales)
    assert "Regresiones" in salida and "reviewer_calibration" in salida


@pytest.mark.asyncio
async def test_en_modo_bloqueo_la_misma_regresion_rompe():
    resultado = await gate.evaluar({
        "enforce": True,
        "agents": {"revisor": {"dataset": "revisor-golden",
                               "metrics": {"reviewer_calibration": {"min": 99}}}},
    })
    assert resultado.exit_code == gate.SALIDA_FALLO


@pytest.mark.asyncio
async def test_el_modo_lo_decide_el_fichero_de_umbrales():
    """Endurecer el gate debe ser un diff junto a los umbrales que endurece, no un
    cambio de infraestructura en el workflow."""
    assert (await gate.evaluar({"enforce": True, "agents": {}})).enforce is True
    assert (await gate.evaluar({"enforce": False, "agents": {}})).enforce is False


# ── Medición rota: rompe en los dos modos ───────────────────────────────────

@pytest.mark.asyncio
async def test_un_dataset_que_no_carga_rompe_aunque_sea_modo_aviso():
    """No es que el agente haya empeorado: es que no se ha llegado a medir."""
    resultado = await gate.evaluar({
        "enforce": False,
        "agents": {"fantasma": {"dataset": "no-existe-golden", "metrics": {"budget": {"min": 50}}}},
    })
    assert resultado.errores
    assert resultado.exit_code == gate.SALIDA_FALLO


@pytest.mark.asyncio
async def test_un_umbral_de_una_metrica_inexistente_rompe():
    """`coherence` no aplica al formateador: su caso no la declara y se salta en
    todos, así que el umbral vigilaría el vacío."""
    resultado = await gate.evaluar({
        "enforce": False,
        "agents": {"formateador": {"dataset": "formateador-golden",
                                   "metrics": {"coherence": {"min": 70}}}},
    })
    assert any("coherence" in e for e in resultado.errores)
    assert resultado.exit_code == gate.SALIDA_FALLO


@pytest.mark.asyncio
async def test_un_agente_sin_metricas_declaradas_rompe():
    resultado = await gate.evaluar({
        "enforce": False, "agents": {"revisor": {"dataset": "revisor-golden", "metrics": {}}},
    })
    assert resultado.exit_code == gate.SALIDA_FALLO


@pytest.mark.asyncio
async def test_un_gate_que_no_compara_nada_no_puede_dar_verde():
    """El fallo silencioso más fácil de este diseño: quedarse sin agentes y salir
    con 0 porque «no hubo regresiones»."""
    resultado = await gate.evaluar({"enforce": False, "agents": {}})
    assert resultado.exit_code == gate.SALIDA_FALLO


def test_un_fichero_de_umbrales_ausente_no_pasa_de_largo():
    with pytest.raises(gate.GateError):
        gate.cargar_umbrales(ROOT_DIR / "no" / "existe.yaml")


def test_la_cli_devuelve_el_codigo_de_salida_del_resultado(tmp_path):
    """Es lo que consume la CI: sin código de salida, el gate es decoración."""
    roto = tmp_path / "roto.yaml"
    roto.write_text(
        "version: 1\nenforce: false\nagents:\n  fantasma:\n"
        "    dataset: no-existe-golden\n    metrics:\n      budget: {min: 50}\n",
        encoding="utf-8",
    )
    assert gate.main(["--thresholds", str(roto)]) == gate.SALIDA_FALLO
    assert gate.main([]) == gate.SALIDA_OK


# ── El workflow ─────────────────────────────────────────────────────────────

def test_el_workflow_existe_y_corre_el_gate(workflow):
    pasos = workflow["jobs"]["gate"]["steps"]
    corre = [p for p in pasos if "evals.agent_behavior.gate" in str(p.get("run", ""))]
    assert corre, "el workflow no ejecuta el gate"


def test_el_workflow_se_dispara_en_prs_a_develop(workflow):
    # `True` y no `"on"`: PyYAML interpreta `on:` como booleano.
    disparador = workflow[True]["pull_request"]
    assert disparador["branches"] == ["develop"]
    assert disparador["paths"], "sin filtro de rutas correría en cada PR"


@pytest.mark.parametrize("ruta", [
    "backend/app/platform/llm.py",
    "backend/app/shared/agents_seed.py",
    "backend/app/modules/agents/adapters",
    "backend/app/platform/engine",
    "backend/evals/agent_behavior",
])
def test_cada_ruta_vigilada_existe_de_verdad(workflow, ruta):
    """La lista de rutas del AC de la spec ya estaba obsoleta al escribirse esta
    tarea: apuntaba a `backend/app/agents/` y `shared/llm.py`, movidos en T8.3/T8.4.
    Un gate que vigila rutas inexistentes da verde por no mirar nada."""
    patrones = workflow[True]["pull_request"]["paths"]
    assert (REPO_DIR / ruta).exists(), f"{ruta} no existe en el repo"
    assert any(ruta in p for p in patrones), f"{ruta} no está vigilada por el workflow"


def test_se_vigilan_los_perfiles_de_los_agentes(workflow):
    """Cambiar un `.agent.md` es cambiar el prompt: es el caso central de AC5."""
    patrones = workflow[True]["pull_request"]["paths"]
    assert any(".agent.md" in p for p in patrones)
    assert list(REPO_DIR.glob("backend/projects/*/agents/*.agent.md")), (
        "no hay perfiles donde el patrón dice que los hay"
    )


def test_el_gate_no_necesita_modelo_para_correr_en_ci(workflow):
    """Corre en `replay`: si llamara al modelo, la CI dependería de tener Ollama o
    una clave, y el gate se caería por motivos ajenos al comportamiento."""
    fuente = (ROOT_DIR / "evals" / "agent_behavior" / "gate.py").read_text(encoding="utf-8")
    assert 'mode="replay"' in fuente
    entorno = workflow["jobs"]["gate"].get("env", {})
    assert "OLLAMA_BASE_URL" not in entorno and "ANTHROPIC_API_KEY" not in entorno
