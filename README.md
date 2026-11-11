# Vidlamp Prompts

Test a whole series of prompts in one run, on one model or side by side on many.

| Node | What it does |
|---|---|
| **Vidlamp Prompt Sheet Creator** | Writes N prompts for the model you use, from a few words ("a cat chef") or, with the idea empty, from ready-made presets (Characters, Food, Landscape, Fantasy...). Its `prompts` output is a list: connect it to your text encoder and ComfyUI makes one picture per prompt. |
| **Vidlamp Prompt Rewrite** | Rewrites the prompts of the Sheet Creator for another model, keeping the same scenes: one prompt written for each model. |
| **Vidlamp Prompt Sheet** | Puts the pictures of a series side by side, each prompt under its picture. Saves the sheet (PNG), an Excel file with every picture next to its full prompt, and a CSV. |
| **Vidlamp Prompt Sheet Compare** | The same prompts made by up to 8 models in one sheet: one row per prompt, one column per model. PNG + Excel + CSV. A model switched off simply leaves its column out. |

Both sheet nodes have an **Open output folder** button.

## Engines (who writes the prompts)

| Engine | What you need | Speed |
|---|---|---|
| Presets (no AI) | nothing | instant |
| ComfyUI text model | a CLIP Loader with a text model that can write: Gemma 3/4 (the LTX text encoders), Qwen3-VL (Krea 2, Qwen-Image 2.1), Qwen3.5 | a few seconds per prompt |
| Ollama | Ollama running: its models appear in the `server_model` menu | a few seconds per prompt |
| LM Studio | LM Studio with its server started: its models appear in the menu | a few seconds per prompt |

The text model is loaded only when the engine is "ComfyUI text model". With Ollama or LM Studio the node first frees the
graphics card from ComfyUI's models and unloads the text model at the end.

Each prompt follows the writing rules of the target model (Z-Image Turbo, Krea 2, FLUX.2 Klein, FLUX 3 Klein,
Qwen-Image, Qwen-Image 2.1, Boogu-Image Turbo, ERNIE Image Turbo, MOSS-Image, SDXL, Anima, NetaYume Lumina, LTX-2.3,
LTX-2.5). The rules are shown in the `system_prompt` box when you pick a target: change them as you like, empty = the
default ones. The randomness comes from the seed: same seed and settings = same series.

## Options

- **target**: the model that will use the prompts.
- **category**: used when the idea is empty. Characters makes random people and creatures.
- **style**: the look of every prompt; Random picks a different style for each one.
- **mode**: Explore (a different scene each time) or Variations (the first scene again with new pose, light, angle...).
- **frame**: landscape, portrait or square: the scene is composed for it.
- **strange_mix**: absurd but beautiful combinations of place, weather and action.
- **trigger_words**: LoRA trigger words put at the start of every prompt.

Without a text model the idea is not translated: write it in English for best results.

## Example

`examples/z_image_prompt_series_api.json`: Z-Image Turbo, 8 prompts, one sheet at the end.

## Zoom Crop / Zoom Paste

Detection and matting models work on a fixed canvas (WeVisOne-VL: 1024x1024 for the whole picture), so small subjects
and big photos get soft edges. **Vidlamp Zoom Crop** takes the boxes of a first pass (core BOUNDING_BOX, e.g. from
WeVisOne-VL Detect), crops their area at full resolution plus a margin; run the model again on the crop, then
**Vidlamp Zoom Paste** puts the mask back on a canvas as big as the original picture. Works well with BiRefNet
(core RemoveBackground) on the same crop for sharp hair and fur.

## Dependencies

None beyond ComfyUI (the Excel file is written without extra libraries).

License: MIT. Made by stefanobeck for Vidlamp and TUTORIAL3d.it.
