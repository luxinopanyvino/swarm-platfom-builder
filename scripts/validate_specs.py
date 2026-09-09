#!/usr/bin/env python3
"""Valida el formato SDD de las especificaciones (docs/specs/SPEC-*.md).

Comprobaciones (deterministas, sin red ni GitHub):
- Toda spec en estado Ready/In progress/Done debe tener el bloque ``sdd-sync``.
- El bloque parsea como YAML y cumple el esquema: epic{id,title,area} + tasks[].
- IDs con formato estable: épica ``E<n>``, tarea ``T<n>.<m>``; sin duplicados
  globales de tarea; ``sev`` ∈ {high,medium,low}.
- ``acceptance`` referencia AC existentes en la sección 3 de esa misma spec.
- ``depends_on`` tiene formato ``T<n>.<m>`` o ``#<issue>``.

Uso:    python scripts/validate_specs.py
Salida: código 0 si todo es válido; 1 si hay errores (los imprime).
Lo ejecuta la CI; también es útil en local antes de abrir PR.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SPECS_DIR = Path(__file__).resolve().parents[1] / "docs" / "specs"
SYNC_STATES = {"Ready", "In progress", "Done"}
ALLOWED_AREAS = {
    "area/security", "area/infra", "area/backend",
    "area/observability", "area/governance", "area/ux",
    "area/evaluation", "area/qa",
}
ALLOWED_SEV = {"high", "medium", "low"}

EPIC_ID_RE = re.compile(r"^E\d+$")
TASK_ID_RE = re.compile(r"^T\d+\.\d+$")
DEP_RE = re.compile(r"^(T\d+\.\d+|#\d+)$")


def _extract_state(text: str) -> str | None:
    m = re.search(r"^\s*-\s*\*\*Estado:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _extract_sync_block(text: str) -> str | None:
    """Return the YAML body of the fenced block that starts with '# sdd-sync'."""
    for m in re.finditer(r"```ya?ml\s*\n(.*?)```", text, re.DOTALL):
        body = m.group(1)
        if "sdd-sync" in body.splitlines()[0]:
            return body
    return None


def _extract_acceptance_ids(text: str) -> set[str]:
    return set(re.findall(r"\*\*(AC\d+)\*\*", text))


def validate() -> list[str]:
    errors: list[str] = []
    seen_task_ids: dict[str, str] = {}  # task_id -> spec filename

    spec_files = sorted(SPECS_DIR.glob("SPEC-*.md"))
    if not spec_files:
        return [f"No se encontraron specs en {SPECS_DIR}"]

    for spec in spec_files:
        name = spec.name
        text = spec.read_text(encoding="utf-8")
        state = _extract_state(text)
        block = _extract_sync_block(text)

        if state in SYNC_STATES and block is None:
            errors.append(f"{name}: estado '{state}' pero falta el bloque 'sdd-sync' (sección 8).")
            continue
        if block is None:
            continue  # Draft sin bloque: permitido

        try:
            data = yaml.safe_load(block) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{name}: el bloque sdd-sync no es YAML válido: {exc}")
            continue

        epic = data.get("epic")
        if not isinstance(epic, dict):
            errors.append(f"{name}: falta la clave 'epic' (objeto) en el bloque.")
        else:
            if not EPIC_ID_RE.match(str(epic.get("id", ""))):
                errors.append(f"{name}: epic.id '{epic.get('id')}' no tiene formato E<n>.")
            if not str(epic.get("title", "")).strip():
                errors.append(f"{name}: epic.title vacío.")
            if epic.get("area") not in ALLOWED_AREAS:
                errors.append(f"{name}: epic.area '{epic.get('area')}' no está en {sorted(ALLOWED_AREAS)}.")

        ac_ids = _extract_acceptance_ids(text)
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            errors.append(f"{name}: 'tasks' debe ser una lista no vacía.")
            tasks = []

        for i, task in enumerate(tasks):
            loc = f"{name}: tasks[{i}]"
            if not isinstance(task, dict):
                errors.append(f"{loc} no es un objeto.")
                continue
            tid = str(task.get("id", ""))
            if not TASK_ID_RE.match(tid):
                errors.append(f"{loc}: id '{tid}' no tiene formato T<n>.<m>.")
            elif tid in seen_task_ids:
                errors.append(f"{loc}: id '{tid}' duplicado (ya en {seen_task_ids[tid]}).")
            else:
                seen_task_ids[tid] = name

            if not str(task.get("title", "")).strip():
                errors.append(f"{loc}: title vacío.")
            if task.get("sev") not in ALLOWED_SEV:
                errors.append(f"{loc}: sev '{task.get('sev')}' no está en {sorted(ALLOWED_SEV)}.")

            deps = task.get("depends_on", [])
            if not isinstance(deps, list):
                errors.append(f"{loc}: depends_on debe ser una lista.")
            else:
                for d in deps:
                    if not DEP_RE.match(str(d)):
                        errors.append(f"{loc}: depends_on '{d}' no es T<n>.<m> ni #<issue>.")

            acc = task.get("acceptance", [])
            if not isinstance(acc, list):
                errors.append(f"{loc}: acceptance debe ser una lista.")
            else:
                for a in acc:
                    if str(a) not in ac_ids:
                        errors.append(f"{loc}: acceptance '{a}' no existe en los AC de la spec ({sorted(ac_ids)}).")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("[FAIL] Validacion de specs SDD fallida:\n")
        for e in errors:
            print(f"  - {e}")
        print(f"\n{len(errors)} error(es).")
        return 1
    print("[OK] Specs SDD validas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
