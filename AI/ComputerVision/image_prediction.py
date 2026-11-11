from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO
from ultralytics.utils.plotting import Annotator, colors

DEVICE = 0
PLANT_CLASSES = {"ready", "germination", "young"}

config_path = Path(__file__).resolve().with_name("config.cfg")
config = ConfigParser()
with config_path.open(encoding="utf-8-sig") as config_file:
    config.read_file(config_file)

output_dir = config_path.parent / config.get("output", "output_dir")

detector = YOLO(config_path.parent / config.get("model", "detection_path"))
classifier = YOLO(config_path.parent / config.get("model", "classifier_path"))


def predict_jpeg(jpeg: bytes):
    """Return the detection result and NPK results keyed by detection box index."""
    if not jpeg:
        raise ValueError("Empty JPEG payload")

    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Could not decode JPEG payload")

    detections = detector.predict(frame, device=DEVICE, conf=0.25, verbose=False)[0]
    crops = []
    box_indices = []
    for index, box in enumerate(detections.boxes.cpu()):
        label = detections.names[int(box.cls.item())]
        if label.casefold() not in PLANT_CLASSES:
            continue

        x1, y1, x2, y2 = box.xyxy[0].int().tolist()
        crop = frame[y1:y2, x1:x2]
        if crop.size:
            crops.append(crop)
            box_indices.append(index)

    classifications = {}
    if crops:
        results = classifier.predict(crops, device=DEVICE, verbose=False)
        for index, result in zip(box_indices, results, strict=True):
            class_id = result.probs.top1
            classifications[index] = {
                "label": result.names[class_id],
                "confidence": result.probs.top1conf.item(),
            }

    return detections, classifications


def process_image(node_id: str, jpeg: bytes) -> Path:
    """Save and return the image with detection boxes and NPK labels."""
    detections, classifications = predict_jpeg(jpeg)
    boxes = detections.boxes.cpu()
    annotator = Annotator(detections.orig_img.copy())
    for index in reversed(range(len(boxes))):
        box = boxes[index]
        class_id = int(box.cls.item())
        label = f"{detections.names[class_id]} {round(box.conf.item(), 2)}"
        classification = classifications.get(index)
        if classification is not None:
            label += f" - {classification['label']} {round(classification['confidence'], 2)}"
            print(f"Image from {node_id}, box {index}: {label}")

        annotator.box_label(box.xyxy[0].tolist(), label, color=colors(class_id, bgr=True))

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f%z")
    output_path = output_dir / f"detected-{timestamp}.jpg"
    annotated_image = annotator.result()
    footer = cv2.copyMakeBorder(
        annotated_image, 0, 36, 0, 0, cv2.BORDER_CONSTANT, value=(32, 32, 32)
    )
    cv2.putText(
        footer,
        f"Objects detected: {len(boxes)}",
        (12, annotated_image.shape[0] + 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    encoded, jpeg_data = cv2.imencode(".jpg", footer)
    if not encoded:
        raise OSError(f"Could not encode annotated image for {output_path}")
    output_path.write_bytes(jpeg_data.tobytes())
    print(
        f"Image from {node_id}: {len(boxes)} detections, "
        f"{len(classifications)} plants classified. Saved to {output_path}"
    )
    return output_path
