"""Unit tests for the pluggable vision adapter."""

from __future__ import annotations

from app.services.vision import (
    COCO_CLASSES,
    Detection,
    expected_vision_classes,
    _iou,
    _nms,
)


def test_coco_classes_are_complete():
    assert len(COCO_CLASSES) == 80
    assert COCO_CLASSES[0] == "person"
    assert COCO_CLASSES[-1] == "toothbrush"


def test_expected_vision_classes_maps_phone_and_keyboard():
    assert expected_vision_classes("HUAWEI Mate 90") == {"cell phone"}
    assert expected_vision_classes("静音机械键盘 87 键") == {"keyboard"}
    assert expected_vision_classes("声动 Pro 无线蓝牙耳机") == set()


def test_nms_keeps_highest_confidence_overlap():
    boxes = [
        (0.0, 0.0, 100.0, 100.0),
        (5.0, 5.0, 105.0, 105.0),
        (300.0, 300.0, 400.0, 400.0),
    ]
    scores = [0.9, 0.8, 0.7]
    kept = _nms(boxes, scores, 0.45)
    assert kept == [0, 2]
    assert _iou(boxes[0], boxes[1]) > 0.45


def test_detection_dataclass_keeps_box():
    detection = Detection(
        label="cell phone",
        confidence=0.75,
        box=(1.0, 2.0, 3.0, 4.0),
    )
    assert detection.label == "cell phone"
