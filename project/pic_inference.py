import time

try:
    from hobot_dnn import pyeasy_dnn as dnn
except ImportError:
    dnn = None

import numpy as np
import cv2
import random
from pathlib import Path

random.seed(0)

CLASSES = ("Can", "Glass-Drink", "paper", "pet bottle")

COLORS = {
    cls: [random.randint(0, 255) for _ in range(3)] for i, cls in enumerate(CLASSES)
}


def letterbox(im, new_shape=(640, 640), color=114):
    # Resize and pad image while meeting stride-multiple constraints
    shape = im.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])

    # Compute padding
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding

    dw /= 2  # divide padding into 2 sides
    dh /= 2

    if shape[::-1] != new_unpad:  # resize
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(
        im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(color, color, color)
    )  # add border
    return im, 1 / r, (dw, dh)


def ratioresize(im, new_shape=(640, 640), color=114):
    shape = im.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)
    new_h, new_w = new_shape
    padded_img = np.ones((new_h, new_w, 3), dtype=np.uint8) * color

    # Scale ratio (new / old)
    r = min(new_h / shape[0], new_w / shape[1])

    # Compute padding
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))

    if shape[::-1] != new_unpad:
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)

    padded_img[: new_unpad[1], : new_unpad[0]] = im
    padded_img = np.ascontiguousarray(padded_img)
    return padded_img, 1 / r, (0, 0)


def get_hw(pro):
    if pro.layout == "NCHW":
        return pro.shape[2], pro.shape[3]
    else:
        return pro.shape[1], pro.shape[2]


def blob(im):
    im = im.transpose(2, 0, 1)
    im = im[np.newaxis, ...]
    im = np.ascontiguousarray(im).astype(np.float32)
    return im / 255.0


def softmax(x, axis=-1):
    e_x = np.exp(x - np.max(x, axis=axis, keepdims=True))
    y = e_x / e_x.sum(axis=axis, keepdims=True)
    return y


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def bgr2nv12_opencv(image):
    height, width = image.shape[:2]
    area = height * width
    yuv420p = cv2.cvtColor(image, cv2.COLOR_BGR2YUV_I420).reshape((area * 3 // 2,))
    y = yuv420p[:area]
    uv_planar = yuv420p[area:].reshape((2, area // 4))
    uv_packed = uv_planar.transpose((1, 0)).reshape((area // 2,))

    nv12 = np.zeros_like(yuv420p)
    nv12[:area] = y
    nv12[area:] = uv_packed
    return nv12


def postprocess(
        output,
        score_thres, iou_thres,
        orin_h, orin_w,
        dh, dw, ratio_h, ratio_w,
        reg_max,
        num_classes
):
    dfl = np.arange(0, reg_max, dtype=np.float32)
    confidences = []
    boxes = []
    classIds = []
    for i in range(len(output) // 2):
        bboxes_feat = output[i * 2 + 0]
        scores_feat = sigmoid(output[i * 2 + 1])
        Argmax = scores_feat.argmax(-1)
        Max = scores_feat.max(-1)
        indices = np.where(Max > score_thres)
        hIdx, wIdx = indices
        num_proposal = hIdx.size
        if not num_proposal:
            continue
        assert scores_feat.shape[-1] == num_classes
        scores = Max[hIdx, wIdx]
        bboxes = bboxes_feat[hIdx, wIdx].reshape(-1, 4, reg_max)
        bboxes = softmax(bboxes, -1) @ dfl
        argmax = Argmax[hIdx, wIdx]

        for k in range(num_proposal):
            x0, y0, x1, y1 = bboxes[k]
            score = scores[k]
            clsid = argmax[k]
            h, w, stride = hIdx[k], wIdx[k], 1 << (i + 3)
            x0 = ((w + 0.5 - x0) * stride - dw) * ratio_w
            y0 = ((h + 0.5 - y0) * stride - dh) * ratio_h
            x1 = ((w + 0.5 + x1) * stride - dw) * ratio_w
            y1 = ((h + 0.5 + y1) * stride - dh) * ratio_h
            # clip
            x0 = min(max(x0, 0), orin_w)
            y0 = min(max(y0, 0), orin_h)
            x1 = min(max(x1, 0), orin_w)
            y1 = min(max(y1, 0), orin_h)
            confidences.append(float(score))
            boxes.append(np.array([x0, y0, x1 - x0, y1 - y0], dtype=np.float32))
            classIds.append(clsid)
    return boxes, confidences, classIds


def nms(boxes, confidences, classIds):
    indices = cv2.dnn.NMSBoxes(boxes, confidences, score_thres, iou_thres).flatten()
    results = []
    for i in indices:
        boxes[i][2:] += boxes[i][:2]
        res = np.array(
            [*boxes[i].round(), confidences[i], classIds[i]], dtype=np.float32
        )
        results.append(res)
    return results


def print_properties(pro):
    print("tensor type:", pro.tensor_type)
    print("data type:", pro.dtype)
    print("layout:", pro.layout)
    print("shape:", pro.shape)
    return get_hw(pro)


if __name__ == "__main__":
    images_path = Path("./images")
    model_path = Path("./yolov8n_horizon.bin")

    score_thres = 0.4
    iou_thres = 0.65
    num_classes = 4

    try:
        models = dnn.load(str(model_path))
        model_h, model_w = print_properties(models[0].inputs[0].properties)
    except Exception as e:
        print(f"Load model error.\n{e}")
        exit()
    else:
        try:
            for _ in range(10):
                models[0].forward(
                    np.random.randint(
                        0, 255, (int(model_h * model_w * 1.5),), dtype=np.uint8
                    )
                )
        except Exception as e:
            print(f"Warm up model error.\n{e}")

    cv2.namedWindow("results", cv2.WINDOW_AUTOSIZE)
    for img_path in images_path.iterdir():
        image = cv2.imread(str(img_path))
        t0 = time.perf_counter()
        ## yolov8 training letterbox
        # resized, ratio, (dw, dh) = letterbox(image, (model_h, model_w))
        resized, ratio, (dw, dh) = ratioresize(image, (model_h, model_w))
        nv12 = bgr2nv12_opencv(resized)
        t1 = time.perf_counter()
        outputs = models[0].forward(nv12)
        outputs = [o.buffer[0] for o in outputs]
        t2 = time.perf_counter()
        results = postprocess(
            outputs,
            score_thres,
            iou_thres,
            image.shape[0],
            image.shape[1],
            dh,
            dw,
            ratio,
            ratio,
            16,
            num_classes,
        )
        results = nms(*results)
        t3 = time.perf_counter()
        for x0, y0, x1, y1, score, label in results:
            x0, y0, x1, y1 = map(int, [x0, y0, x1, y1])
            cls_id = int(label)
            cls = CLASSES[cls_id]
            color = COLORS[cls]
            cv2.rectangle(image, [x0, y0], [x1, y1], color, 1)
            cv2.putText(
                image,
                f"{cls}:{score:.3f}",
                (x0, y0 - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.325,
                [0, 0, 225],
                thickness=1,
            )
        t4 = time.perf_counter()
        cv2.imshow("results", image)
        print(
            f"TimeConsuming:\n"
            f"Preprocess: {(t1 - t0) * 1000} ms\n"
            f"Inference: {(t2 - t1) * 1000} ms\n"
            f"Postprocess: {(t3 - t2) * 1000} ms\n"
            f"Drawing: {(t4 - t3) * 1000} ms\n"
            f"End2END: {(t4 - t0) * 1000} ms"
        )
        key = cv2.waitKey(0)
        if key & 0xFF == ord("q"):
            break

