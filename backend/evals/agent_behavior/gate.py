"""Gate EDD de regresión (SPEC-014 / T9.5 / AC5).

Corre el conjunto `golden` de cada agente y compara las medias por métrica contra
los umbrales declarados en `thresholds.yaml`. Es lo que convierte el harness en
algo que **defiende** el comportamiento en vez de solo describirlo: un cambio de
`prompt_template` que baja la fidelidad de citas deja de ser invisible hasta que
alguien lea el artículo.

Uso (desde `backend/`):

    python -m evals.agent_behavior.gate            # el modo lo dice thresholds.yaml
    python -m evals.agent_behavior.gate --enforce  # forzar bloqueo
    python -m evals.agent_behavior.gate --warn     # forzar aviso
    python -m evals.agent_behavior.gate --json informe.json

Dos desenlaces que **no** son el mismo, y confundirlos vaciaría el gate:

* **Regresión** — una métrica cae por debajo de su umbral. En modo aviso se
  informa y se sale con 0; en modo bloqueo, con 1. Es una decisión de producto
  sobre cuánta confianza dan los datos (hoy los golden son `handwritten`).
* **Medición rota** — un dataset que no carga, un caso que revienta, un umbral
  declarado para una métrica que no se computa. **Sale con 1 siempre**, en los dos
  modos: no es que el agente haya empeorado, es que no se ha llegado a medir, y
  tragárselo en modo aviso dejaría un gate verde que no mira nada.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from evals.agent_behavior import runner
from evals.agent_behavior.models import EvalReport

UMBRALES = Path(__file__).resolve().parent / "thresholds.yaml"

SALIDA_OK = 0
SALIDA_FALLO = 1


class GateError(RuntimeError):
    """La medición no se pudo hacer. Nunca es un aviso."""


@dataclass
class Comparacion:
    agent: str
    metric: str
    minimo: float
    obtenido: Optional[float]
    baseline: Optional[float] = None

    @property
    def medida(self) -> bool:
        return self.obtenido is not None

    @property
    def regresa(self) -> bool:
        return self.medida and self.obtenido < self.minimo

    @property
    def margen(self) -> Optional[float]:
        return None if not self.medida else round(self.obtenido - self.minimo, 2)


@dataclass
class Resultado:
    enforce: bool
    comparaciones: List[Comparacion] = field(default_factory=list)
    #: Problemas de medición: dataset roto, caso que revienta, métrica ausente.
    errores: List[str] = field(default_factory=list)
    informes: Dict[str, EvalReport] = field(default_factory=dict)

    @property
    def regresiones(self) -> List[Comparacion]:
        return [c for c in self.comparaciones if c.regresa]

    @property
    def exit_code(self) -> int:
        if self.errores:
            return SALIDA_FALLO
        if self.regresiones and self.enforce:
            return SALIDA_FALLO
        return SALIDA_OK


def cargar_umbrales(ruta: Path = UMBRALES) -> Dict[str, Any]:
    if not ruta.is_file():
        raise GateError(f"No existe el fichero de umbrales {ruta}")
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    if not isinstance(datos.get("agents"), dict) or not datos["agents"]:
        raise GateError(f"{ruta.name} no declara ningún agente en 'agents'")
    return datos


async def evaluar(
    umbrales: Optional[Dict[str, Any]] = None,
    *,
    enforce: Optional[bool] = None,
) -> Resultado:
    """Corre los golden y compara. No imprime nada: eso es de `render`."""
    datos = umbrales if umbrales is not None else cargar_umbrales()
    resultado = Resultado(
        enforce=bool(datos.get("enforce", False)) if enforce is None else enforce
    )

    for agente, config in (datos.get("agents") or {}).items():
        dataset = (config or {}).get("dataset") or f"{agente}-golden"
        declaradas = (config or {}).get("metrics") or {}
        if not declaradas:
            resultado.errores.append(f"{agente}: no declara ninguna métrica en los umbrales")
            continue

        try:
            informe = await runner.run(dataset, mode="replay", agent=agente)
        except Exception as error:
            resultado.errores.append(
                f"{agente}: no se pudo evaluar '{dataset}': {error.__class__.__name__}: {error}"
            )
            continue

        resultado.informes[agente] = informe

        # Un caso que revienta no puntúa: su métrica saldría de la media sin que
        # nadie lo note, y el gate daría verde sobre menos casos de los que cree.
        rotos = [c.case_id for c in informe.cases if c.error]
        if rotos:
            resultado.errores.append(
                f"{agente}: {len(rotos)} caso(s) no se pudieron ejecutar: {', '.join(rotos)}"
            )

        obtenidas = informe.scores()
        for metrica, limite in declaradas.items():
            minimo = float(limite["min"] if isinstance(limite, dict) else limite)
            baseline = float(limite["baseline"]) if isinstance(limite, dict) and "baseline" in limite else None
            valor = obtenidas.get(metrica)
            if valor is None:
                # Declarar un umbral para algo que no se computa es un gate que
                # cree vigilar algo y no vigila nada. Es error de medición.
                resultado.errores.append(
                    f"{agente}: se declara umbral para '{metrica}' pero el informe no la trae "
                    f"(¿se saltó en todos los casos, o cambió de nombre?)"
                )
                continue
            resultado.comparaciones.append(
                Comparacion(agente, metrica, minimo, float(valor), baseline)
            )

    if not resultado.comparaciones and not resultado.errores:
        resultado.errores.append("el gate no comparó ninguna métrica: no hay nada que vigilar")
    return resultado


def render(resultado: Resultado, datos: Dict[str, Any]) -> str:
    lineas: List[str] = ["# Gate EDD — regresión de comportamiento", ""]

    modo = "BLOQUEO" if resultado.enforce else "AVISO"
    lineas.append(f"Modo: **{modo}**")
    if not resultado.enforce:
        lineas += [
            "",
            "> El gate **informa pero no bloquea** (SPEC-014 §5). Los conjuntos golden",
            f"> son `{(datos.get('baseline_taken') or {}).get('provenance', 'no declarada')}`:",
            "> un verde dice que las métricas funcionan, no que el modelo se comporte",
            "> así. Para endurecer: regraba los golden en `--mode live` y pon",
            "> `enforce: true` en `thresholds.yaml`.",
        ]
    lineas.append("")

    if resultado.errores:
        lineas += ["## ❌ La medición no se pudo hacer", ""]
        lineas += [f"- {e}" for e in resultado.errores]
        lineas += ["", "Esto **rompe la PR en los dos modos**: no es que el agente haya",
                   "empeorado, es que no se ha llegado a medir.", ""]

    if resultado.comparaciones:
        lineas += [
            "## Métricas",
            "",
            "| Agente | Métrica | Obtenido | Mínimo | Margen | Línea base |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for c in resultado.comparaciones:
            icono = "❌" if c.regresa else "✅"
            base = "—" if c.baseline is None else f"{c.baseline}"
            lineas.append(
                f"| `{c.agent}` | `{c.metric}` | {icono} {c.obtenido} | {c.minimo} "
                f"| {c.margen:+} | {base} |"
            )
        lineas.append("")

    if resultado.regresiones:
        lineas += ["## ⚠️ Regresiones", ""]
        for c in resultado.regresiones:
            lineas.append(
                f"- `{c.agent}` · `{c.metric}`: {c.obtenido} < {c.minimo} "
                f"(línea base {c.baseline if c.baseline is not None else '—'})"
            )
        lineas.append("")
        lineas.append(
            "Si el cambio es deliberado y el nuevo comportamiento es el bueno, baja el "
            "umbral en `thresholds.yaml` **en esta misma PR**: así el relajo queda "
            "revisado en vez de ocurrir en silencio."
        )
    elif not resultado.errores:
        lineas.append("✅ Ninguna métrica por debajo de su umbral.")

    return "\n".join(lineas) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--enforce", action="store_true", help="una regresión rompe la PR")
    modo.add_argument("--warn", action="store_true", help="informar sin bloquear")
    parser.add_argument("--thresholds", default=None, help="fichero de umbrales alternativo")
    parser.add_argument("--json", default=None, help="escribe el detalle en este fichero")
    args = parser.parse_args(argv)

    forzado = True if args.enforce else (False if args.warn else None)
    ruta = Path(args.thresholds) if args.thresholds else UMBRALES

    try:
        datos = cargar_umbrales(ruta)
        resultado = asyncio.run(evaluar(datos, enforce=forzado))
    except GateError as error:
        print(f"El gate EDD no se pudo ejecutar: {error}", file=sys.stderr)
        return SALIDA_FALLO

    print(render(resultado, datos))

    if args.json:
        import json

        Path(args.json).write_text(json.dumps({
            "enforce": resultado.enforce,
            "exit_code": resultado.exit_code,
            "errors": resultado.errores,
            "metrics": [
                {"agent": c.agent, "metric": c.metric, "value": c.obtenido,
                 "min": c.minimo, "baseline": c.baseline, "regressed": c.regresa}
                for c in resultado.comparaciones
            ],
        }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if resultado.regresiones and not resultado.enforce:
        print(
            f"[aviso] {len(resultado.regresiones)} métrica(s) por debajo de su umbral; "
            "el gate no bloquea todavía (ver thresholds.yaml).",
            file=sys.stderr,
        )
    return resultado.exit_code


if __name__ == "__main__":
    sys.exit(main())
