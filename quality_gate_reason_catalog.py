from __future__ import annotations

from typing import Any


CHECK_CATALOG: dict[str, dict[str, Any]] = {
    "canvas_integrity.perimeter_low_frequency_continuity": {
        "group": "CANVAS_INTEGRITY",
        "criterion": "perimeter_low_frequency_continuity",
        "lock_scope": "GEOMETRY",
        "lock_mode": "HARD_LOCK",
        "severity": "FAIL",
        "title": "Нарушение целостности по периметру кадра",
        "description": "По краю изображения обнаружен неестественный низкочастотный переход, который может выглядеть как рамка, шов или локальное изменение фона.",
        "impact": "В FINAL может сохраниться заметный артефакт по границе изображения.",
        "suggested_action": "regenerate_donor",
        "suggested_action_label": "Проверить край на 100% и при подтверждении перегенерировать донор",
        "overlay": {"type": "border", "thickness": 0.055},
    },
    "canvas_integrity.no_unsupported_long_border_seam": {
        "group": "CANVAS_INTEGRITY",
        "criterion": "no_unsupported_long_border_seam",
        "lock_scope": "GEOMETRY",
        "lock_mode": "HARD_LOCK",
        "severity": "FAIL",
        "title": "Длинный неподтверждённый шов у границы кадра",
        "description": "Вдоль края найден протяжённый контур, которого валидатор не подтверждает по исходнику. Это типичный признак border seam или frame-within-frame артефакта.",
        "impact": "Может нарушать непрерывность сцены и быть хорошо заметен после публикации в 4K.",
        "suggested_action": "regenerate_donor",
        "suggested_action_label": "Открыть Split/Diff, проверить шов и при подтверждении перегенерировать донор",
        "overlay": {"type": "border", "thickness": 0.045},
    },
    "canvas_integrity.source_edge_preservation": {
        "group": "CANVAS_INTEGRITY",
        "criterion": "source_edge_preservation",
        "lock_scope": "GEOMETRY",
        "lock_mode": "HARD_LOCK",
        "severity": "FAIL",
        "title": "Недостаточное сохранение исходных контуров",
        "description": "Часть основных контуров SOURCE не совпадает с кандидатом после регистрации.",
        "impact": "Возможны изменения геометрии, силуэта или положения структурных элементов.",
        "suggested_action": "regenerate_donor",
        "suggested_action_label": "Проверить Edge Diff и перегенерировать при подтверждении",
    },
    "canvas_integrity.critical_dense_structure_preservation": {
        "group": "CANVAS_INTEGRITY",
        "criterion": "critical_dense_structure_preservation",
        "lock_scope": "ARCHITECTURE",
        "lock_mode": "HARD_LOCK",
        "severity": "FAIL",
        "title": "Потеря плотной структурной детали",
        "description": "В зоне с высокой плотностью деталей изменена структура, которую система считает критичной для исходной сцены.",
        "impact": "Могут быть изменены фасадные ритмы, ограждения, оконная сетка или другие повторяющиеся элементы.",
        "suggested_action": "manual_review",
        "suggested_action_label": "Проверить участок на 100% и в Split",
    },
    "canvas_integrity.paired_inset_side_seams": {
        "group": "CANVAS_INTEGRITY",
        "criterion": "paired_inset_side_seams",
        "lock_scope": "GEOMETRY",
        "lock_mode": "HARD_LOCK",
        "severity": "FAIL",
        "title": "Парные боковые швы",
        "description": "Обнаружена симметричная пара внутренних вертикальных границ, характерная для вставленного кадра или ошибочного outpaint.",
        "impact": "Кандидат может выглядеть как изображение, помещённое внутрь другой рамки.",
        "suggested_action": "regenerate_donor",
        "suggested_action_label": "Проверить боковые границы и перегенерировать донор",
        "overlay": {"type": "side_insets", "offset": 0.035},
    },
    "artifact_control.corrupt_grid_cells": {
        "group": "ARTIFACT_CONTROL",
        "criterion": "corrupt_grid_cells",
        "lock_scope": "TEXTURES",
        "lock_mode": "HARD_LOCK",
        "severity": "FAIL",
        "title": "Локальные повреждения структуры или текстуры",
        "description": "Одна или несколько контрольных ячеек отличаются от SOURCE сильнее допустимого порога.",
        "impact": "В локальной зоне возможны выдуманные детали, потеря фактуры или структурная деформация.",
        "suggested_action": "manual_review",
        "suggested_action_label": "Проверить 100% / Diff; при видимом дефекте перегенерировать донор",
    },
}


def _fallback(code: str) -> dict[str, Any]:
    if "." in code:
        group, criterion = code.split(".", 1)
    else:
        group, criterion = "QUALITY_GATE", code
    return {
        "group": group.upper(),
        "criterion": criterion,
        "lock_scope": "UNMAPPED",
        "lock_mode": "CHECK",
        "severity": "FAIL",
        "title": criterion.replace("_", " ").strip().capitalize() or code,
        "description": "Автоматическая проверка не прошла. Для этого кода пока нет отдельной человекочитаемой расшифровки.",
        "impact": "Кандидат требует визуальной проверки перед ручным утверждением.",
        "suggested_action": "manual_review",
        "suggested_action_label": "Открыть 100% / Split / Diff и принять решение вручную",
    }


def describe_failed_checks(codes: list[str] | tuple[str, ...] | None) -> list[dict[str, Any]]:
    detailed: list[dict[str, Any]] = []
    for raw in codes or []:
        code = str(raw)
        item = dict(CHECK_CATALOG.get(code, _fallback(code)))
        item["code"] = code
        overlay = item.pop("overlay", None)
        if overlay:
            item["has_overlay"] = True
        else:
            item["has_overlay"] = False
        detailed.append(item)
    return detailed


def defect_overlays(codes: list[str] | tuple[str, ...] | None) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    for raw in codes or []:
        code = str(raw)
        catalog = CHECK_CATALOG.get(code)
        if not catalog or not catalog.get("overlay"):
            continue
        overlay = dict(catalog["overlay"])
        overlay.update({
            "code": code,
            "severity": catalog.get("severity", "FAIL"),
            "label": catalog.get("title", code),
        })
        overlays.append(overlay)
    return overlays
