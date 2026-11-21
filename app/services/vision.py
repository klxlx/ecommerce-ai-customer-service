"""Pluggable photo analysis for return/exchange requests.

The MVP uses a pretrained YOLOv8n COCO detector exported to ONNX. It can only
locate common objects, so product model, colour and damage type stay empty and
must be confirmed by the customer. The adapter interface is intentionally
small so a Qwen-VL or a custom YOLO model can replace it later without changing
the API or database.
"""

from __future__ import annotations

import asyncio
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]

COCO_CLASSES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)

CLASS_ZH = {
    "backpack": "双肩包",
    "umbrella": "雨伞",
    "handbag": "手提包",
    "suitcase": "行李箱",
    "bottle": "瓶子",
    "cup": "杯子",
    "book": "书",
    "clock": "时钟",
    "vase": "花瓶",
    "scissors": "剪刀",
    "teddy bear": "毛绒玩具",
    "hair drier": "吹风机",
    "toothbrush": "牙刷",
    "laptop": "笔记本电脑",
    "mouse": "鼠标",
    "remote": "遥控器",
    "keyboard": "键盘",
    "cell phone": "手机",
    "tv": "电视",
    "microwave": "微波炉",
    "oven": "烤箱",
    "toaster": "烤面包机",
    "sink": "水槽",
    "refrigerator": "冰箱",
    "sports ball": "球类",
    "bowl": "碗",
    "wine glass": "酒杯",
}

# COCO classes that can plausibly be a sold product. People, animals and
# vehicles are excluded so a selfie or street photo cannot pass the check.
PRODUCT_CLASSES = {
    "backpack",
    "umbrella",
    "handbag",
    "suitcase",
    "bottle",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "book",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
    "sports ball",
}

PRODUCT_CLASS_HINTS: tuple[tuple[str, set[str]], ...] = (
    ("手机", {"cell phone"}),
    ("iphone", {"cell phone"}),
    ("huawei mate", {"cell phone"}),
    ("huawei pura", {"cell phone"}),
    ("redmi", {"cell phone"}),
    ("充电宝", {"cell phone"}),
    ("键盘", {"keyboard"}),
    ("鼠标", {"mouse"}),
    ("保温杯", {"cup"}),
    ("杯子", {"cup"}),
    ("水杯", {"cup"}),
    ("瓶", {"bottle"}),
    ("牙刷", {"toothbrush"}),
    ("吹风机", {"hair drier"}),
    ("剪刀", {"scissors"}),
    ("花瓶", {"vase"}),
    ("笔记本", {"laptop"}),
    ("电脑", {"laptop"}),
    ("书", {"book"}),
    ("玩具", {"teddy bear"}),
    ("背包", {"backpack"}),
    ("雨伞", {"umbrella"}),
    ("行李箱", {"suitcase"}),
)


class VisionUnavailable(Exception):
    """The configured vision model cannot be loaded."""


@dataclass
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]


@dataclass
class VisionResult:
    passed: bool
    confidence: float
    product: str = ""
    product_class: str = ""
    model: str = ""
    color: str = ""
    accessories: list[str] = field(default_factory=list)
    damage_type: str = "未知"
    detected_objects: list[dict] = field(default_factory=list)
    message: str = ""
    provider: str = "yolo_onnx"


class VisionAnalyzer(Protocol):
    async def analyze(self, image_path: Path) -> VisionResult: ...


def _resolve_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def expected_vision_classes(product_name: str) -> set[str]:
    """Map a product name to plausible COCO classes, empty when unknown."""
    if not product_name:
        return set()
    normalized = product_name.lower()
    for keyword, classes in PRODUCT_CLASS_HINTS:
        if keyword in normalized:
            return classes
    return set()


class YoloOnnxVisionAnalyzer:
    """COCO YOLOv8n ONNX detector with lazy loading and thread-safe inference."""

    def __init__(self, settings: Settings) -> None:
        self.model_path = _resolve_path(settings.yolo_model_path)
        self.confidence_threshold = settings.yolo_confidence_threshold
        self.iou_threshold = settings.yolo_iou_threshold
        self.input_size = settings.yolo_input_size
        self._net = None
        self._net_lock = threading.Lock()

    def _load_net(self):
        if self._net is not None:
            return self._net
        with self._net_lock:
            if self._net is not None:
                return self._net
            if not self.model_path.is_file():
                raise VisionUnavailable(
                    f"未找到视觉模型：{self.model_path}"
                )
            try:
                self._net = cv2.dnn.readNetFromONNX(str(self.model_path))
            except Exception as exc:  # pragma: no cover - depends on binary
                raise VisionUnavailable(
                    f"视觉模型加载失败：{type(exc).__name__}"
                ) from exc
            return self._net

    async def analyze(self, image_path: Path) -> VisionResult:
        try:
            detections = await asyncio.to_thread(
                self._detect_sync, image_path
            )
        except VisionUnavailable as exc:
            return VisionResult(
                passed=False,
                confidence=0.0,
                message=str(exc),
                provider="yolo_onnx",
            )
        except Exception:
            return VisionResult(
                passed=False,
                confidence=0.0,
                message="照片解析失败，请重新上传清晰图片",
                provider="yolo_onnx",
            )

        products = [
            detection
            for detection in detections
            if detection.label in PRODUCT_CLASSES
        ]
        if not products:
            return VisionResult(
                passed=False,
                confidence=0.0,
                detected_objects=[
                    _detection_dict(detection) for detection in detections[:8]
                ],
                message="未识别到商品主体，请拍摄商品正面清晰照片",
                provider="yolo_onnx",
            )

        top = max(products, key=lambda detection: detection.confidence)
        passed = top.confidence >= self.confidence_threshold
        return VisionResult(
            passed=passed,
            confidence=round(top.confidence, 4),
            product=CLASS_ZH.get(top.label, top.label),
            product_class=top.label,
            detected_objects=[
                _detection_dict(detection) for detection in detections[:8]
            ],
            message=(
                "已识别到商品主体，型号、颜色和破损类型请核对或手动填写"
                if passed
                else "识别置信度不足，请重拍清晰照片后再提交"
            ),
            provider="yolo_onnx",
        )

    def _detect_sync(self, image_path: Path) -> list[Detection]:
        net = self._load_net()
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError("image cannot be decoded")

        height, width = image.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        resized_w = max(1, int(round(width * scale)))
        resized_h = max(1, int(round(height * scale)))
        resized = cv2.resize(
            image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR
        )
        canvas = np.full(
            (self.input_size, self.input_size, 3), 114, dtype=np.uint8
        )
        pad_left = (self.input_size - resized_w) // 2
        pad_top = (self.input_size - resized_h) // 2
        canvas[
            pad_top : pad_top + resized_h,
            pad_left : pad_left + resized_w,
        ] = resized

        blob = cv2.dnn.blobFromImage(
            canvas,
            scalefactor=1.0 / 255.0,
            size=(self.input_size, self.input_size),
            swapRB=True,
            crop=False,
        )
        net.setInput(blob)
        output = np.asarray(net.forward())
        if output.ndim == 3:
            output = output[0]
        if output.ndim != 2:
            raise ValueError("unexpected YOLO output shape")
        # Exporters emit either (84, 8400) or (8400, 84).
        if output.shape[0] < output.shape[1]:
            output = output.T

        boxes: list[tuple[float, float, float, float]] = []
        scores: list[float] = []
        class_ids: list[int] = []
        for row in output:
            if row.size < 5:
                continue
            class_scores = row[4:]
            class_id = int(np.argmax(class_scores))
            confidence = float(class_scores[class_id])
            if not math.isfinite(confidence):
                continue
            if confidence < self.confidence_threshold:
                continue
            cx, cy, box_w, box_h = (
                float(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
            )
            x1 = (cx - box_w / 2 - pad_left) / scale
            y1 = (cy - box_h / 2 - pad_top) / scale
            x2 = (cx + box_w / 2 - pad_left) / scale
            y2 = (cy + box_h / 2 - pad_top) / scale
            x1 = max(0.0, min(float(width), x1))
            y1 = max(0.0, min(float(height), y1))
            x2 = max(0.0, min(float(width), x2))
            y2 = max(0.0, min(float(height), y2))
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append((x1, y1, x2, y2))
            scores.append(confidence)
            class_ids.append(class_id)

        kept = _nms(boxes, scores, self.iou_threshold)
        detections: list[Detection] = []
        for index in kept[:20]:
            label = (
                COCO_CLASSES[class_ids[index]]
                if class_ids[index] < len(COCO_CLASSES)
                else str(class_ids[index])
            )
            detections.append(
                Detection(
                    label=label,
                    confidence=scores[index],
                    box=boxes[index],
                )
            )
        return detections


def _nms(
    boxes: list[tuple[float, float, float, float]],
    scores: list[float],
    iou_threshold: float,
) -> list[int]:
    if not boxes:
        return []
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    keep: list[int] = []
    while order:
        current = order.pop(0)
        keep.append(current)
        remaining: list[int] = []
        for candidate in order:
            if _iou(boxes[current], boxes[candidate]) < iou_threshold:
                remaining.append(candidate)
        order = remaining
    return keep


def _iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _detection_dict(detection: Detection) -> dict:
    return {
        "label": detection.label,
        "label_zh": CLASS_ZH.get(detection.label, detection.label),
        "confidence": round(detection.confidence, 4),
        "box": [round(value, 2) for value in detection.box],
    }


def build_vision_analyzer(settings: Settings) -> VisionAnalyzer:
    provider = (settings.vision_provider or "yolo").strip().lower()
    if provider in {"yolo", "yolo_onnx", "onnx"}:
        return YoloOnnxVisionAnalyzer(settings)
    raise VisionUnavailable(f"不支持的视觉提供方：{settings.vision_provider}")
