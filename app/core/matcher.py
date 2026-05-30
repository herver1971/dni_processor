"""
Módulo de Matcheo — Emparejamiento de frentes con dorsos.

Empareja DNIs detectados como "frente" con sus correspondientes "dorso"
basándose en el número de DNI extraído por OCR.

Algoritmo:
1. Para cada frente con número leído, buscar el dorso con número más cercano
   (distancia Levenshtein).
2. Aceptar match si distancia ≤ MATCH_MAX_DISTANCE.
3. Resolver conflictos: si dos frentes apuntan al mismo dorso, se asigna
   al que tenga menor distancia (mejor confianza relativa).
4. Frentes y dorsos sin emparejar quedan como huérfanos para resolución manual.

DECISIÓN DE DISEÑO IMPORTANTE: nunca emparejamos por "cercanía de orden de
subida" u otras heurísticas. En contexto notarial, un falso positivo es
peor que un huérfano explícito. Si no podemos garantizar el match por OCR,
preferimos pasarlo a revisión manual.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import Levenshtein

from app.core.constants import MATCH_MAX_DISTANCE
from app.schemas.session import DetectedDNI, MatchedPair, UnpairedDNI

logger = logging.getLogger(__name__)


@dataclass
class _MatchCandidate:
    """Candidato de matcheo intermedio durante el algoritmo."""
    frente_idx: int
    dorso_idx: int
    distance: int


def _compute_distance(num_a: str | None, num_b: str | None) -> int | None:
    """
    Calcula la distancia Levenshtein entre dos números de DNI.

    Returns:
        Distancia entera, o None si alguno de los números es None
        (no se puede comparar).
    """
    if num_a is None or num_b is None:
        return None
    return Levenshtein.distance(num_a, num_b)


def match_frentes_dorsos(
    frentes: list[DetectedDNI],
    dorsos: list[DetectedDNI],
) -> tuple[list[MatchedPair], list[UnpairedDNI], list[UnpairedDNI]]:
    """
    Empareja frentes con dorsos.

    Args:
        frentes: Lista de DNIs clasificados como frente.
        dorsos: Lista de DNIs clasificados como dorso.

    Returns:
        Tupla (pares_matcheados, frentes_huérfanos, dorsos_huérfanos).
    """
    pairs: list[MatchedPair] = []

    # 1. Generar todos los candidatos de matcheo con distancia <= threshold
    candidates: list[_MatchCandidate] = []
    for fi, frente in enumerate(frentes):
        for di, dorso in enumerate(dorsos):
            dist = _compute_distance(frente.dni_number, dorso.dni_number)
            if dist is not None and dist <= MATCH_MAX_DISTANCE:
                candidates.append(_MatchCandidate(fi, di, dist))

    # 2. Ordenar candidatos por distancia ascendente (los mejores primero)
    candidates.sort(key=lambda c: c.distance)

    # 3. Asignación greedy: tomar el mejor candidato disponible para cada par.
    # Esto resuelve conflictos automáticamente: si dos frentes pueden matchear
    # con el mismo dorso, gana el de menor distancia.
    used_frentes: set[int] = set()
    used_dorsos: set[int] = set()

    for cand in candidates:
        if cand.frente_idx in used_frentes or cand.dorso_idx in used_dorsos:
            continue

        frente = frentes[cand.frente_idx]
        dorso = dorsos[cand.dorso_idx]

        pairs.append(
            MatchedPair(
                frente=frente,
                dorso=dorso,
                match_distance=cand.distance,
                is_exact_match=(cand.distance == 0),
            )
        )
        used_frentes.add(cand.frente_idx)
        used_dorsos.add(cand.dorso_idx)
        logger.info(
            f"Match: frente '{frente.dni_number}' ↔ dorso '{dorso.dni_number}' "
            f"(distancia={cand.distance})"
        )

    # 4. Construir lista de huérfanos
    unpaired_frentes: list[UnpairedDNI] = []
    for fi, frente in enumerate(frentes):
        if fi not in used_frentes:
            reason = (
                "No se extrajo número por OCR"
                if frente.dni_number is None
                else f"Ningún dorso con número compatible (DNI leído: {frente.dni_number})"
            )
            unpaired_frentes.append(UnpairedDNI(detected=frente, reason=reason))

    unpaired_dorsos: list[UnpairedDNI] = []
    for di, dorso in enumerate(dorsos):
        if di not in used_dorsos:
            reason = (
                "No se extrajo número por OCR"
                if dorso.dni_number is None
                else f"Ningún frente con número compatible (DNI leído: {dorso.dni_number})"
            )
            unpaired_dorsos.append(UnpairedDNI(detected=dorso, reason=reason))

    logger.info(
        f"Matcheo completo: {len(pairs)} pares, "
        f"{len(unpaired_frentes)} frentes huérfanos, "
        f"{len(unpaired_dorsos)} dorsos huérfanos"
    )

    return pairs, unpaired_frentes, unpaired_dorsos
