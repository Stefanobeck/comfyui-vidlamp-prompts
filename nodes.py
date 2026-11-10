"""Vidlamp Prompt Series and Vidlamp Prompt Sheet.

Prompt Series writes N prompts for a target model (from a few words or from presets) and outputs them as a LIST:
ComfyUI then runs the nodes after it once per prompt (one image or video per prompt).
Prompt Sheet collects the results and the prompts into one contact sheet (PNG) with each prompt under its picture,
plus an Excel file (.xlsx) with every picture next to its full prompt, and a CSV with the same columns as Vidlamp's MY_PROMPTS/prompts.csv (category, title, prompt, models).
"""
import asyncio
import csv
import io
import json
import os
import textwrap
import time
import zipfile
from xml.sax.saxutils import escape as xml_escape

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

import comfy.model_management as mm
import folder_paths

from . import series, servers

LOG = "[Vidlamp Prompt Series]"
VIDLAMP_IDEA = ('A vintage brass magic lamp on a dark wooden table, warm cinematic light, soft smoke rising from its spout, a small elegant brass plate in front of it engraved with the word "VIDLAMP", shallow depth of field, photorealistic.')


# ------------------------------------------------------------------ text engines
def _comfy_engine(clip, max_length, temperature):
    def generate(system, message, seed):
        # skip_template=False: the tokenizers skip the chat template by default, and without it the model only
        # continues the text (first test with Gemma 4 E4B: checklists and ", high contrast." instead of a prompt)
        try:
            tokens = clip.tokenize(message, min_length=1, skip_template=False, thinking=False, system_prompt=system)
        except TypeError:
            # older ComfyUI: no system prompt in the chat template, the rules go before the request
            tokens = clip.tokenize(f"{system}\n\n{message}", min_length=1, skip_template=False)
        ids = clip.generate(tokens, do_sample=True, max_length=max_length, temperature=temperature, top_k=64,
                            top_p=0.95, min_p=0.05, repetition_penalty=1.05, seed=seed)
        text = clip.decode(ids)
        _, sep, after = text.partition("</think>")
        return after if sep else text
    return generate


def _open_output_folder(prefix, subfolder, filename, remote):
    """Opens the folder of the results (and selects the file) with the system file manager."""
    import subprocess
    import sys
    out = os.path.realpath(folder_paths.get_output_directory())
    folder = os.path.realpath(os.path.join(out, subfolder or os.path.dirname(str(prefix or "")) or "vidlamp_prompts"))
    if os.path.commonpath([out, folder]) != out:          # never outside the output folder
        folder = out
    target = os.path.join(folder, os.path.basename(filename)) if filename else ""
    if not os.path.isdir(folder):
        return {"opened": False, "path": folder, "error": "The folder does not exist yet: run the workflow first."}
    if remote not in ("127.0.0.1", "::1", "localhost"):
        return {"opened": False, "path": folder, "error": "ComfyUI runs on another computer: the folder is there."}
    try:
        if sys.platform.startswith("win"):
            if target and os.path.isfile(target):
                subprocess.Popen(["explorer", "/select,", target])
            else:
                os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target] if target and os.path.isfile(target) else ["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        return {"opened": False, "path": folder, "error": str(e)}
    return {"opened": True, "path": folder}


def _register_route():
    """GET /vidlamp_prompts/models?engine=Ollama&url=...: the browser fills the model menu when the engine changes."""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:           # outside ComfyUI (tests)
        return

    @PromptServer.instance.routes.get("/vidlamp_prompts/models")
    async def models(request):
        engine = request.query.get("engine", "")
        if engine not in servers.DEFAULT_URL:
            return web.json_response({"models": [], "error": None})
        names, error = await asyncio.get_running_loop().run_in_executor(
            None, servers.list_models, engine, request.query.get("url", ""))
        return web.json_response({"models": names, "error": error, "url": servers.url_for(engine, request.query.get("url", ""))})

    @PromptServer.instance.routes.get("/vidlamp_prompts/system")
    async def system(request):
        """The system prompt of a target, shown in the node when the target changes."""
        target = request.query.get("target", "")
        if target not in series.guides():
            return web.json_response({"system": "", "error": f"unknown target '{target}'"}, status=404)
        if request.query.get("kind") == "rewrite":
            return web.json_response({"system": series.rewrite_system_for(target)})
        return web.json_response({"system": series.system_for(target)})

    @PromptServer.instance.routes.post("/vidlamp_prompts/open_folder")
    async def open_folder(request):
        """The button of Prompt Sheet / Sheet Compare: opens the output folder on the computer where ComfyUI runs
        (10/11/2026, the user). Only folders inside ComfyUI's output folder; with the last file selected when known.
        When ComfyUI is not on this computer (remote browser) nothing opens: the path comes back to show."""
        data = await request.json()
        return web.json_response(_open_output_folder(data.get("prefix", ""), data.get("subfolder", ""),
                                                     data.get("filename", ""), request.remote))


_register_route()


# ------------------------------------------------------------------ Prompt Series
class VidlampPromptSeries:
    CATEGORY = "Vidlamp/prompts"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING", "INT", "VIDLAMP_TEXT_ENGINE")
    RETURN_NAMES = ("prompts", "all_prompts", "count", "text_engine")
    OUTPUT_IS_LIST = (True, False, False, False)
    OUTPUT_TOOLTIPS = (
        "One prompt per item: the nodes after it run once per prompt.",
        "All the prompts in one text, numbered (to read them or save them).",
        "How many prompts were written.",
        "The text model chosen here, for the Prompt Rewrite nodes (one prompt written for each model).",
    )
    DESCRIPTION = ("Writes a series of prompts for one model from a few words or from presets, to try them all in one "
                   "run. Connect 'prompts' to your text encoder: ComfyUI makes one picture per prompt.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # default: the Vidlamp lamp (10/11/2026, the user: "not a cat chef, the Vidlamp lamp")
                "idea": ("STRING", {"multiline": True, "default": VIDLAMP_IDEA,
                                    "tooltip": "A few words in any language (e.g. 'a cat chef'). Empty = the presets "
                                               "choose the subject from the category."}),
                "target": (series.targets(), {"tooltip": "The model that will use the prompts: they are written "
                                                         "with its own rules."}),
                "category": (series.category_names(), {"tooltip": "Used when the idea is empty. 'Characters' "
                                                                  "makes random people and creatures."}),
                "style": (series.style_names(), {"tooltip": "The look of every prompt. 'Random' picks a different "
                                                            "style for each one."}),
                "count": ("INT", {"default": 6, "min": 1, "max": 64}),
                "mode": (series.MODES, {"tooltip": "Explore: a different scene each time. Variations: the first "
                                                   "scene again, with new pose, light, angle..."}),
                "engine": (series.ENGINES, {"tooltip": "Presets (no AI): instant, from the ready prompts. "
                                                       "ComfyUI text model: connect a text encoder (Gemma, Qwen3-VL) "
                                                       "to 'clip', nothing to install. Ollama / LM Studio: needs the program running, its models appear in "
                                                       "'server_model'."}),
                "frame": (series.FRAMES, {"tooltip": "The frame of your pictures: the scene is composed for it."}),
                "strange_mix": ("BOOLEAN", {"default": False, "tooltip": "Absurd but beautiful combinations of "
                                                                         "place, weather and action."}),
                "seed": ("INT", {"default": 42, "min": 0, "max": 0xffffffffffffffff,
                                 "tooltip": "Same seed and settings = same series."}),
            },
            "optional": {
                # lazy (10/11/2026): ComfyUI loads the text model only when the engine is "ComfyUI text model"; with the
                # other engines the Load CLIP node is not run (before, Gemma's 9 GB were loaded for nothing)
                "clip": ("CLIP", {"lazy": True,
                                  "tooltip": "Only for the 'ComfyUI text model' engine: a CLIP Loader with a text "
                                             "model that can write (Gemma 3/4, Qwen3-VL, Qwen3.5)."}),
                "trigger_words": ("STRING", {"default": "", "tooltip": "LoRA trigger words, put at the start of "
                                                                       "every prompt."}),
                "server_model": ([servers.AUTO], {"tooltip": "Ollama / LM Studio: pick the engine and this menu shows the "
                                                             "models installed there (only those that write text)."}),
                "server_url": ("STRING", {"default": "", "tooltip": "Ollama / LM Studio address. Empty = the usual one "
                                                                   "(Ollama 127.0.0.1:11434, LM Studio 127.0.0.1:1234)."}),
                "system_prompt": ("STRING", {"multiline": True, "default": "",
                                             "tooltip": "Text model engines only. The instructions the text model gets: "
                                                        "filled with the rules of the target model every time you "
                                                        "change target. Change them as you like; empty = the rules of "
                                                        "the target."}),
                "temperature": ("FLOAT", {"default": 1.0, "min": 0.1, "max": 2.0, "step": 0.05,
                                          "tooltip": "Text model only: higher = more varied ideas."}),
                "max_length": ("INT", {"default": 700, "min": 64, "max": 4096,
                                       "tooltip": "ComfyUI text model only: the longest answer in tokens."}),
                # last on purpose: saved workflows keep their widget values in order (10/11/2026, the user: "let the
                # user switch the prompt generator off and test what he writes on every model")
                "use_as_written": ("BOOLEAN", {"default": False,
                                               "tooltip": "On: what you write in 'idea' IS the prompt, word for word "
                                                          "(no text model). Several prompts: leave an empty line "
                                                          "between them. The Prompt Rewrite nodes still adapt them "
                                                          "to each model if their mode is on."}),
            },
        }

    def check_lazy_status(self, engine, clip=None, **kwargs):
        return ["clip"] if engine == "ComfyUI text model" and clip is None else []

    @classmethod
    def VALIDATE_INPUTS(cls, server_model):
        # the menu is filled by the browser from Ollama / LM Studio: ComfyUI must not check it against the fixed list
        # (the node checks the model when it runs, with a clear message)
        return True

    def run(self, idea, target, category, style, count, mode, engine, frame, strange_mix, seed, clip=None,
            trigger_words="", server_model=servers.AUTO, server_url="", system_prompt="", temperature=1.0, max_length=700,
            use_as_written=False):
        plan = series.Plan(idea, target, category, style, count, mode, strange_mix, seed, trigger_words, frame)
        start = time.time()
        if use_as_written:
            prompts = series.as_written(idea, trigger_words)
            print(f"{LOG} {len(prompts)} prompts used as written (no text model)")
        elif engine == "Presets (no AI)":
            prompts = series.presets_series(plan, log=lambda t: print(f"{LOG} {t}"))
        elif engine == "ComfyUI text model":
            if clip is None:
                raise ValueError("The 'ComfyUI text model' engine needs a text model: connect a Load CLIP node with "
                                 "Gemma or Qwen3-VL to the 'clip' input (or switch its group on), or pick another "
                                 "engine: 'Presets (no AI)', Ollama or LM Studio.")
            prompts = series.ai_series(plan, _comfy_engine(clip, max_length, temperature), system=system_prompt)
        elif engine in servers.DEFAULT_URL:
            text_engine = servers.Engine(engine, server_model, server_url, temperature)
            # the program and ComfyUI share the graphics card: free ComfyUI's models first, unload the text model at
            # the end (the rule of Vidlamp: never both on the card)
            mm.unload_all_models()
            mm.soft_empty_cache()
            try:
                prompts = series.ai_series(plan, text_engine.generate, system=system_prompt)
            finally:
                text_engine.unload()
        else:
            raise ValueError(f"Unknown engine '{engine}'.")
        print(f"{LOG} {len(prompts)} prompts for {target} in {time.time() - start:.1f} s ({engine})")
        numbered = "\n\n".join(f"{i + 1}. {p}" for i, p in enumerate(prompts))
        text_engine = {"engine": engine, "clip": clip, "server_model": server_model, "server_url": server_url,
                       "temperature": temperature, "max_length": max_length, "frame": frame, "seed": seed}
        return {"ui": {"text": [numbered]}, "result": (prompts, numbered, len(prompts), text_engine)}


# ------------------------------------------------------------------ Prompt Rewrite
class VidlampPromptRewrite:
    """The prompts of the Sheet Creator rewritten with the rules of another model, same scene (10/11/2026, the user:
    in the model comparison, a prompt written for each model). 'enabled' off = the prompts pass unchanged: in the
    comparison workflow one Boolean switches all the Rewrite nodes at once."""
    CATEGORY = "Vidlamp/prompts"
    FUNCTION = "run"
    INPUT_IS_LIST = True
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompts",)
    OUTPUT_IS_LIST = (True,)
    DESCRIPTION = ("Rewrites the prompts of the Sheet Creator for another model, keeping the same scenes. Uses the text "
                   "model of the Sheet Creator (connect its 'text_engine' output).")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompts": ("STRING", {"forceInput": True, "tooltip": "The 'prompts' output of the Sheet Creator."}),
                "text_engine": ("VIDLAMP_TEXT_ENGINE", {"tooltip": "The 'text_engine' output of the Sheet Creator."}),
                "target": (series.targets(), {"tooltip": "The model these prompts are for."}),
                "enabled": ("BOOLEAN", {"default": True, "tooltip": "Off = the prompts pass unchanged (the same "
                                                                    "prompt for every model)."}),
            },
            "optional": {
                "system_prompt": ("STRING", {"multiline": True, "default": "",
                                             "tooltip": "The instructions for rewriting: filled with the rules of the "
                                                        "target every time you change target. Empty = the rules of "
                                                        "the target."}),
            },
        }

    def run(self, prompts, text_engine, target, enabled, system_prompt=None):
        target, enabled = target[0], enabled[0]
        cfg = text_engine[0]
        system = (system_prompt or [""])[0]
        prompts = [str(p) for p in prompts]
        if not enabled:
            return (prompts,)
        engine = cfg["engine"]
        start = time.time()
        log = lambda m: print(m)  # noqa: E731
        if engine == "ComfyUI text model":
            if cfg.get("clip") is None:
                raise ValueError("Prompt Rewrite: the Sheet Creator has no text model connected to 'clip'.")
            gen = _comfy_engine(cfg["clip"], cfg["max_length"], cfg["temperature"])
            out = series.rewrite_series(prompts, target, gen, cfg["frame"], cfg["seed"], system, log)
        elif engine in servers.DEFAULT_URL:
            text_engine = servers.Engine(engine, cfg["server_model"], cfg["server_url"], cfg["temperature"])
            mm.unload_all_models()
            mm.soft_empty_cache()
            try:
                out = series.rewrite_series(prompts, target, text_engine.generate, cfg["frame"], cfg["seed"], system,
                                            log)
            finally:
                text_engine.unload()
        else:
            print(f"[Vidlamp Prompt Rewrite] {target}: the Sheet Creator uses '{engine}', no text model to rewrite "
                  f"with: the prompts pass unchanged")
            return (prompts,)
        print(f"[Vidlamp Prompt Rewrite] {len(out)} prompts for {target} in {time.time() - start:.1f} s ({engine})")
        return (out,)


# ------------------------------------------------------------------ Prompt Sheet
def _font(size):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _to_pil(t):
    return Image.fromarray(np.clip(t.cpu().numpy() * 255.0, 0, 255).astype(np.uint8))


def _xml(text):
    """Text for a cell: XML escaped, without the control characters Excel refuses."""
    text = "".join(c for c in str(text) if c in "\t\n\r" or ord(c) >= 32)
    return xml_escape(text)


def _col(n):
    """Excel column letters: 0 -> A, 1 -> B..."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _save_xlsx(path, pictures, prompts, thumb_width=384):
    """An Excel file with one row per prompt: number, the picture, the full prompt (10/11/2026, the user: "put the
    picture in the sheet too, maybe an xlsx"). Written by hand (an xlsx is a zip of XML files) so the node needs no
    extra library: ComfyUI portable has no openpyxl."""
    _save_xlsx_grid(path, prompts, [("Picture", pictures)], thumb_width)


def _save_xlsx_grid(path, prompts, columns, thumb_width=384, texts=None, links=None):
    """One row per prompt: the number, one picture per column (columns = [(label, pictures)], e.g. one per model),
    then the full prompt, then optional text columns (texts = [(label, strings)]: the prompt each model received),
    then optional file columns (links = [(label, file names)]: the picture files, clickable, in the same folder as
    the Excel file; 10/11/2026, the user: "in the Excel I want the path and name of every picture")."""
    texts = texts or []
    links = links or []
    hyper = []                                        # (cell, file name)
    first_link = len(columns) + 2 + len(texts)

    def link_cells(i, r):
        cells = ""
        for j, (_, names) in enumerate(links):
            name = names[i] if i < len(names) else ""
            if not name:
                continue
            ref = f"{_col(first_link + j)}{r}"
            hyper.append((ref, name))
            cells += (f'<c r="{ref}" t="inlineStr" s="3"><is><t xml:space="preserve">{_xml(name)}</t></is></c>')
        return cells

    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    emu = 9525                                        # EMU per pixel
    rows, anchors, rels, media = [], [], [], []
    n_rows = max([len(prompts)] + [len(p) for _, p in columns])
    prompt_col = _col(len(columns) + 1)
    head = '<c r="A1" t="inlineStr" s="2"><is><t>#</t></is></c>'
    for c, (label, _) in enumerate(columns):
        head += f'<c r="{_col(c + 1)}1" t="inlineStr" s="2"><is><t>{_xml(label)}</t></is></c>'
    head += f'<c r="{prompt_col}1" t="inlineStr" s="2"><is><t>Prompt</t></is></c>'
    for j, (label, _) in enumerate(texts):
        head += f'<c r="{_col(len(columns) + 2 + j)}1" t="inlineStr" s="2"><is><t>{_xml(label)}</t></is></c>'
    for j, (label, _) in enumerate(links):
        head += f'<c r="{_col(first_link + j)}1" t="inlineStr" s="2"><is><t>{_xml(label)}</t></is></c>'
    rows.append(f'<row r="1" ht="20" customHeight="1">{head}</row>')
    for i in range(n_rows):
        r = i + 2
        tallest = 20
        for c, (_, pics) in enumerate(columns):
            if i >= len(pics) or pics[i] is None:
                continue
            pic = pics[i]
            h = max(1, round(pic.height * thumb_width / pic.width))
            tallest = max(tallest, h)
            thumb = pic.convert("RGB").resize((thumb_width, h), Image.LANCZOS)
            png = io.BytesIO()
            thumb.save(png, "PNG", compress_level=6)
            media.append(png.getvalue())
            k = len(media)
            rels.append(f'<Relationship Id="rId{k}" Type="{ns_r}/image" Target="../media/image{k}.png"/>')
            anchors.append(
                f'<xdr:oneCellAnchor><xdr:from><xdr:col>{c + 1}</xdr:col><xdr:colOff>{4 * emu}</xdr:colOff>'
                f'<xdr:row>{r - 1}</xdr:row><xdr:rowOff>{4 * emu}</xdr:rowOff></xdr:from><xdr:ext '
                f'cx="{thumb_width * emu}" cy="{h * emu}"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{k + 1}" '
                f'name="Picture {k}"/><xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
                f'<xdr:blipFill><a:blip r:embed="rId{k}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
                f'<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{thumb_width * emu}" cy="{h * emu}"/>'
                f'</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic><xdr:clientData/>'
                f'</xdr:oneCellAnchor>')
        prompt = prompts[i] if i < len(prompts) else ""
        rows.append(f'<row r="{r}" ht="{tallest * 0.75 + 8:.1f}" customHeight="1">'
                    f'<c r="A{r}" s="1"><v>{i + 1}</v></c>'
                    f'<c r="{prompt_col}{r}" t="inlineStr" s="1"><is><t xml:space="preserve">{_xml(prompt)}</t>'
                    f'</is></c>' + "".join(
                        f'<c r="{_col(len(columns) + 2 + j)}{r}" t="inlineStr" s="1"><is><t xml:space="preserve">'
                        f'{_xml(vals[i] if i < len(vals) else "")}</t></is></c>' for j, (_, vals) in enumerate(texts))
                    + link_cells(i, r) + '</row>')
    picture_col = thumb_width / 7 + 2                 # column width in characters
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/drawings/drawing1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>',
        "_rels/.rels":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{ns_pkg}">'
            f'<Relationship Id="rId1" Type="{ns_r}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{ns_main}" xmlns:r="{ns_r}">'
            f'<sheets><sheet name="Prompts" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{ns_pkg}">'
            f'<Relationship Id="rId1" Type="{ns_r}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{ns_r}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{ns_main}">'
            '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font>'
            '<font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
            '<alignment vertical="top" wrapText="1"/></xf>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
            '<alignment vertical="top"/></xf></cellXfs>'
            '</styleSheet>',
        "xl/worksheets/sheet1.xml":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{ns_main}" xmlns:r="{ns_r}">'
            f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            f'state="frozen"/></sheetView></sheetViews>'
            f'<cols><col min="1" max="1" width="5" customWidth="1"/>'
            f'<col min="2" max="{len(columns) + 1}" width="{picture_col:.1f}" customWidth="1"/>'
            f'<col min="{len(columns) + 2}" max="{len(columns) + 2 + len(texts)}" '
            f'width="{110 if len(columns) == 1 else 70}" customWidth="1"/>'
            + (f'<col min="{first_link}" max="{first_link + len(links) - 1}" width="42" customWidth="1"/>'
               if links else '') + '</cols>'
            f'<sheetData>{"".join(rows)}</sheetData>'
            + (('<hyperlinks>' + "".join(f'<hyperlink ref="{ref}" r:id="rIdL{k}"/>' for k, (ref, _) in enumerate(hyper))
                + '</hyperlinks>') if hyper else '') + '<drawing r:id="rId1"/></worksheet>',
        "xl/worksheets/_rels/sheet1.xml.rels":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{ns_pkg}">'
            f'<Relationship Id="rId1" Type="{ns_r}/drawing" Target="../drawings/drawing1.xml"/>'
            + "".join(f'<Relationship Id="rIdL{k}" Type="{ns_r}/hyperlink" Target="{_xml(name)}" TargetMode="External"/>'
                      for k, (_, name) in enumerate(hyper)) + '</Relationships>',
        "xl/drawings/drawing1.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="{ns_r}">'
            f'{"".join(anchors)}</xdr:wsDr>',
        "xl/drawings/_rels/drawing1.xml.rels":
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{ns_pkg}">'
            f'{"".join(rels)}</Relationships>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"))
        for i, data in enumerate(media):
            z.writestr(f"xl/media/image{i + 1}.png", data)


class VidlampPromptSheet:
    CATEGORY = "Vidlamp/prompts"
    FUNCTION = "run"
    INPUT_IS_LIST = True
    OUTPUT_NODE = True
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("sheet",)
    DESCRIPTION = ("Puts the pictures of a prompt series side by side, with each prompt written under its picture, and "
                   "saves the sheet (PNG) and a CSV with the prompts, ready for Vidlamp's MY_PROMPTS/prompts.csv.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "The pictures made from the series (for a video: its frames, the "
                                                "sheet takes the first one of each)."}),
                "prompts": ("STRING", {"forceInput": True, "tooltip": "The 'prompts' output of Prompt Series."}),
                "columns": ("INT", {"default": 4, "min": 1, "max": 12}),
                "cell_width": ("INT", {"default": 512, "min": 128, "max": 2048, "step": 16}),
                "text_lines": ("INT", {"default": 6, "min": 0, "max": 40,
                                       "tooltip": "Lines of prompt under each picture (the full text is in the CSV). "
                                                  "0 = only the number."}),
                "filename_prefix": ("STRING", {"default": "vidlamp_prompts/sheet"}),
            },
        }

    def run(self, images, prompts, columns, cell_width, text_lines, filename_prefix):
        columns, cell_width, text_lines = columns[0], cell_width[0], text_lines[0]
        prefix = filename_prefix[0]
        prompts = [str(p) for p in prompts]
        # one picture per run of the series: a batch (or a video) gives its first frame
        pictures = [_to_pil(b[0]) for b in images if b is not None and b.shape[0] > 0]
        if not pictures:
            raise ValueError("Prompt Sheet received no pictures.")
        if len(pictures) != len(prompts):
            print(f"{LOG} Prompt Sheet: {len(pictures)} pictures and {len(prompts)} prompts, pairing them in order")

        size = max(14, cell_width // 30)
        font, font_bold = _font(size), _font(int(size * 1.3))
        pad, line_h = size // 2 + 4, int(size * 1.35)
        chars = max(12, int(cell_width / (size * 0.52)))
        cells = []
        for i, pic in enumerate(pictures):
            h = round(pic.height * cell_width / pic.width)
            pic = pic.resize((cell_width, h), Image.LANCZOS)
            text = prompts[i] if i < len(prompts) else ""
            lines = textwrap.wrap(text, chars) if text_lines else []
            if len(lines) > text_lines:
                lines = lines[:text_lines]
                lines[-1] = lines[-1][: max(0, chars - 1)].rstrip() + "…"
            cells.append((pic, lines))
        img_h = max(p.height for p, _ in cells)
        text_h = pad * 2 + int(size * 1.3) + 4 + line_h * (text_lines if text_lines else 0)
        cell_h = img_h + text_h
        cols = min(columns, len(cells))
        rows = (len(cells) + cols - 1) // cols
        gap = 8
        sheet = Image.new("RGB", (cols * cell_width + (cols + 1) * gap, rows * cell_h + (rows + 1) * gap), (24, 24, 26))
        draw = ImageDraw.Draw(sheet)
        for i, (pic, lines) in enumerate(cells):
            x = gap + (i % cols) * (cell_width + gap)
            y = gap + (i // cols) * (cell_h + gap)
            sheet.paste(pic, (x, y + (img_h - pic.height) // 2))
            ty = y + img_h + pad
            draw.text((x + pad, ty), f"#{i + 1}", fill=(255, 196, 64), font=font_bold)
            ty += int(size * 1.3) + 4
            for line in lines:
                draw.text((x + pad, ty), line, fill=(230, 230, 230), font=font)
                ty += line_h

        out_dir = folder_paths.get_output_directory()
        full, name, counter, sub, _ = folder_paths.get_save_image_path(prefix, out_dir, sheet.width, sheet.height)
        base = f"{name}_{counter:05}_"
        sheet.save(os.path.join(full, base + ".png"), compress_level=4)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["category", "title", "prompt", "models"])
        for i, p in enumerate(prompts):
            w.writerow([f"Prompt series {stamp}", f"#{i + 1}", p, ""])
        with open(os.path.join(full, base + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write(buf.getvalue())
        _save_xlsx(os.path.join(full, base + ".xlsx"), pictures, prompts)
        print(f"{LOG} sheet saved: {os.path.join(full, base + '.png')} (+ .csv, .xlsx)")

        tensor = torch.from_numpy(np.asarray(sheet).astype(np.float32) / 255.0)[None,]
        # "vidlamp_files": the Excel and CSV files, so Vidlamp can save them with the creation (10/11/2026)
        files = [{"filename": base + ext, "subfolder": sub, "type": "output"} for ext in (".xlsx", ".csv")]
        return {"ui": {"images": [{"filename": base + ".png", "subfolder": sub, "type": "output"}],
                       "vidlamp_files": files},
                "result": (tensor,)}


COMPARE_SLOTS = 8


class VidlampPromptSheetCompare:
    """The same prompts made by several models, in one sheet: one row per prompt, one column per model (10/11/2026,
    the user: "the same prompt with the different installed models, then all in one sheet"). A model switched off in
    the workflow (its group muted) simply does not arrive: its column is left out."""
    CATEGORY = "Vidlamp/prompts"
    FUNCTION = "run"
    INPUT_IS_LIST = True
    OUTPUT_NODE = True
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("sheet",)
    DESCRIPTION = ("Compares models: connect the pictures of each model to image_1...image_8 and name them in "
                   "label_1...label_8. Saves the sheet (PNG, a row per prompt and a column per model), an Excel file "
                   "with the same grid and a CSV with the prompts.")

    @classmethod
    def INPUT_TYPES(cls):
        optional = {}
        for i in range(1, COMPARE_SLOTS + 1):
            optional[f"image_{i}"] = ("IMAGE", {"tooltip": f"The pictures of model {i} (one per prompt)."})
            optional[f"label_{i}"] = ("STRING", {"default": f"Model {i}", "tooltip": f"The name of model {i}."})
            optional[f"prompts_{i}"] = ("STRING", {"forceInput": True, "tooltip": f"Optional: the prompts model {i} "
                                                   f"received, when they were rewritten for it (Prompt Rewrite). "
                                                   f"They go in the Excel file and the CSV."})
        return {
            "required": {
                "prompts": ("STRING", {"forceInput": True, "tooltip": "The 'prompts' output of the Sheet Creator."}),
                "cell_width": ("INT", {"default": 448, "min": 128, "max": 2048, "step": 16}),
                "text_lines": ("INT", {"default": 3, "min": 0, "max": 20,
                                       "tooltip": "Lines of prompt under each row (the full text is in the Excel "
                                                  "file and the CSV). 0 = only the number."}),
                "filename_prefix": ("STRING", {"default": "vidlamp_prompts/compare"}),
            },
            "optional": optional,
        }

    def run(self, prompts, cell_width, text_lines, filename_prefix, **kw):
        cell_width, text_lines, prefix = cell_width[0], text_lines[0], filename_prefix[0]
        prompts = [str(p) for p in prompts]
        columns, texts = [], []
        for i in range(1, COMPARE_SLOTS + 1):
            batches = kw.get(f"image_{i}")
            if not batches:
                continue                    # model switched off or not connected
            pics = [_to_pil(b[0]) for b in batches if b is not None and b.shape[0] > 0]
            if pics:
                label = (kw.get(f"label_{i}") or [f"Model {i}"])[0]
                columns.append((str(label), pics))
                own = [str(x) for x in kw.get(f"prompts_{i}") or []]
                if own and own != prompts:            # rewritten for this model (not just passed through)
                    texts.append((f"Prompt for {label}", own))
        if not columns:
            raise ValueError("Prompt Sheet Compare received no pictures: switch on at least one model.")
        n_rows = max([len(prompts)] + [len(p) for _, p in columns])

        size = max(14, cell_width // 30)
        font, font_bold = _font(size), _font(int(size * 1.3))
        pad, line_h, gap = size // 2 + 4, int(size * 1.35), 8
        head_h = int(size * 1.3) + pad * 2
        num_w = int(size * 3.2)
        img_h = 0
        cells = []
        for _, pics in columns:
            col = []
            for pic in pics:
                h = round(pic.height * cell_width / pic.width)
                col.append(pic.resize((cell_width, h), Image.LANCZOS))
                img_h = max(img_h, h)
            cells.append(col)
        width = num_w + len(columns) * (cell_width + gap) + gap
        chars = max(20, int((width - num_w - 2 * pad) / (size * 0.52)))
        text_h = pad + line_h * text_lines if text_lines else 0
        row_h = img_h + text_h + gap
        sheet = Image.new("RGB", (width, head_h + n_rows * row_h + gap), (24, 24, 26))
        draw = ImageDraw.Draw(sheet)
        for c, (label, _) in enumerate(columns):
            draw.text((num_w + gap + c * (cell_width + gap) + pad, pad), label, fill=(255, 196, 64), font=font_bold)
        for r in range(n_rows):
            y = head_h + r * row_h
            draw.text((pad, y + pad), f"#{r + 1}", fill=(255, 196, 64), font=font_bold)
            for c, col in enumerate(cells):
                if r < len(col):
                    x = num_w + gap + c * (cell_width + gap)
                    sheet.paste(col[r], (x, y + (img_h - col[r].height) // 2))
            if text_lines and r < len(prompts):
                lines = textwrap.wrap(prompts[r], chars)
                if len(lines) > text_lines:
                    lines = lines[:text_lines]
                    lines[-1] = lines[-1][: max(0, chars - 1)].rstrip() + "…"
                ty = y + img_h + pad // 2
                for line in lines:
                    draw.text((num_w + gap, ty), line, fill=(230, 230, 230), font=font)
                    ty += line_h

        out_dir = folder_paths.get_output_directory()
        full, name, counter, sub, _ = folder_paths.get_save_image_path(prefix, out_dir, sheet.width, sheet.height)
        base = f"{name}_{counter:05}_"
        sheet.save(os.path.join(full, base + ".png"), compress_level=4)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["category", "title", "prompt", "models"])
        for i, p in enumerate(prompts):
            w.writerow([f"Model comparison {stamp}", f"#{i + 1}", p, ""])
            for label, vals in texts:            # the prompt rewritten for each model, as rows of their own
                if i < len(vals):
                    w.writerow([f"Model comparison {stamp}", f"#{i + 1} {label}", vals[i], ""])
        with open(os.path.join(full, base + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write(buf.getvalue())
        # every picture of every model saved here, next to the sheet, with a clear name (model + prompt number):
        # the Excel file lists them, clickable (10/11/2026, the user: "the path and name of every picture")
        links, saved = [], []
        for label, pics in columns:
            slug = "".join(ch if ch.isalnum() else "-" for ch in label).strip("-")
            names = []
            for r, pic in enumerate(pics):
                fname = f"{base}{slug}_{r + 1:02d}.png"
                pic.convert("RGB").save(os.path.join(full, fname), compress_level=4)
                names.append(fname)
                saved.append({"filename": fname, "subfolder": sub, "type": "output"})
            links.append((f"{label} · file", names))
        _save_xlsx_grid(os.path.join(full, base + ".xlsx"), prompts, columns, texts=texts, links=links)
        print(f"{LOG} comparison saved: {os.path.join(full, base + '.png')} (+ .csv, .xlsx, {len(saved)} pictures), "
              f"{len(columns)} models: {', '.join(l for l, _ in columns)}")

        tensor = torch.from_numpy(np.asarray(sheet).astype(np.float32) / 255.0)[None,]
        # "vidlamp_files": the Excel and CSV files, so Vidlamp can save them with the creation (10/11/2026)
        files = [{"filename": base + ext, "subfolder": sub, "type": "output"} for ext in (".xlsx", ".csv")]
        # the sheet first (the result to show), then the pictures of the models
        return {"ui": {"images": [{"filename": base + ".png", "subfolder": sub, "type": "output"}] + saved,
                       "vidlamp_files": files},
                "result": (tensor,)}


NODE_CLASS_MAPPINGS = {
    "VidlampPromptSeries": VidlampPromptSeries,
    "VidlampPromptSheet": VidlampPromptSheet,
    "VidlampPromptSheetCompare": VidlampPromptSheetCompare,
    "VidlampPromptRewrite": VidlampPromptRewrite,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "VidlampPromptSeries": "Vidlamp Prompt Sheet Creator",
    "VidlampPromptSheet": "Vidlamp Prompt Sheet",
    "VidlampPromptSheetCompare": "Vidlamp Prompt Sheet Compare",
    "VidlampPromptRewrite": "Vidlamp Prompt Rewrite",
}
