"""Vidlamp Zoom Crop / Zoom Paste (11/11/2026).

Detection and matting models work on a fixed canvas (WeVisOne-VL: 1024x1024 for the whole picture), so a small object
or a big photo gets soft, blocky edges. Zoom Crop cuts out the area of the boxes found by a first pass (plus a margin),
the model runs again on that area, and Zoom Paste puts the mask back on a canvas as big as the original picture.

Boxes: the core BOUNDING_BOX type ({"x", "y", "width", "height"} in pixels, one list per frame). Every box of every
frame is joined in one area, so the crops of a batch have the same size. No box: the whole picture.
"""
import torch


def _boxes(bboxes):
    if isinstance(bboxes, dict):
        return [bboxes]
    out = []
    for item in bboxes or []:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, (list, tuple)):
            out.extend(b for b in item if isinstance(b, dict))
    return out


class VidlampZoomCrop:
    CATEGORY = "Vidlamp/image"
    DESCRIPTION = ("Crops the picture to the area of the boxes (plus a margin), at full resolution, so a second pass of "
                   "a detection or matting model sees the subject big. Put the mask back with Vidlamp Zoom Paste.")

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "bboxes": ("BOUNDING_BOX", {"forceInput": True}),
            "margin": ("FLOAT", {"default": 0.10, "min": 0.0, "max": 1.0, "step": 0.01,
                                 "tooltip": "Extra space around the boxes, as a share of their size on each side."}),
        }}

    RETURN_TYPES = ("IMAGE", "VIDLAMP_ZOOM")
    RETURN_NAMES = ("crop", "zoom")
    FUNCTION = "crop"

    def crop(self, image, bboxes, margin):
        _, h, w, _ = image.shape
        boxes = [b for b in _boxes(bboxes) if float(b.get("width", 0)) > 0 and float(b.get("height", 0)) > 0]
        if not boxes:
            x1, y1, x2, y2 = 0, 0, w, h
        else:
            bx1 = min(float(b["x"]) for b in boxes)
            by1 = min(float(b["y"]) for b in boxes)
            bx2 = max(float(b["x"]) + float(b["width"]) for b in boxes)
            by2 = max(float(b["y"]) + float(b["height"]) for b in boxes)
            mx, my = (bx2 - bx1) * margin, (by2 - by1) * margin
            x1, y1 = max(0, int(bx1 - mx)), max(0, int(by1 - my))
            x2, y2 = min(w, int(round(bx2 + mx))), min(h, int(round(by2 + my)))
            if x2 - x1 < 8 or y2 - y1 < 8:
                x1, y1, x2, y2 = 0, 0, w, h
        zoom = {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1, "canvas_width": w, "canvas_height": h}
        return (image[:, y1:y2, x1:x2, :].clone(), zoom)


class VidlampZoomPaste:
    CATEGORY = "Vidlamp/image"
    DESCRIPTION = "Puts a mask made on the crop of Vidlamp Zoom Crop back on a canvas as big as the original picture."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"mask": ("MASK",), "zoom": ("VIDLAMP_ZOOM",)}}

    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "paste"

    def paste(self, mask, zoom):
        if mask.dim() == 2:
            mask = mask.unsqueeze(0)
        x, y, cw, ch = zoom["x"], zoom["y"], zoom["width"], zoom["height"]
        if tuple(mask.shape[-2:]) != (ch, cw):
            mask = torch.nn.functional.interpolate(mask.unsqueeze(1).float(), size=(ch, cw), mode="bilinear",
                                                   align_corners=False).squeeze(1)
        out = torch.zeros((mask.shape[0], zoom["canvas_height"], zoom["canvas_width"]), dtype=mask.dtype,
                          device=mask.device)
        out[:, y:y + ch, x:x + cw] = mask
        return (out,)


NODE_CLASS_MAPPINGS = {"VidlampZoomCrop": VidlampZoomCrop, "VidlampZoomPaste": VidlampZoomPaste}
NODE_DISPLAY_NAME_MAPPINGS = {"VidlampZoomCrop": "Vidlamp Zoom Crop", "VidlampZoomPaste": "Vidlamp Zoom Paste"}
