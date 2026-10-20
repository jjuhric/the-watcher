"""YOLO11n (ONNX) object detector run through OpenCV's DNN module. No torch needed at runtime."""
import cv2
import numpy as np

import config

COCO = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
        "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
        "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
        "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
        "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
        "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
        "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
        "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
        "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"]

SIZE = 640


class Detector:
    def __init__(self, path=config.MODEL_PATH):
        self.net = cv2.dnn.readNetFromONNX(str(path))

    def detect(self, frame):
        """Returns [{cls, conf, x, y, w, h}] with x/y/w/h normalized to the frame."""
        h, w = frame.shape[:2]
        scale = min(SIZE / w, SIZE / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        canvas = np.full((SIZE, SIZE, 3), 114, np.uint8)
        px, py = (SIZE - nw) // 2, (SIZE - nh) // 2
        canvas[py:py + nh, px:px + nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
        self.net.setInput(cv2.dnn.blobFromImage(canvas, 1 / 255.0, (SIZE, SIZE), swapRB=True))
        out = self.net.forward()[0].T                     # (8400, 4 + 80)
        scores = out[:, 4:]
        cls_ids = scores.argmax(1)
        conf = scores[np.arange(len(cls_ids)), cls_ids]
        keep = conf >= min(config.CONF_PERSON, config.CONF_OTHER)
        boxes, confs, ids = [], [], []
        for (cx, cy, bw, bh), c, k in zip(out[keep, :4], conf[keep], cls_ids[keep]):
            need = config.CONF_PERSON if k == 0 else config.CONF_OTHER
            if c < need:
                continue
            x = (cx - bw / 2 - px) / scale
            y = (cy - bh / 2 - py) / scale
            boxes.append([int(x), int(y), int(bw / scale), int(bh / scale)])
            confs.append(float(c))
            ids.append(int(k))
        res = []
        for i in np.array(cv2.dnn.NMSBoxes(boxes, confs, 0.1, 0.5)).flatten() if boxes else []:
            x, y, bw, bh = boxes[i]
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + bw), min(h, y + bh)
            if x1 <= x0 or y1 <= y0:
                continue
            res.append({"cls": COCO[ids[i]], "conf": round(confs[i], 3),
                        "x": x0 / w, "y": y0 / h, "w": (x1 - x0) / w, "h": (y1 - y0) / h})
        return res
